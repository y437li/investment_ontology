"""Discovery freeze service (M5, OI-3).

Ensures:
  - discovery/ and validation/ directories are physically separated.
  - validation/ directory is created (empty) before validation reads future data.
  - All discovery artifacts are sha256-hashed.
  - run_manifest.json is updated with discovery_artifact_hashes and
    discovery_frozen=true.
  - frozen_at is recorded in the manifest.

Freeze is IDEMPOTENT: re-running recomputes hashes deterministically.
Freeze must occur BEFORE any validation reads future market or fundamental data.

Keys in discovery_artifact_hashes are of the form 'discovery/<name>' and
match the keys expected by tests/test_leakage_gates.py.

Spec references:
  - theme_discovery_engine_v1.md §16 (Leakage Prevention)
  - theme_discovery_engine_v1.md §18 (Discovery vs Validation)
  - docs/io_contracts.md §2 (Run Manifest — discovery_artifact_hashes)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import runs
from .models import RunManifest

# Discovery artifacts that MUST exist before freeze (matches leakage gate keys).
# These are the core pipeline artifacts required at minimum.
_REQUIRED_DISCOVERY_ARTIFACTS: list[str] = [
    "raw_documents.parquet",
    "documents.parquet",
    "document_cleaning_log.parquet",
    "chunks.parquet",
    "entities.parquet",
    "entity_aliases.parquet",
    "edges.parquet",
    "graph.json",
]

# Additional M4/M5 discovery artifacts included in hashes when present.
# Not required to be present (run may not have reached M4/M5 yet), but
# if present they must be hashed for integrity.
_OPTIONAL_DISCOVERY_ARTIFACTS: list[str] = [
    "communities.json",
    "theme_snapshots.json",
    "theme_lineage.json",
    "theme_metrics.parquet",
    "company_theme_exposure.parquet",
    "edge_explanations.parquet",
    "entities.parquet",        # also in required, deduped below
    "entity_aliases_global.parquet",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_file(path: Path) -> str:
    """Compute sha256 hash of file contents."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _ensure_directory_layout(run_id: str) -> None:
    """Ensure discovery/ and validation/ directories both exist.

    Physical separation of discovery/ and validation/ is the core leakage
    gate (spec §16). Both dirs must exist before freeze completes.
    """
    run_dir = runs.get_run_dir(run_id)
    (run_dir / runs.DISCOVERY_DIR).mkdir(parents=True, exist_ok=True)
    (run_dir / runs.VALIDATION_DIR).mkdir(parents=True, exist_ok=True)


def _collect_artifact_hashes(run_id: str) -> dict[str, str]:
    """Compute sha256 hashes for all present discovery artifacts.

    Keys are 'discovery/<filename>' consistent with io_contracts §2 and
    tests/test_leakage_gates.py expected keys.

    Raises FileNotFoundError if any required artifact is missing.
    """
    discovery_dir = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR
    hashes: dict[str, str] = {}

    # Required artifacts — must exist
    for name in _REQUIRED_DISCOVERY_ARTIFACTS:
        p = discovery_dir / name
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(
                f"discovery artifact missing before freeze: {name}"
            )
        key = f"{runs.DISCOVERY_DIR}/{name}"
        hashes[key] = _hash_file(p)

    # Optional artifacts — hash if present
    optional_names_deduped: list[str] = []
    seen: set[str] = set(_REQUIRED_DISCOVERY_ARTIFACTS)
    for name in _OPTIONAL_DISCOVERY_ARTIFACTS:
        if name not in seen:
            seen.add(name)
            optional_names_deduped.append(name)

    for name in optional_names_deduped:
        p = discovery_dir / name
        if p.exists() and p.is_file():
            key = f"{runs.DISCOVERY_DIR}/{name}"
            if key not in hashes:
                hashes[key] = _hash_file(p)

    # Return keys sorted for determinism
    return dict(sorted(hashes.items()))


def freeze_discovery(run_id: str) -> RunManifest:
    """Freeze all discovery artifacts for the given run.

    Steps:
      1. Load manifest (raises if run not found).
      2. Ensure discovery/ and validation/ directories exist.
      3. Migrate any root-level discovery artifacts to discovery/ (legacy compat).
      4. Compute sha256 hash of every discovery artifact.
      5. Update manifest: discovery_artifact_hashes, discovery_frozen=true,
         frozen_at timestamp.
      6. Write updated manifest to disk.
      7. Return updated RunManifest.

    Idempotent: re-running recomputes hashes from current file contents and
    rewrites the manifest. Hash values are deterministic for unchanged files.

    Raises:
        RuntimeError: run not found.
        FileNotFoundError: required discovery artifact missing.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise RuntimeError(f"run not found: {run_id}")

    # Step 2: ensure directory layout (creates validation/ dir)
    _ensure_directory_layout(run_id)

    # Step 3: migrate any legacy root-level artifacts
    runs._migrate_root_discovery_artifacts(run_id)

    # Step 4: compute artifact hashes
    artifact_hashes = _collect_artifact_hashes(run_id)

    # Step 5: build updated manifest dict
    manifest_dict = manifest.model_dump()
    manifest_dict["discovery_frozen"] = True
    manifest_dict["discovery_artifact_hashes"] = artifact_hashes
    manifest_dict["frozen_at"] = _utc_now_iso()

    # Step 6: write updated manifest
    run_dir = runs.get_run_dir(run_id)
    manifest_path = run_dir / runs.MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    # Step 7: return updated RunManifest (reload from disk for fidelity)
    updated = runs.load_manifest(run_id)
    assert updated is not None  # we just wrote it
    return updated
