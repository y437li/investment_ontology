"""Run-level data integrity validator — CI pytest gate.

(a) POSITIVE: builds a small fixture run through the real pipeline
    (import -> clean -> chunk -> extract -> resolve) and asserts that
    validate_run reports ok=True with zero violations.

(b) NEGATIVE: for EACH check category, corrupts one artifact in a temp copy
    of the run and asserts that validate_run flags exactly that violation.
    Categories:
      1. PIT_NO_LEAKAGE        — chunk.available_at set after as_of_date
      2. REFERENTIAL_INTEGRITY — edge references an entity that no longer exists
      3. SCHEMA_CONFORMANCE    — a required column is renamed/removed
      4. RECONCILIATION        — a document is silently dropped from documents.parquet
      5. NON_NULL              — entity_id set to None
      6. REFERENTIAL_INTEGRITY — chunk orphaned from documents
      7. PIT_NO_LEAKAGE (inheritance) — chunk.available_at != document.available_at

No network calls occur (RuleBasedExtractor is the default).
Artifacts are written to tmp dirs — never under data/.

Note: conftest.py sets RUN_OUTPUT_DIR before theme_engine is imported, so
Settings() captures it at class-definition time. All pipeline calls use the
conftest tmp dir. Negative tests corrupt artifacts in place and restore them
after each test, rather than trying to override the settings singleton.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import sys
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine.main import app
from theme_engine.config import settings
from theme_engine.integrity import validate_run, assert_run_ok, IntegrityError

client = TestClient(app)

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "extraction"
AS_OF_DATE = "2024-06-30"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_full_run() -> str:
    """Run the complete discovery pipeline using the conftest RUN_OUTPUT_DIR.

    Returns run_id. Asserts every step succeeds.
    """
    resp = client.post("/api/runs/create", json={"as_of_date": AS_OF_DATE})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    resp = client.post(
        "/api/data/import",
        json={
            "run_id": run_id,
            "documents_dir": str(FIXTURES),
            "source_manifest_path": str(FIXTURES / "source_manifest.csv"),
        },
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/api/data/clean",
        json={"run_id": run_id, "documents_dir": str(FIXTURES)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["included_documents"] >= 1

    resp = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/extraction/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/extraction/resolve", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


def _get_check(report, check_name: str):
    """Return the CheckResult for the given check name."""
    for c in report.checks:
        if c.check == check_name:
            return c
    raise KeyError(f"check {check_name!r} not found in report")


def _discovery_dir(run_id: str) -> Path:
    """Return the discovery directory for a run using the configured output dir."""
    return settings.run_output_dir / run_id / "discovery"


def _read_parquet(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def _write_parquet(rows: list[dict], columns: list[str], path: Path) -> None:
    """Write rows as a parquet file with the given column order."""
    if not rows:
        schema_fields = []
        for col in columns:
            if col in ("source_chunk_ids", "evidence_chunk_ids", "source_record_ids"):
                schema_fields.append(pa.field(col, pa.list_(pa.string())))
            elif col == "confidence":
                schema_fields.append(pa.field(col, pa.float64()))
            else:
                schema_fields.append(pa.field(col, pa.string()))
        schema = pa.schema(schema_fields)
        pq.write_table(
            pa.table(
                {col: pa.array([], type=f.type) for col, f in zip(columns, schema_fields)},
                schema=schema,
            ),
            path,
        )
        return
    pydict = {col: [r.get(col) for r in rows] for col in columns}
    pq.write_table(pa.Table.from_pydict(pydict), path)


# ---------------------------------------------------------------------------
# Module-scoped fixture: build a run once; tests corrupt in place and restore
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pipeline_run():
    """Build a full pipeline run once; yield run_id.

    Individual negative tests must save + restore any artifacts they mutate.
    """
    run_id = _build_full_run()
    yield run_id
    # run dir lives in conftest tmp dir and is cleaned up automatically


# ---------------------------------------------------------------------------
# (a) POSITIVE: full pipeline run should produce ok=True, zero violations
# ---------------------------------------------------------------------------


def test_positive_full_pipeline_ok(pipeline_run):
    """A clean end-to-end run must produce ok=True with zero violations."""
    run_id = pipeline_run
    report = validate_run(run_id)

    assert report.ok is True, (
        "Expected ok=True but got violations:\n"
        + "\n".join(
            f"  [{c.check}] {v}"
            for c in report.checks
            if not c.passed
            for v in c.violations
        )
    )
    total_violations = sum(len(c.violations) for c in report.checks)
    assert total_violations == 0, (
        f"Expected zero violations but found {total_violations}"
    )


# ---------------------------------------------------------------------------
# Negative test helper: save/restore a parquet artifact around a test body
# ---------------------------------------------------------------------------


class _ArtifactGuard:
    """Context manager that saves an artifact before the test and restores it after."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._backup_dir = tempfile.mkdtemp(prefix="artifact_guard_")
        self._backup = Path(self._backup_dir) / path.name

    def __enter__(self) -> "_ArtifactGuard":
        shutil.copy2(self._path, self._backup)
        return self

    def __exit__(self, *_) -> None:
        shutil.copy2(self._backup, self._path)
        shutil.rmtree(self._backup_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# (b) NEGATIVE: PIT_NO_LEAKAGE — chunk.available_at after as_of_date
# ---------------------------------------------------------------------------


def test_negative_pit_chunk_after_asof(pipeline_run):
    """Setting a chunk.available_at after as_of_date must trigger PIT_NO_LEAKAGE."""
    run_id = pipeline_run
    chunks_path = _discovery_dir(run_id) / "chunks.parquet"

    with _ArtifactGuard(chunks_path):
        rows = _read_parquet(chunks_path)
        assert rows, "need at least one chunk row for this test"

        future_date = "2099-12-31"
        rows[0] = dict(rows[0])
        rows[0]["available_at"] = future_date

        from theme_engine.chunking import CHUNKS_COLUMNS
        _write_parquet(rows, CHUNKS_COLUMNS, chunks_path)

        report = validate_run(run_id)

    pit_check = _get_check(report, "PIT_NO_LEAKAGE")
    assert pit_check.passed is False, "PIT_NO_LEAKAGE should fail"
    assert any(future_date in v for v in pit_check.violations), (
        f"Expected violation mentioning {future_date!r}; got: {pit_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: REFERENTIAL_INTEGRITY — edge references a missing entity
# ---------------------------------------------------------------------------


def test_negative_ref_integrity_missing_entity(pipeline_run):
    """Removing an entity referenced by an edge must trigger REFERENTIAL_INTEGRITY."""
    run_id = pipeline_run
    entities_path = _discovery_dir(run_id) / "entities.parquet"

    with _ArtifactGuard(entities_path):
        edges = _read_parquet(_discovery_dir(run_id) / "edges.parquet")
        entities = _read_parquet(entities_path)

        assert edges, "need at least one edge for this test"
        assert entities, "need at least one entity for this test"

        # Pick the source_entity_id of the first edge
        target_entity_id = edges[0]["source_entity_id"]

        # Remove that entity from entities.parquet
        remaining = [e for e in entities if e["entity_id"] != target_entity_id]
        assert len(remaining) < len(entities), "entity not found — test broken"

        from theme_engine.extraction import ENTITIES_COLUMNS
        _write_parquet(remaining, ENTITIES_COLUMNS, entities_path)

        report = validate_run(run_id)

    ref_check = _get_check(report, "REFERENTIAL_INTEGRITY")
    assert ref_check.passed is False, "REFERENTIAL_INTEGRITY should fail"
    assert any(target_entity_id in v for v in ref_check.violations), (
        f"Expected violation mentioning {target_entity_id!r}; got: {ref_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: SCHEMA_CONFORMANCE — rename a required column
# ---------------------------------------------------------------------------


def test_negative_schema_missing_column(pipeline_run):
    """Renaming a required column in chunks.parquet must trigger SCHEMA_CONFORMANCE."""
    run_id = pipeline_run
    chunks_path = _discovery_dir(run_id) / "chunks.parquet"

    with _ArtifactGuard(chunks_path):
        table = pq.read_table(chunks_path)

        # Rename 'chunk_id' to an invalid name
        old_names = table.schema.names
        new_names = ["chunk_identifier" if n == "chunk_id" else n for n in old_names]
        renamed = table.rename_columns(new_names)
        pq.write_table(renamed, chunks_path)

        report = validate_run(run_id)

    schema_check = _get_check(report, "SCHEMA_CONFORMANCE")
    assert schema_check.passed is False, "SCHEMA_CONFORMANCE should fail"
    assert any("chunks" in v for v in schema_check.violations), (
        f"Expected violation mentioning 'chunks'; got: {schema_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: RECONCILIATION — silently drop a document from documents.parquet
# ---------------------------------------------------------------------------


def test_negative_reconciliation_silent_drop(pipeline_run):
    """Silently removing a row from documents.parquet must trigger RECONCILIATION."""
    run_id = pipeline_run
    docs_path = _discovery_dir(run_id) / "documents.parquet"

    with _ArtifactGuard(docs_path):
        rows = _read_parquet(docs_path)
        assert len(rows) >= 1, "need at least one document row for this test"

        # Silently drop the first document (no corresponding cleaning log entry)
        truncated = rows[1:]

        from theme_engine.data_cleaning import DOCUMENTS_COLUMNS
        _write_parquet(truncated, DOCUMENTS_COLUMNS, docs_path)

        report = validate_run(run_id)

    recon_check = _get_check(report, "RECONCILIATION")
    assert recon_check.passed is False, "RECONCILIATION should fail"
    assert len(recon_check.violations) >= 1, (
        f"Expected at least one RECONCILIATION violation; got: {recon_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: NON_NULL — set entity_id to None
# ---------------------------------------------------------------------------


def test_negative_non_null_entity_id(pipeline_run):
    """Setting entity_id=None in entities.parquet must trigger NON_NULL."""
    run_id = pipeline_run
    entities_path = _discovery_dir(run_id) / "entities.parquet"

    with _ArtifactGuard(entities_path):
        rows = _read_parquet(entities_path)
        assert rows, "need at least one entity row for this test"

        rows[0] = dict(rows[0])
        rows[0]["entity_id"] = None

        from theme_engine.extraction import ENTITIES_COLUMNS
        _write_parquet(rows, ENTITIES_COLUMNS, entities_path)

        report = validate_run(run_id)

    null_check = _get_check(report, "NON_NULL")
    assert null_check.passed is False, "NON_NULL should fail"
    assert any("entity_id" in v for v in null_check.violations), (
        f"Expected violation mentioning 'entity_id'; got: {null_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: REFERENTIAL_INTEGRITY — chunk orphaned from documents
# ---------------------------------------------------------------------------


def test_negative_ref_integrity_orphan_chunk(pipeline_run):
    """A chunk referencing a non-existent document_id must trigger REFERENTIAL_INTEGRITY."""
    run_id = pipeline_run
    chunks_path = _discovery_dir(run_id) / "chunks.parquet"

    with _ArtifactGuard(chunks_path):
        rows = _read_parquet(chunks_path)
        assert rows, "need at least one chunk for this test"

        rows[0] = dict(rows[0])
        rows[0]["document_id"] = "doc_NONEXISTENT_999"

        from theme_engine.chunking import CHUNKS_COLUMNS
        _write_parquet(rows, CHUNKS_COLUMNS, chunks_path)

        report = validate_run(run_id)

    ref_check = _get_check(report, "REFERENTIAL_INTEGRITY")
    assert ref_check.passed is False, "REFERENTIAL_INTEGRITY should fail"
    assert any("doc_NONEXISTENT_999" in v for v in ref_check.violations), (
        f"Expected violation mentioning orphan document_id; got: {ref_check.violations}"
    )


# ---------------------------------------------------------------------------
# (b) NEGATIVE: PIT_NO_LEAKAGE (inheritance) — chunk.available_at != document
# ---------------------------------------------------------------------------


def test_negative_pit_chunk_inheritance_violation(pipeline_run):
    """A chunk whose available_at differs from its document must trigger PIT_NO_LEAKAGE."""
    run_id = pipeline_run
    chunks_path = _discovery_dir(run_id) / "chunks.parquet"

    with _ArtifactGuard(chunks_path):
        rows = _read_parquet(chunks_path)
        assert rows, "need at least one chunk for this test"

        rows[0] = dict(rows[0])
        # Use a clearly different but past date so it won't also trigger the
        # "after as_of_date" violation — we want to isolate the inheritance check.
        rows[0]["available_at"] = "2020-01-01"

        from theme_engine.chunking import CHUNKS_COLUMNS
        _write_parquet(rows, CHUNKS_COLUMNS, chunks_path)

        report = validate_run(run_id)

    pit_check = _get_check(report, "PIT_NO_LEAKAGE")
    assert pit_check.passed is False, (
        f"PIT_NO_LEAKAGE should fail (inheritance violation); "
        f"violations={pit_check.violations}"
    )
    assert any("inheritance violation" in v for v in pit_check.violations), (
        f"Expected 'inheritance violation' in violations; got: {pit_check.violations}"
    )


# ---------------------------------------------------------------------------
# Bonus: assert_run_ok raises IntegrityError on corruption
# ---------------------------------------------------------------------------


def test_assert_run_ok_raises_on_failure(pipeline_run):
    """assert_run_ok must raise IntegrityError when a check fails."""
    run_id = pipeline_run
    entities_path = _discovery_dir(run_id) / "entities.parquet"

    with _ArtifactGuard(entities_path):
        rows = _read_parquet(entities_path)
        assert rows

        rows[0] = dict(rows[0])
        rows[0]["entity_id"] = None

        from theme_engine.extraction import ENTITIES_COLUMNS
        _write_parquet(rows, ENTITIES_COLUMNS, entities_path)

        with pytest.raises(IntegrityError):
            assert_run_ok(run_id)
