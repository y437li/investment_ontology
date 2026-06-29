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
# Single source of truth lives in runs.REQUIRED_DISCOVERY_ARTIFACTS; freeze sorts
# it deterministically for hashing.
_REQUIRED_DISCOVERY_ARTIFACTS: list[str] = sorted(runs.REQUIRED_DISCOVERY_ARTIFACTS)

# Additional discovery artifacts included in hashes when present.
# OI-3: extended to cover the full pipeline artifact set (EG-B, SENT-B/C, FI-C,
# provenance, etc.).  Any file present in discovery/ that is not in the required
# list is hashed here to ensure all discovery evidence is frozen.
_OPTIONAL_DISCOVERY_ARTIFACTS: list[str] = [
    # Original optional set
    "theme_lineage.json",
    "edge_explanations.parquet",
    "entity_aliases_global.parquet",
    # EG-B: discovery-time fundamentals / metrics (io_contracts §20a-c)
    "fundamentals_asreported.parquet",
    "financial_metrics.parquet",
    "financial_metric_edges.parquet",
    # SENT-B/C: management sentiment (io_contracts §S-B, §S-C)
    "management_sentiment.parquet",
    "sentiment_edges.parquet",
    "management_sentiment_fused.parquet",
    # FI-C: forward inference projected impacts (io_contracts §FI-C)
    "projected_impacts.parquet",
    # EG-E: provenance artifacts (io_contracts §E1-E3)
    "entity_chunk_provenance.parquet",
    "theme_document_evidence.parquet",
    "company_theme_document_evidence.parquet",
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


def _collect_artifact_hashes(discovery_dir: Path,
                             key_prefix: str = "discovery") -> dict[str, str]:
    """Compute sha256 hashes for all present discovery artifacts in *discovery_dir*.

    Keys are '<key_prefix>/<filename>'.  For a flat run key_prefix is
    'discovery' (keys 'discovery/<name>', consistent with io_contracts §2 and
    tests/test_leakage_gates.py).  For a per-point run key_prefix is
    'discovery/<as_of>' (keys 'discovery/<as_of>/<name>').

    Raises FileNotFoundError if any required artifact is missing.
    """
    hashes: dict[str, str] = {}

    # Required artifacts — must exist
    for name in _REQUIRED_DISCOVERY_ARTIFACTS:
        p = discovery_dir / name
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(
                f"discovery artifact missing before freeze: {name}"
            )
        hashes[f"{key_prefix}/{name}"] = _hash_file(p)

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
            key = f"{key_prefix}/{name}"
            if key not in hashes:
                hashes[key] = _hash_file(p)

    # Return keys sorted for determinism
    return dict(sorted(hashes.items()))


def freeze_discovery(run_id: str, as_of: Optional[str] = None) -> RunManifest:
    """Freeze discovery artifacts for the given run.

    Legacy flat path (as_of is None and the run has no as_of_dates): unchanged
    behavior — hashes flat discovery/, keys 'discovery/<name>', sets run-level
    discovery_frozen=True and frozen_at.

    Per-point path (as_of set, or implied by a multi-point manifest): hashes
    discovery/<as_of>/, keys 'discovery/<as_of>/<name>', MERGES into the existing
    hash dict, records discovery_frozen_points[as_of], and flips run-level
    discovery_frozen=True once every authored point is frozen.

    Idempotent: re-freezing a point recomputes identical hashes and overwrites
    that point's keys/timestamp; other points are untouched.

    Raises:
        RuntimeError: run not found.
        ValueError: as_of is None on a multi-point run (bulk freeze is R2).
        FileNotFoundError: required discovery artifact missing.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise RuntimeError(f"run not found: {run_id}")

    # Ensure directory layout (creates validation/ dir) + legacy root migration.
    _ensure_directory_layout(run_id)
    runs._migrate_root_discovery_artifacts(run_id)

    now = _utc_now_iso()
    manifest_dict = manifest.model_dump()

    if as_of is None and not manifest.as_of_dates:
        # ---- Legacy flat freeze (unchanged behavior) ----
        discovery_dir = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR
        artifact_hashes = _collect_artifact_hashes(discovery_dir, runs.DISCOVERY_DIR)
        manifest_dict["discovery_frozen"] = True
        manifest_dict["discovery_artifact_hashes"] = artifact_hashes
        manifest_dict["frozen_at"] = now
    else:
        if as_of is None:
            raise ValueError(
                "as_of required to freeze a multi-point run (R1); bulk freeze is R2"
            )
        # ---- Per-point freeze ----
        discovery_dir = runs.discovery_point_dir(run_id, as_of, for_write=True)
        key_prefix = f"{runs.DISCOVERY_DIR}/{as_of}"
        point_hashes = _collect_artifact_hashes(discovery_dir, key_prefix)

        # Merge into existing hashes (do not clobber other points' keys).
        merged = dict(manifest_dict.get("discovery_artifact_hashes") or {})
        # Drop any stale keys for THIS point before merging (idempotent overwrite).
        stale_prefix = f"{key_prefix}/"
        merged = {k: v for k, v in merged.items() if not k.startswith(stale_prefix)}
        merged.update(point_hashes)
        manifest_dict["discovery_artifact_hashes"] = dict(sorted(merged.items()))

        frozen_points = dict(manifest_dict.get("discovery_frozen_points") or {})
        frozen_points[as_of] = now
        manifest_dict["discovery_frozen_points"] = frozen_points

        # Run-level flag: True iff every authored point is frozen.
        all_points = manifest.as_of_dates or [as_of]
        run_level = all(p in frozen_points for p in all_points)
        was_frozen = bool(manifest_dict.get("discovery_frozen"))
        manifest_dict["discovery_frozen"] = run_level
        if run_level and not was_frozen:
            manifest_dict["frozen_at"] = now

    # Write updated manifest
    run_dir = runs.get_run_dir(run_id)
    manifest_path = run_dir / runs.MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    # Return updated RunManifest (reload from disk for fidelity)
    updated = runs.load_manifest(run_id)
    assert updated is not None  # we just wrote it
    return updated
