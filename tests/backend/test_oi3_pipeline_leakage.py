"""OI-3: Pipeline-wide leakage gates — end-to-end tests.

Deliverables tested:
  (a) PIT INTEGRITY — every row with a date in EVERY dated discovery artifact
      has date <= run.as_of_date.  A single systematic gate, proven non-
      tautological by injecting a future-dated row and asserting the gate fails.

  (b) FREEZE IMMUTABILITY — after freeze, tampering with a discovery artifact
      is detected by the hash check.  Covers the full current discovery artifact
      set.

  (c) DISCOVERY/VALIDATION ISOLATION:
      (c1) Pre-freeze: reading a validation-only artifact raises LeakageError;
           reading a discovery artifact does NOT raise.
      (c2) Post-freeze: reading a validation-only artifact is permitted.
      (c3) Source-scan: no discovery-stage module imports/reads validation/ paths.

All tests are hermetic; conftest.py redirects RUN_OUTPUT_DIR to a temp dir.

Non-tautological proof strategy:
  - For each gate a "good" fixture passes, then a "bad" fixture (injected
    violation) fails the same assertion.  If a gate silently passed on the
    bad fixture, it would be tautological.
"""

from __future__ import annotations

import ast
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine import run_cache, runs
from theme_engine.config import settings
from theme_engine.leakage import (
    DISCOVERY_ARTIFACTS,
    DISCOVERY_DATED_COLUMNS,
    VALIDATION_ONLY_ARTIFACTS,
    LeakageError,
    assert_read_allowed,
    is_validation_path,
)
from theme_engine.models import RunManifest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AS_OF = "2024-06-30"
FUTURE_DATE = "2025-01-15"   # strictly > AS_OF; triggers PIT violations
PAST_DATE = "2024-01-01"     # strictly < AS_OF; always PIT-clean


# ---------------------------------------------------------------------------
# Helpers — run/directory construction
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run_dir(as_of_date: str = AS_OF, frozen: bool = False) -> tuple[str, Path]:
    """Create a minimal run directory and return (run_id, run_dir).

    Does NOT require the full pipeline; just creates the manifest + dirs.
    """
    run_id = f"oi3_test_{uuid.uuid4().hex[:10]}"
    run_dir = settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    validation = run_dir / "validation"
    discovery.mkdir(parents=True, exist_ok=True)
    validation.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "as_of_date": as_of_date,
        "created_at": _utcnow(),
        "code_version": "oi3-test",
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "input_hash": "test",
        "discovery_frozen": frozen,
        "discovery_artifact_hashes": None,
        "sweep_parent_id": None,
        "frozen_at": None,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_id, run_dir


def _parquet(path: Path, rows: list[dict], schema: Optional[pa.Schema] = None) -> None:
    """Write *rows* to a Parquet file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        if schema is not None:
            empty = {f.name: pa.array([], type=f.type) for f in schema}
            pq.write_table(pa.table(empty, schema=schema), path)
        else:
            pq.write_table(pa.table({}), path)
        return
    # Build column arrays preserving Python types
    names = list(rows[0].keys())
    arrays: dict[str, pa.Array] = {}
    for name in names:
        values = [r.get(name) for r in rows]
        if schema is not None:
            field = schema.field(name) if name in schema.names else None
            if field is not None:
                arrays[name] = pa.array(values, type=field.type)
                continue
        # Auto-detect type
        non_null = [v for v in values if v is not None]
        if non_null and isinstance(non_null[0], bool):
            arrays[name] = pa.array(values, type=pa.bool_())
        elif non_null and isinstance(non_null[0], float):
            arrays[name] = pa.array(values, type=pa.float64())
        elif non_null and isinstance(non_null[0], int):
            arrays[name] = pa.array(values, type=pa.int64())
        else:
            arrays[name] = pa.array(
                [str(v) if v is not None else None for v in values], type=pa.string()
            )
    pq.write_table(pa.table(arrays), path)


def _json_file(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


# ---------------------------------------------------------------------------
# Helpers — full discovery artifact fixture
# ---------------------------------------------------------------------------

# Required artifacts for freeze (matches freeze.py _REQUIRED_DISCOVERY_ARTIFACTS).
_FREEZE_REQUIRED = [
    "raw_documents.parquet",
    "documents.parquet",
    "document_cleaning_log.parquet",
    "chunks.parquet",
    "entities.parquet",
    "entity_aliases.parquet",
    "edges.parquet",
    "graph.json",
    "communities.json",
    "theme_snapshots.json",
    "theme_metrics.parquet",
    "company_theme_exposure.parquet",
]

# Full set including optional/extended artifacts tracked by OI-3.
_ALL_DISCOVERY_ARTIFACTS = _FREEZE_REQUIRED + [
    "fundamentals_asreported.parquet",
    "financial_metrics.parquet",
    "financial_metric_edges.parquet",
    "management_sentiment.parquet",
    "management_sentiment_fused.parquet",
    "projected_impacts.parquet",
    "entity_chunk_provenance.parquet",
]


def _seed_required_discovery_artifacts(discovery_dir: Path, run_id: str, as_of: str) -> None:
    """Seed all required freeze artifacts with PIT-clean content.

    Uses stable bytes-based stubs for JSON, and minimal Parquet rows with
    valid date columns set to PAST_DATE (always <= AS_OF).
    """
    # ── Parquet stubs with required date columns ──────────────────────────────

    # raw_documents + documents: available_at column
    for name in ("raw_documents.parquet", "documents.parquet"):
        _parquet(discovery_dir / name, [
            {"schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
             "available_at": PAST_DATE, "doc_id": "doc_001"},
        ])

    # document_cleaning_log: no mandatory PIT date (created_at is an audit field)
    _parquet(discovery_dir / "document_cleaning_log.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "cleaning_step": "html_clean",
         "action_type": "pass", "rule_id": "r1", "status": "ok"},
    ])

    # chunks: available_at
    _parquet(discovery_dir / "chunks.parquet", [
        {"schema_version": "1.0", "chunk_id": "chunk_001", "document_id": "doc_001",
         "raw_document_id": "raw_001", "run_id": run_id, "available_at": PAST_DATE,
         "text": "Test text.", "block_type": "prose"},
    ])

    # entities: first_seen_at
    _parquet(discovery_dir / "entities.parquet", [
        {"schema_version": "1.0", "entity_id": "ent_co_001", "entity_type": "Company",
         "name": "TestCo", "canonical_name": "TestCo", "first_seen_at": PAST_DATE,
         "confidence": 0.9, "extraction_method": "rule_based", "review_status": "pending"},
    ])

    # entity_aliases: as_of_date column
    _parquet(discovery_dir / "entity_aliases.parquet", [
        {"schema_version": "1.0", "entity_id": "ent_co_001", "alias": "TC",
         "alias_scope": "point_in_time", "as_of_date": as_of},
    ])

    # edges: first_seen_at
    _parquet(discovery_dir / "edges.parquet", [
        {"schema_version": "1.0", "edge_id": "edge_001", "run_id": run_id,
         "source_entity_id": "ent_co_001", "target_entity_id": "ent_concept_001",
         "edge_type": "exposed_to", "confidence": 0.85,
         "first_seen_at": PAST_DATE, "last_seen_at": PAST_DATE,
         "as_of_date": as_of, "extraction_method": "document_stated"},
    ])

    # graph.json
    _json_file(discovery_dir / "graph.json", {
        "schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
        "nodes": [{"entity_id": "ent_co_001", "entity_type": "Company", "label": "TestCo"}],
        "edges": [],
    })

    # communities.json
    _json_file(discovery_dir / "communities.json", {
        "schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
        "communities": [{"community_id": "comm_001", "theme_name": "Test Theme"}],
    })

    # theme_snapshots.json
    _json_file(discovery_dir / "theme_snapshots.json", {
        "schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
        "snapshots": [{"theme_snapshot_id": "snap_001", "community_id": "comm_001",
                       "theme_name": "Test Theme", "state": "Emerging"}],
    })

    # theme_metrics.parquet
    _parquet(discovery_dir / "theme_metrics.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "community_id": "comm_001",
         "as_of_date": as_of, "node_count": 1, "edge_count": 0},
    ])

    # company_theme_exposure.parquet
    _parquet(discovery_dir / "company_theme_exposure.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
         "company_id": "ent_co_001", "theme_snapshot_id": "snap_001",
         "community_id": "comm_001", "exposure_score": 0.5,
         "edge_confidence_sum": 0.85, "evidence_count": 1,
         "calculation_method": "exposure_v1_document_stated"},
    ])


def _seed_optional_discovery_artifacts(discovery_dir: Path, run_id: str, as_of: str) -> None:
    """Seed the extended set of discovery artifacts (beyond freeze-required)."""

    # fundamentals_asreported: available_at
    _parquet(discovery_dir / "fundamentals_asreported.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "company_id": "ent_co_001",
         "period_end": "2023-12-31", "metric_name": "revenue",
         "value_numeric": 1000.0, "filing_date": PAST_DATE,
         "available_at": PAST_DATE},
    ])

    # financial_metrics: available_at
    _parquet(discovery_dir / "financial_metrics.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "company_id": "ent_co_001",
         "period_end": "2023-12-31", "metric_name": "revenue",
         "value_numeric": 1000.0, "as_of_date": as_of,
         "available_at": PAST_DATE},
    ])

    # financial_metric_edges: no dedicated PIT date (derived from edges)
    _parquet(discovery_dir / "financial_metric_edges.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "edge_id": "fme_001",
         "company_id": "ent_co_001", "metric_id": "fm_001",
         "edge_type": "has_financial_metric"},
    ])

    # management_sentiment: no dedicated PIT date column (PIT via evidence_chunk_id)
    _parquet(discovery_dir / "management_sentiment.parquet", [
        {"schema_version": "1.0", "sentiment_id": "sent_001",
         "company_id": "ent_co_001", "speaker_role": "management",
         "direction": "positive", "evidence_chunk_id": "chunk_001",
         "confidence": 0.8, "created_at": _utcnow()},
    ])

    # management_sentiment_fused: available_at
    _parquet(discovery_dir / "management_sentiment_fused.parquet", [
        {"schema_version": "1.0", "fusion_id": "fuse_001",
         "sentiment_id": "sent_001", "company_id": "ent_co_001",
         "evidence_chunk_id": "chunk_001", "available_at": PAST_DATE,
         "fused_tone": "positive", "agreement": "agree",
         "created_at": _utcnow()},
    ])

    # projected_impacts: as_of_date (run-level date, not a row-level PIT date)
    _parquet(discovery_dir / "projected_impacts.parquet", [
        {"schema_version": "1.0", "run_id": run_id, "as_of_date": as_of,
         "trigger_id": "ent_evt_001", "trigger_kind": "Event",
         "company_id": "ent_co_001", "direction": 1, "strength": 0.7,
         "confidence": 0.75, "method": "propagation_v1_event_trigger"},
    ])

    # entity_chunk_provenance: available_at
    _parquet(discovery_dir / "entity_chunk_provenance.parquet", [
        {"schema_version": "1.0", "entity_id": "ent_co_001",
         "chunk_id": "chunk_001", "document_id": "doc_001",
         "company_id": "ent_co_001", "available_at": PAST_DATE},
    ])


def _make_full_discovery_run(as_of: str = AS_OF) -> tuple[str, Path]:
    """Create a run with the FULL set of current discovery artifacts.

    All dates are PIT-clean (available_at/first_seen_at <= as_of).
    Returns (run_id, run_dir).
    """
    run_id, run_dir = _make_run_dir(as_of)
    discovery = run_dir / "discovery"
    _seed_required_discovery_artifacts(discovery, run_id, as_of)
    _seed_optional_discovery_artifacts(discovery, run_id, as_of)
    return run_id, run_dir


def _freeze_run(run_id: str) -> dict[str, str]:
    """Call freeze.freeze_discovery and return the artifact hashes.

    Requires all freeze-required artifacts to already be present.
    """
    from theme_engine import freeze as freeze_mod
    manifest = freeze_mod.freeze_discovery(run_id)
    return manifest.discovery_artifact_hashes or {}


# ===========================================================================
# (a) PIT INTEGRITY GATE
# ===========================================================================
#
# Systematically scans ALL dated discovery artifacts and asserts every row's
# PIT date is <= run.as_of_date.
# Proven non-tautological: inject a future row → gate fails.
# ===========================================================================


def _check_pit_integrity(run_dir: Path, as_of: str) -> list[str]:
    """Return a list of PIT violations across all dated discovery artifacts.

    A violation is a string describing the artifact + column + value that
    exceeds as_of_date.  An empty list means no violations.
    """
    violations: list[str] = []
    discovery = run_dir / "discovery"

    for artifact_name, date_col in DISCOVERY_DATED_COLUMNS.items():
        artifact_path = discovery / artifact_name
        if not artifact_path.exists():
            continue  # optional artifact absent — skip

        try:
            rows = pq.read_table(artifact_path).to_pylist()
        except Exception as exc:
            violations.append(f"{artifact_name}: failed to read ({exc})")
            continue

        for i, row in enumerate(rows):
            raw_date = row.get(date_col)
            if raw_date is None or raw_date == "":
                continue  # null/empty dates are exempt (e.g. SENT-C absent SENT-A)
            date_str = str(raw_date)[:10]  # normalise to YYYY-MM-DD
            if date_str > as_of:
                violations.append(
                    f"{artifact_name} row[{i}] {date_col}={date_str!r} > as_of={as_of!r}"
                )

    return violations


class TestPitIntegrity:
    """Gate (a): every date in every dated discovery artifact is <= as_of."""

    def test_pit_gate_passes_on_clean_run(self):
        """All artifacts with PAST_DATE pass the PIT gate."""
        _, run_dir = _make_full_discovery_run(AS_OF)
        violations = _check_pit_integrity(run_dir, AS_OF)
        assert violations == [], (
            f"Unexpected PIT violations on clean fixture:\n" +
            "\n".join(violations)
        )

    def test_pit_gate_fails_on_future_chunks(self):
        """Injecting a future available_at into chunks.parquet makes the gate fail.

        Non-tautological proof: the same gate passes above (clean) but FAILS
        when a future-dated row is present.
        """
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        # Append one future-dated row to chunks.parquet
        existing = pq.read_table(discovery / "chunks.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "chunk_id": "chunk_future",
            "document_id": "doc_001", "raw_document_id": "raw_001",
            "run_id": run_id, "available_at": FUTURE_DATE,
            "text": "FUTURE TEXT", "block_type": "prose",
        })
        _parquet(discovery / "chunks.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0, (
            "PIT gate should FAIL when a future-dated chunk row is present "
            "(non-tautological check)"
        )
        assert any("chunks.parquet" in v for v in violations)
        assert any(FUTURE_DATE in v for v in violations)

    def test_pit_gate_fails_on_future_entity(self):
        """Injecting a future first_seen_at into entities.parquet makes the gate fail."""
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        existing = pq.read_table(discovery / "entities.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "entity_id": "ent_future_001",
            "entity_type": "Company", "name": "FutureCo",
            "canonical_name": "FutureCo", "first_seen_at": FUTURE_DATE,
            "confidence": 0.9, "extraction_method": "rule_based",
            "review_status": "pending",
        })
        _parquet(discovery / "entities.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0
        assert any("entities.parquet" in v for v in violations)

    def test_pit_gate_fails_on_future_edge(self):
        """Injecting a future first_seen_at into edges.parquet makes the gate fail."""
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        existing = pq.read_table(discovery / "edges.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "edge_id": "edge_future_001",
            "run_id": run_id,
            "source_entity_id": "ent_co_001", "target_entity_id": "ent_concept_001",
            "edge_type": "exposed_to", "confidence": 0.9,
            "first_seen_at": FUTURE_DATE, "last_seen_at": FUTURE_DATE,
            "as_of_date": AS_OF, "extraction_method": "document_stated",
        })
        _parquet(discovery / "edges.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0
        assert any("edges.parquet" in v for v in violations)

    def test_pit_gate_fails_on_future_provenance(self):
        """Injecting a future available_at into entity_chunk_provenance makes the gate fail."""
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        existing = pq.read_table(discovery / "entity_chunk_provenance.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "entity_id": "ent_co_001",
            "chunk_id": "chunk_future_prov", "document_id": "doc_001",
            "company_id": "ent_co_001", "available_at": FUTURE_DATE,
        })
        _parquet(discovery / "entity_chunk_provenance.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0
        assert any("entity_chunk_provenance.parquet" in v for v in violations)

    def test_pit_gate_fails_on_future_fundamentals(self):
        """Injecting a future available_at into fundamentals_asreported makes the gate fail."""
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        existing = pq.read_table(discovery / "fundamentals_asreported.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "run_id": run_id,
            "company_id": "ent_co_001", "period_end": "2024-12-31",
            "metric_name": "revenue", "value_numeric": 9999.0,
            "filing_date": FUTURE_DATE, "available_at": FUTURE_DATE,
        })
        _parquet(discovery / "fundamentals_asreported.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0
        assert any("fundamentals_asreported.parquet" in v for v in violations)

    def test_pit_gate_fails_on_future_sentiment_fused(self):
        """Injecting a future available_at into management_sentiment_fused makes the gate fail."""
        run_id, run_dir = _make_full_discovery_run(AS_OF)
        discovery = run_dir / "discovery"

        existing = pq.read_table(discovery / "management_sentiment_fused.parquet").to_pylist()
        existing.append({
            "schema_version": "1.0", "fusion_id": "fuse_future",
            "sentiment_id": "sent_001", "company_id": "ent_co_001",
            "evidence_chunk_id": "chunk_future", "available_at": FUTURE_DATE,
            "fused_tone": "negative", "agreement": "conflict",
            "created_at": _utcnow(),
        })
        _parquet(discovery / "management_sentiment_fused.parquet", existing)

        violations = _check_pit_integrity(run_dir, AS_OF)
        assert len(violations) > 0
        assert any("management_sentiment_fused.parquet" in v for v in violations)

    def test_pit_gate_covers_all_registered_artifacts(self):
        """DISCOVERY_DATED_COLUMNS covers the expected set of discovery artifacts.

        This is a structural check: if a new dated artifact is added to the
        pipeline, it must also be registered in leakage.DISCOVERY_DATED_COLUMNS.
        """
        # Every artifact in DISCOVERY_DATED_COLUMNS must be in DISCOVERY_ARTIFACTS.
        for name in DISCOVERY_DATED_COLUMNS:
            assert name in DISCOVERY_ARTIFACTS, (
                f"{name!r} is in DISCOVERY_DATED_COLUMNS but not DISCOVERY_ARTIFACTS"
            )

        # The known dated artifacts we expect to be registered.
        expected_dated = {
            "chunks.parquet",
            "entities.parquet",
            "edges.parquet",
            "entity_chunk_provenance.parquet",
            "fundamentals_asreported.parquet",
            "management_sentiment_fused.parquet",
        }
        missing = expected_dated - set(DISCOVERY_DATED_COLUMNS)
        assert not missing, (
            f"Dated discovery artifacts missing from DISCOVERY_DATED_COLUMNS: {missing}"
        )


# ===========================================================================
# (b) FREEZE IMMUTABILITY GATE
# ===========================================================================
#
# After freeze, tampering with a discovery artifact changes its hash.
# Covers the full current discovery artifact set (required + optional).
# Non-tautological: the tampered hash is verified to differ from the frozen one.
# ===========================================================================


class TestFreezeImmutability:
    """Gate (b): tampering with a frozen discovery artifact is detectable."""

    def test_freeze_hashes_all_required_artifacts(self):
        """freeze_discovery writes hashes for all required discovery artifacts."""
        run_id, run_dir = _make_full_discovery_run()
        hashes = _freeze_run(run_id)

        for name in _FREEZE_REQUIRED:
            key = f"discovery/{name}"
            assert key in hashes, f"missing hash for {key!r}"
            assert hashes[key].startswith("sha256:"), (
                f"hash for {key!r} does not start with 'sha256:': {hashes[key]!r}"
            )

    def test_freeze_hashes_optional_oi3_artifacts(self):
        """freeze_discovery includes hashes for the extended OI-3 artifact set when present.

        Optional artifacts (fundamentals_asreported, financial_metrics, management_sentiment_fused,
        projected_impacts, entity_chunk_provenance) that are present on disk must be hashed.
        """
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)

        # Reload manifest
        manifest = runs.load_manifest(run_id)
        assert manifest is not None
        hashes = manifest.discovery_artifact_hashes or {}

        optional_expected = [
            "fundamentals_asreported.parquet",
            "financial_metrics.parquet",
            "management_sentiment_fused.parquet",
            "projected_impacts.parquet",
            "entity_chunk_provenance.parquet",
        ]
        # These are optional — if on disk they SHOULD be in hashes (freeze.py
        # hashes all present files under discovery/).
        discovery_dir = run_dir / "discovery"
        for name in optional_expected:
            if (discovery_dir / name).exists():
                key = f"discovery/{name}"
                assert key in hashes, (
                    f"Optional artifact {name!r} is on disk but missing from frozen hashes"
                )

    def test_tamper_with_required_artifact_changes_hash(self):
        """After freeze, tampering with a required artifact produces a different hash.

        Non-tautological: we compare hash_before != hash_after.
        """
        run_id, run_dir = _make_full_discovery_run()
        hashes_before = _freeze_run(run_id)
        original_hash = hashes_before["discovery/edges.parquet"]

        # Tamper: append bytes to edges.parquet
        target = run_dir / "discovery" / "edges.parquet"
        original_bytes = target.read_bytes()
        target.write_bytes(original_bytes + b"\x00TAMPERED")

        # Re-freeze to compute new hashes (freeze is idempotent but re-hashes)
        from theme_engine import freeze as freeze_mod
        manifest_after = freeze_mod.freeze_discovery(run_id)
        hashes_after = manifest_after.discovery_artifact_hashes or {}
        tampered_hash = hashes_after["discovery/edges.parquet"]

        assert original_hash != tampered_hash, (
            "Tampered artifact must produce a different sha256 hash "
            "(freeze immutability gate is non-tautological)"
        )

    def test_tamper_detected_by_validate_ready(self):
        """validate_ready_for_validation raises ValueError when an artifact is tampered.

        This exercises the runs.py hash-verification path used by the validation
        preflight gate.
        """
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)

        # Tamper with graph.json after freeze
        graph_path = run_dir / "discovery" / "graph.json"
        graph_path.write_text('{"tampered": true}', encoding="utf-8")

        with pytest.raises(ValueError, match="hash mismatch"):
            runs.validate_ready_for_validation(run_id)

    def test_tamper_with_optional_artifact_changes_its_hash(self):
        """Tampering with an optional artifact (e.g. projected_impacts) changes its hash."""
        run_id, run_dir = _make_full_discovery_run()
        hashes_before = _freeze_run(run_id)
        key = "discovery/projected_impacts.parquet"
        if key not in hashes_before:
            pytest.skip("projected_impacts.parquet not in frozen hashes (optional)")

        original_hash = hashes_before[key]
        target = run_dir / "discovery" / "projected_impacts.parquet"
        target.write_bytes(target.read_bytes() + b"\x00TAMPERED_IMPACT")

        from theme_engine import freeze as freeze_mod
        manifest_after = freeze_mod.freeze_discovery(run_id)
        tampered_hash = (manifest_after.discovery_artifact_hashes or {}).get(key)
        assert original_hash != tampered_hash, (
            "Tampered projected_impacts.parquet must produce a different hash"
        )

    def test_freeze_idempotent_on_untampered_artifacts(self):
        """Freezing a second time without tampering produces identical hashes."""
        run_id, _ = _make_full_discovery_run()
        hashes1 = _freeze_run(run_id)
        hashes2 = _freeze_run(run_id)
        assert hashes1 == hashes2, (
            "Idempotent re-freeze must produce identical hashes when artifacts unchanged"
        )


# ===========================================================================
# (c) DISCOVERY/VALIDATION ISOLATION GATE
# ===========================================================================


class TestIsolationReadGuard:
    """Gate (c1/c2): run_cache refuses validation/ reads pre-freeze; allows post-freeze."""

    def setup_method(self):
        """Clear caches before each test to avoid state bleed."""
        run_cache.clear()
        run_cache.clear_frozen_cache()

    def teardown_method(self):
        run_cache.clear()
        run_cache.clear_frozen_cache()

    # ── Helper — minimal validation parquet ───────────────────────────────────

    def _write_market_prices(self, path: Path) -> None:
        _parquet(path, [
            {"schema_version": "1.0", "company_id": "ent_co_001",
             "price_date": "2024-09-30", "adjusted_close": 155.0,
             "available_at": "2024-09-30"},
        ])

    # ── Pre-freeze block (c1) ─────────────────────────────────────────────────

    def test_prefreeeze_validation_read_raises_leakage_error_json(self, tmp_path):
        """load_json raises LeakageError for a validation/ path on an unfrozen run."""
        run_id, run_dir = _make_run_dir(frozen=False)
        val_path = run_dir / "validation" / "market_prices.json"
        _json_file(val_path, {"test": True})

        with pytest.raises(LeakageError, match="pre-freeze|frozen|Leakage guard"):
            run_cache.load_json(val_path)

    def test_prefreeeze_validation_read_raises_leakage_error_parquet(self):
        """load_parquet_rows raises LeakageError for a validation/ path on an unfrozen run."""
        run_id, run_dir = _make_run_dir(frozen=False)
        val_path = run_dir / "validation" / "market_prices.parquet"
        self._write_market_prices(val_path)

        with pytest.raises(LeakageError, match="pre-freeze|frozen|Leakage guard"):
            run_cache.load_parquet_rows(val_path)

    def test_prefreeeze_fundamentals_parquet_blocked(self):
        """validation/fundamentals.parquet (§20) is blocked before freeze."""
        run_id, run_dir = _make_run_dir(frozen=False)
        val_path = run_dir / "validation" / "fundamentals.parquet"
        _parquet(val_path, [{"company_id": "c1", "metric": "revenue", "value": 100.0}])

        with pytest.raises(LeakageError):
            run_cache.load_parquet_rows(val_path)

    def test_prefreeeze_projection_scores_blocked(self):
        """validation/projection_scores.parquet is blocked before freeze."""
        run_id, run_dir = _make_run_dir(frozen=False)
        val_path = run_dir / "validation" / "projection_scores.parquet"
        _parquet(val_path, [{"trigger_id": "t1", "hit": 1}])

        with pytest.raises(LeakageError):
            run_cache.load_parquet_rows(val_path)

    # ── Discovery reads unaffected (c1) ────────────────────────────────────

    def test_discovery_reads_allowed_prefreeeze(self):
        """Discovery artifact reads are NEVER blocked (even on unfrozen runs)."""
        run_id, run_dir = _make_run_dir(frozen=False)
        disc_path = run_dir / "discovery" / "graph.json"
        _json_file(disc_path, {"nodes": [], "edges": []})

        # Must NOT raise
        result = run_cache.load_json(disc_path)
        assert isinstance(result, dict)

    def test_discovery_parquet_reads_allowed_prefreeeze(self):
        """Discovery parquet reads are not blocked by the guard."""
        run_id, run_dir = _make_run_dir(frozen=False)
        disc_path = run_dir / "discovery" / "chunks.parquet"
        _parquet(disc_path, [{"chunk_id": "c1", "available_at": PAST_DATE}])

        rows = run_cache.load_parquet_rows(disc_path)
        assert len(rows) == 1

    # ── Post-freeze reads permitted (c2) ─────────────────────────────────────

    def test_postfreeze_validation_read_allowed(self):
        """After freeze, validation/ reads are permitted."""
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)
        run_cache.clear_frozen_cache()  # ensure guard re-checks manifest

        val_path = run_dir / "validation" / "market_prices.parquet"
        self._write_market_prices(val_path)

        # Must NOT raise
        rows = run_cache.load_parquet_rows(val_path)
        assert len(rows) >= 0

    def test_postfreeze_market_prices_read_allowed(self):
        """market_prices.parquet (§19) is readable after freeze."""
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)
        run_cache.clear_frozen_cache()

        val_path = run_dir / "validation" / "market_prices.parquet"
        self._write_market_prices(val_path)

        rows = run_cache.load_parquet_rows(val_path)
        assert isinstance(rows, list)

    def test_postfreeze_fundamentals_read_allowed(self):
        """validation/fundamentals.parquet (§20) is readable after freeze."""
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)
        run_cache.clear_frozen_cache()

        val_path = run_dir / "validation" / "fundamentals.parquet"
        _parquet(val_path, [{"company_id": "ent_co_001", "metric_name": "revenue",
                              "value_numeric": 1000.0, "period_end": "2024-12-31",
                              "as_of_date": "2025-01-01"}])

        rows = run_cache.load_parquet_rows(val_path)
        assert isinstance(rows, list)

    def test_frozen_cache_accelerates_second_read(self):
        """The frozen-status cache avoids repeated manifest reads on subsequent calls."""
        run_id, run_dir = _make_full_discovery_run()
        _freeze_run(run_id)
        run_cache.clear_frozen_cache()

        val_path = run_dir / "validation" / "market_prices.parquet"
        self._write_market_prices(val_path)

        # First call populates frozen cache
        run_cache.load_parquet_rows(val_path)

        # Corrupt the manifest to verify the cache is used (no re-read of manifest)
        manifest_path = run_dir / "run_manifest.json"
        manifest_path.write_text("{}")  # invalid but should not be read again

        # Second call must still succeed (uses cached frozen=True)
        rows = run_cache.load_parquet_rows(val_path)
        assert isinstance(rows, list)

    def test_non_run_path_never_blocked(self, tmp_path):
        """Paths NOT under run_output_dir are never blocked by the guard."""
        val_dir = tmp_path / "validation"
        val_dir.mkdir()
        val_path = val_dir / "data.json"
        _json_file(val_path, {"unguarded": True})

        # Must NOT raise (tmp_path is not under settings.run_output_dir)
        result = run_cache.load_json(val_path)
        assert result == {"unguarded": True}


# ===========================================================================
# (c) SOURCE-SCAN ISOLATION GATE
# ===========================================================================


# Discovery-stage modules whose source code must NOT reference validation/ paths.
# These are the modules that run BEFORE freeze; they must never read future data.
_DISCOVERY_STAGE_MODULES = {
    "chunking.py",
    "data_import.py",
    "data_cleaning.py",
    "extraction.py",
    "graph_build.py",
    "exposure.py",
    "propagation.py",
    "projected_impacts.py",
    "company_sentiment.py",
    "sentiment_fusion.py",
    "fundamentals_adapter.py",
    "altdata_adapter.py",
    "provenance.py",
    "entity_resolution.py",
    "concept_resolution.py",
    "theme_hierarchy.py",
    "theme_relevance.py",
}

# Pattern that should NOT appear in discovery-stage source code as a runtime
# path reference.  The check looks for string literals containing "validation/"
# in the AST, which catches actual runtime path usage without false positives
# from comments or import names.
_FORBIDDEN_PATH_FRAGMENT = "validation/"

_THEME_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "app" / "backend" / "theme_engine"


def _extract_nondocstring_string_literals(source: str) -> list[str]:
    """Return string literals from *source*, EXCLUDING module/class/function docstrings.

    Docstrings often reference validation/ paths for documentation purposes
    (e.g. "separate from validation/fundamentals.parquet").  The source-scan
    gate cares only about RUNTIME path usage, not documentation text.

    Strategy:
      1. Parse the AST.
      2. Identify all ``ast.Constant(value=str)`` nodes that are the first
         statement body of a Module/FunctionDef/AsyncFunctionDef/ClassDef
         (standard Python docstring position).
      3. Return only non-docstring string constants.

    Tolerant of parse errors (returns []).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Collect node ids of docstring constant nodes to exclude.
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring_ids.add(id(node.body[0].value))

    literals: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            literals.append(node.value)
    return literals


class TestSourceScanIsolation:
    """Gate (c3): discovery-stage modules must not reference validation/ paths."""

    def test_discovery_modules_do_not_hardcode_validation_paths(self):
        """No discovery-stage .py file contains 'validation/' as a string literal.

        This guards against a future developer accidentally hard-coding a
        validation/ path read in a discovery-stage module.
        """
        violations: list[str] = []

        for module_name in sorted(_DISCOVERY_STAGE_MODULES):
            src_path = _THEME_ENGINE_ROOT / module_name
            if not src_path.exists():
                continue  # optional module absent — skip

            source = src_path.read_text(encoding="utf-8")
            literals = _extract_nondocstring_string_literals(source)
            forbidden = [lit for lit in literals if _FORBIDDEN_PATH_FRAGMENT in lit]
            if forbidden:
                violations.append(
                    f"{module_name}: found 'validation/' string literal(s): {forbidden[:3]}"
                )

        assert not violations, (
            "Discovery-stage modules must not reference 'validation/' paths:\n"
            + "\n".join(violations)
        )

    def test_source_scan_is_nontautological(self, tmp_path):
        """Prove the source-scan gate catches a violation when injected.

        Writes a synthetic 'module' with 'validation/' string literal and
        verifies the scanner reports it.
        """
        fake_src = '''
def bad_discovery_read(run_id):
    path = "validation/market_prices.parquet"
    return open(path).read()
'''
        literals = _extract_nondocstring_string_literals(fake_src)
        flagged = [lit for lit in literals if _FORBIDDEN_PATH_FRAGMENT in lit]
        assert flagged, (
            "Source scan must detect 'validation/' string literal in synthetic bad module "
            "(non-tautological check)"
        )

    def test_projection_scorer_not_imported_by_propagation(self):
        """propagation.py must NOT import projection_scorer (one-way leakage discipline)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "propagation_src",
            _THEME_ENGINE_ROOT / "propagation.py",
        )
        source = (_THEME_ENGINE_ROOT / "propagation.py").read_text(encoding="utf-8")
        assert "projection_scorer" not in source, (
            "propagation.py must not import projection_scorer "
            "(post-freeze scorer must never flow into discovery)"
        )

    def test_projection_scorer_not_imported_by_projected_impacts(self):
        """projected_impacts.py must NOT import projection_scorer."""
        source = (_THEME_ENGINE_ROOT / "projected_impacts.py").read_text(encoding="utf-8")
        assert "projection_scorer" not in source, (
            "projected_impacts.py must not import projection_scorer"
        )


# ===========================================================================
# (d) leakage.py CLASSIFICATION SELF-TESTS
# ===========================================================================


class TestLeakageClassification:
    """Verify the single-source classification in leakage.py."""

    def test_validation_only_artifacts_not_in_discovery(self):
        """No artifact can be in both DISCOVERY_ARTIFACTS and VALIDATION_ONLY_ARTIFACTS."""
        overlap = DISCOVERY_ARTIFACTS & VALIDATION_ONLY_ARTIFACTS
        assert not overlap, (
            f"Artifacts appear in both discovery and validation-only sets: {overlap}"
        )

    def test_is_validation_path_detects_validation_dir(self, tmp_path):
        """is_validation_path returns True for paths under validation/."""
        val_path = tmp_path / "run_001" / "validation" / "market_prices.parquet"
        val_path.parent.mkdir(parents=True, exist_ok=True)
        val_path.touch()
        assert is_validation_path(val_path) is True

    def test_is_validation_path_ignores_discovery_dir(self, tmp_path):
        """is_validation_path returns False for discovery/ paths."""
        disc_path = tmp_path / "run_001" / "discovery" / "chunks.parquet"
        disc_path.parent.mkdir(parents=True, exist_ok=True)
        disc_path.touch()
        assert is_validation_path(disc_path) is False

    def test_assert_read_allowed_passes_for_discovery(self, tmp_path):
        """assert_read_allowed does not raise for discovery paths."""
        disc_path = tmp_path / "run_001" / "discovery" / "graph.json"
        disc_path.parent.mkdir(parents=True, exist_ok=True)
        disc_path.touch()
        # Must not raise regardless of frozen status
        assert_read_allowed(disc_path, discovery_frozen=False)
        assert_read_allowed(disc_path, discovery_frozen=True)

    def test_assert_read_allowed_raises_for_validation_prefreeeze(self, tmp_path):
        """assert_read_allowed raises LeakageError for validation/ paths when not frozen."""
        val_path = tmp_path / "run_001" / "validation" / "market_prices.parquet"
        val_path.parent.mkdir(parents=True, exist_ok=True)
        val_path.touch()
        with pytest.raises(LeakageError):
            assert_read_allowed(val_path, discovery_frozen=False)

    def test_assert_read_allowed_passes_for_validation_postfreeze(self, tmp_path):
        """assert_read_allowed does not raise for validation/ paths when frozen=True."""
        val_path = tmp_path / "run_001" / "validation" / "market_prices.parquet"
        val_path.parent.mkdir(parents=True, exist_ok=True)
        val_path.touch()
        # Must not raise
        assert_read_allowed(val_path, discovery_frozen=True)

    def test_validation_only_artifacts_contains_required(self):
        """VALIDATION_ONLY_ARTIFACTS contains the three §-required artifacts."""
        assert "fundamentals.parquet" in VALIDATION_ONLY_ARTIFACTS, (
            "fundamentals.parquet (§20) must be in VALIDATION_ONLY_ARTIFACTS"
        )
        assert "market_prices.parquet" in VALIDATION_ONLY_ARTIFACTS, (
            "market_prices.parquet (§19) must be in VALIDATION_ONLY_ARTIFACTS"
        )
        assert "projection_scores.parquet" in VALIDATION_ONLY_ARTIFACTS, (
            "projection_scores.parquet (§FI-E) must be in VALIDATION_ONLY_ARTIFACTS"
        )

    def test_discovery_artifacts_contains_required(self):
        """DISCOVERY_ARTIFACTS contains the full expected set."""
        required = {
            "chunks.parquet", "entities.parquet", "edges.parquet",
            "graph.json", "communities.json", "company_theme_exposure.parquet",
            "projected_impacts.parquet", "management_sentiment.parquet",
            "management_sentiment_fused.parquet", "fundamentals_asreported.parquet",
            "entity_chunk_provenance.parquet",
        }
        missing = required - DISCOVERY_ARTIFACTS
        assert not missing, (
            f"Expected discovery artifacts missing from DISCOVERY_ARTIFACTS: {missing}"
        )
