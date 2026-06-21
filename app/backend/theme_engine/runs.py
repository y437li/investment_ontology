"""File-backed run service.

A run is a directory under `RUN_OUTPUT_DIR/<run_id>/` whose source of truth is
`run_manifest.json`. This is the Milestone 1 slice: create a run and read its
status. Later milestones write further artifacts into the same directory.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, settings
from .models import RunCreateRequest, RunManifest, RunStatus

MANIFEST_NAME = "run_manifest.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _code_version() -> str:
    """Short git SHA, or 'unknown' outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
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
    (run_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(), indent=2), encoding="utf-8"
    )
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
        p.name for p in run_dir.iterdir()
        if p.is_file() and p.name != MANIFEST_NAME
    )
    return RunStatus(
        run_id=manifest.run_id,
        as_of_date=manifest.as_of_date,
        created_at=manifest.created_at,
        discovery_frozen=manifest.discovery_frozen,
        artifacts_present=artifacts,
    )
