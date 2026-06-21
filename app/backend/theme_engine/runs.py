"""File-backed run service.

A run is a directory under `RUN_OUTPUT_DIR/<run_id>/` whose source of truth is
`run_manifest.json`. This is the Milestone 1 slice: create a run and read its
status. Later milestones write further artifacts into the same directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import PurePosixPath
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


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _model_config_hash(model_config: dict[str, object] | None) -> str | None:
    if not model_config:
        return None
    return f"sha256:{hashlib.sha256(_stable_json(model_config).encode('utf-8')).hexdigest()}"


def _input_hash(
    config_paths: list[str],
    model_config: dict[str, object] | None,
) -> str:
    h = hashlib.sha256()
    for rel in config_paths:
        h.update(rel.encode("utf-8"))
        p = REPO_ROOT / rel
        h.update(p.read_bytes() if p.exists() else b"")

    if model_config is not None:
        h.update(b"::model_config::")
        h.update(_stable_json(model_config).encode("utf-8"))
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
    expected = manifest.discovery_artifact_hashes or {}
    actual = _compute_required_discovery_hashes(run_id)

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

    extra = sorted(set(expected) - set(actual))
    if extra:
        raise ValueError(
            f"frozen discovery manifest has unexpected hash entries: {extra}"
        )


def create_run(req: RunCreateRequest) -> RunManifest:
    universe = req.universe_config or settings.universe_config
    pipeline = req.pipeline_config or settings.pipeline_config
    validation = req.validation_config or settings.validation_config
    model_config = req.model_config

    run_id, run_dir = _allocate_run_dir()
    manifest = RunManifest(
        run_id=run_id,
        as_of_date=req.as_of_date,
        universe_config=universe,
        pipeline_config=pipeline,
        validation_config=validation,
        model_config=model_config,
        model_config_hash=_model_config_hash(model_config),
        created_at=_utc_now_iso(),
        code_version=_code_version(),
        input_hash=_input_hash([universe, pipeline, validation], model_config),
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
    validation_dir = _validation_dir(run_id)
    artifacts = sorted(
        p.relative_to(run_dir).as_posix()
        for p in run_dir.rglob("*")
        if p.is_file() and p.relative_to(run_dir).as_posix() != MANIFEST_NAME
    )
    validation_artifacts = (
        sorted(
            p.relative_to(run_dir).as_posix()
            for p in validation_dir.rglob("*")
            if p.is_file()
        )
        if validation_dir.exists()
        else []
    )
    validation_status = _read_validation_status(validation_dir / "validation.csv")
    return RunStatus(
        run_id=manifest.run_id,
        as_of_date=manifest.as_of_date,
        created_at=manifest.created_at,
        discovery_frozen=manifest.discovery_frozen,
        artifacts_present=artifacts,
        validation_status=validation_status,
        validation_artifacts=validation_artifacts,
    )


def _read_validation_status(report_csv: Path) -> str | None:
    if not report_csv.exists() or not report_csv.is_file():
        return None
    try:
        with report_csv.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            row = next(reader, None)
    except Exception:
        return None

    if not row:
        return None
    status = (row.get("validation_status") or "").strip()
    return status or None


def resolve_artifact_path(run_id: str, artifact_name: str) -> Path | None:
    """Resolve a run artifact path and reject traversal attacks.

    `artifact_name` may contain slashes, e.g. `discovery/raw_documents.parquet`
    or `validation/validation.csv`.
    """
    run_dir = get_run_dir(run_id)
    manifest = load_manifest(run_id)
    if manifest is None:
        return None

    if not artifact_name:
        return None
    if artifact_name.startswith(("/", "\\")):
        return None

    try:
        artifact_posix = PurePosixPath(artifact_name)
    except Exception:
        return None

    if ".." in artifact_posix.parts:
        return None

    target = run_dir.joinpath(*artifact_posix.parts)
    try:
        resolved = target.resolve(strict=False)
        run_root = run_dir.resolve()
    except Exception:
        return None

    if not resolved.as_posix().startswith(run_root.as_posix() + "/"):
        return None

    if not target.exists() or not target.is_file():
        return None

    return target
