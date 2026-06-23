"""File-backed run service.

A run is a directory under `RUN_OUTPUT_DIR/<run_id>/` whose source of truth is
`run_manifest.json`. This is the Milestone 1 slice: create a run and read its
status. Later milestones write further artifacts into the same directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, settings
from .models import RunCreateRequest, RunManifest, RunStatus

MANIFEST_NAME = "run_manifest.json"
DISCOVERY_DIR = "discovery"
VALIDATION_DIR = "validation"
REQUIRED_DISCOVERY_ARTIFACTS = {
    "raw_documents.parquet",
    "documents.parquet",
    "document_cleaning_log.parquet",
    "chunks.parquet",
    "entities.parquet",
    "entity_aliases.parquet",
    "edges.parquet",
    "graph.json",
    # Validation consumes these — they MUST be frozen+hashed, else a post-freeze
    # regeneration would be silently accepted (audit CRITICAL).
    "communities.json",
    "theme_snapshots.json",
    "theme_metrics.parquet",
    "company_theme_exposure.parquet",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _code_version() -> str:
    """Short git SHA, or 'unknown' outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _input_hash(config_paths: list[str]) -> str:
    """Deterministic hash of the referenced config files' contents.

    Missing files hash as the empty string so a run is still reproducible and
    the hash changes if a config later appears.
    """
    h = hashlib.sha256()
    for rel in config_paths:
        h.update(rel.encode("utf-8"))
        p = REPO_ROOT / rel
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:16]


def _allocate_run_dir() -> tuple[str, Path]:
    """Pick a unique run id of the spec form run_YYYYMMDD_HHMMSS."""
    base = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    run_id, suffix = base, 1
    while (settings.run_output_dir / run_id).exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
    run_dir = settings.run_output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir


def get_run_dir(run_id: str) -> Path:
    return settings.run_output_dir / run_id


def list_runs() -> list[dict]:
    """Summaries of all runs on disk, newest first."""
    base = settings.run_output_dir
    if not base.exists():
        return []
    summaries: list[dict] = []
    for d in base.iterdir():
        manifest_path = d / MANIFEST_NAME
        if not d.is_dir() or not manifest_path.exists():
            continue
        try:
            man = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        summaries.append({
            "run_id": man.get("run_id", d.name),
            "as_of_date": man.get("as_of_date"),
            "created_at": man.get("created_at"),
            "discovery_frozen": man.get("discovery_frozen", False),
        })
    summaries.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return summaries


def _discovery_dir(run_id: str) -> Path:
    return get_run_dir(run_id) / DISCOVERY_DIR


def _validation_dir(run_id: str) -> Path:
    return get_run_dir(run_id) / VALIDATION_DIR


def _discovery_path(run_id: str, name: str) -> Path:
    return _discovery_dir(run_id) / name


def _ensure_run_layout(run_id: str) -> None:
    _discovery_dir(run_id).mkdir(parents=True, exist_ok=True)
    _validation_dir(run_id).mkdir(parents=True, exist_ok=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _migrate_root_discovery_artifacts(run_id: str) -> None:
    run_dir = get_run_dir(run_id)
    discovery_dir = _discovery_dir(run_id)

    # Compatibility with earlier milestones that dropped artifacts under
    # the run root.
    for name in REQUIRED_DISCOVERY_ARTIFACTS:
        legacy = run_dir / name
        if not legacy.exists():
            continue
        target = discovery_dir / name
        if target.exists():
            continue
        shutil.copy2(legacy, target)


def _compute_required_discovery_hashes(run_id: str) -> dict[str, str]:
    run_discovery_dir = _discovery_dir(run_id)
    hashes: dict[str, str] = {}

    for name in sorted(REQUIRED_DISCOVERY_ARTIFACTS):
        file_path = run_discovery_dir / name
        if not file_path.exists():
            raise FileNotFoundError(f"discovery artifact missing before freeze: {name}")
        if not file_path.is_file():
            raise FileNotFoundError(f"discovery artifact missing before freeze: {name}")
        hashes[f"{DISCOVERY_DIR}/{name}"] = _hash_file(file_path)

    return hashes


def _ensure_discovery_hashes_match(manifest: RunManifest, run_id: str) -> None:
    """Verify that all required discovery artifact hashes in the manifest match
    the current file contents.

    Optional M4/M5 artifacts (communities.json, company_theme_exposure.parquet,
    etc.) may also appear in discovery_artifact_hashes and are verified if
    present on disk. Extra hash entries for optional artifacts are allowed.
    """
    expected = manifest.discovery_artifact_hashes or {}
    actual = _compute_required_discovery_hashes(run_id)

    # 1. All required artifacts must have a matching hash in the manifest.
    for rel in sorted(REQUIRED_DISCOVERY_ARTIFACTS):
        prefixed = f"{DISCOVERY_DIR}/{rel}"
        if prefixed not in actual:
            raise FileNotFoundError(f"missing frozen discovery artifact: {prefixed}")
        if prefixed not in expected:
            raise PermissionError(f"frozen discovery manifest missing hash: {prefixed}")

        digest = expected[prefixed]
        if actual[prefixed] != digest:
            raise ValueError(
                f"frozen discovery artifact hash mismatch: {prefixed}"
            )

    # 2. Optional artifacts present in the manifest must match if the file exists.
    #    Extra hash entries for artifacts that do NOT exist on disk are ignored
    #    (they may be optional M4/M5 artifacts not yet produced on a legacy run).
    discovery_dir = _discovery_dir(run_id)
    for key, digest in expected.items():
        if key in actual:
            # Already checked above for required; double-check optional ones.
            if actual[key] != digest:
                raise ValueError(f"frozen discovery artifact hash mismatch: {key}")
        else:
            # Key not in required set — check if file exists on disk
            name = key.split("/", 1)[-1]  # strip 'discovery/' prefix
            p = discovery_dir / name
            if p.exists() and p.is_file():
                file_hash = _hash_file(p)
                if file_hash != digest:
                    raise ValueError(f"frozen discovery artifact hash mismatch: {key}")


def create_run(req: RunCreateRequest) -> RunManifest:
    universe = req.universe_config or settings.universe_config
    pipeline = req.pipeline_config or settings.pipeline_config
    validation = req.validation_config or settings.validation_config

    run_id, run_dir = _allocate_run_dir()
    manifest = RunManifest(
        run_id=run_id,
        as_of_date=req.as_of_date,
        universe_config=universe,
        pipeline_config=pipeline,
        validation_config=validation,
        created_at=_utc_now_iso(),
        code_version=_code_version(),
        input_hash=_input_hash([universe, pipeline, validation]),
        discovery_frozen=False,
        sweep_parent_id=req.sweep_parent_id,
    )
    _ensure_run_layout(run_id)
    (run_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
    return manifest


def freeze_discovery(run_id: str) -> RunManifest:
    manifest = load_manifest(run_id)
    if manifest is None:
        raise RuntimeError(f"run not found: {run_id}")

    _ensure_run_layout(run_id)
    _migrate_root_discovery_artifacts(run_id)
    discovered_hashes = _compute_required_discovery_hashes(run_id)

    manifest = manifest.model_copy(
        update={
            "discovery_frozen": True,
            "discovery_artifact_hashes": discovered_hashes,
        }
    )

    run_dir = get_run_dir(run_id)
    (run_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
    return manifest


def validate_ready_for_validation(run_id: str) -> RunManifest:
    manifest = load_manifest(run_id)
    if manifest is None:
        raise RuntimeError(f"run not found: {run_id}")

    if not manifest.discovery_frozen:
        raise PermissionError("discovery not frozen")

    if not manifest.discovery_artifact_hashes:
        raise PermissionError("missing discovery artifact hashes in manifest")

    _ensure_discovery_hashes_match(manifest, run_id)
    return manifest


def load_manifest(run_id: str) -> RunManifest | None:
    p = settings.run_output_dir / run_id / MANIFEST_NAME
    if not p.exists():
        return None
    return RunManifest.model_validate_json(p.read_text(encoding="utf-8"))


def get_status(run_id: str) -> RunStatus | None:
    manifest = load_manifest(run_id)
    if manifest is None:
        return None
    run_dir = settings.run_output_dir / run_id
    artifacts = sorted(
        p.relative_to(run_dir).as_posix()
        for p in run_dir.rglob("*")
        if p.is_file() and p.relative_to(run_dir).as_posix() != MANIFEST_NAME
    )
    return RunStatus(
        run_id=manifest.run_id,
        as_of_date=manifest.as_of_date,
        created_at=manifest.created_at,
        discovery_frozen=manifest.discovery_frozen,
        artifacts_present=artifacts,
    )
