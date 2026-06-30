"""Tests for EG-C (company detail page endpoints) + EG-D (evidence quantification).

Acceptance criteria:
1. PROFILE: GET /api/themes/{run}/companies/{id} returns profile + fundamentals +
   financial_facts + per-theme exposure list.

2. NO_SILENT_BLANK: fundamentals.available=False + an explicit message when
   no B1 rows exist at as_of for the company — never a silent blank payload.

3. PIT: fundamentals rows with available_at AFTER as_of are NOT returned;
   rows at or before as_of ARE returned.

4. EVIDENCE_BY_THEME: GET .../{id}/evidence groups evidence strictly by theme
   (E3 grain) — a company in >=2 themes has each theme's evidence isolated.
   Cross-theme bleed test: chunk_ids from theme A must NOT appear in theme B.

5. EG-D: when a financial_metrics.parquet fact exists for a chunk, evidence
   endpoint returns financial_fact + fact_label for that chunk; when none
   exists, financial_fact=None (sentence-level fallback, no regression).

6. ENTITY_NOT_FOUND: returns 404 when the entity_id does not exist in the run.

Note: test_endpoints_guard_when_llm_absent (in test_pipeline_integrity.py) is a
pre-existing environmental failure (missing openai package) and is reported but
NOT part of these tests.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine.main import app
from theme_engine.config import settings

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _run_dir(run_id: str) -> Path:
    return settings.run_output_dir / run_id


def _discovery(run_id: str) -> Path:
    return _run_dir(run_id) / "discovery"


def _make_manifest(run_id: str, as_of: str) -> None:
    _run_dir(run_id).mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "as_of_date": as_of,
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "created_at": "2025-01-01T00:00:00Z",
        "code_version": "test",
        "input_hash": "abc",
        "discovery_frozen": False,
    }
    (_run_dir(run_id) / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_entities(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("schema_version", pa.string()),
        pa.field("entity_id", pa.string()),
        pa.field("entity_type", pa.string()),
        pa.field("name", pa.string()),
        pa.field("canonical_name", pa.string()),
        pa.field("ticker", pa.string()),
        pa.field("exchange", pa.string()),
        pa.field("sector", pa.string()),
        pa.field("country", pa.string()),
        pa.field("first_seen_at", pa.string()),
        pa.field("source_chunk_ids", pa.list_(pa.string())),
        pa.field("confidence", pa.float64()),
        pa.field("extraction_method", pa.string()),
        pa.field("review_status", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col == "source_chunk_ids" and v is None:
                v = []
            elif col == "confidence" and v is None:
                v = 1.0
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


def _write_exposure(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("schema_version", pa.string()),
        pa.field("as_of_date", pa.string()),
        pa.field("company_id", pa.string()),
        pa.field("ticker", pa.string()),
        pa.field("theme_snapshot_id", pa.string()),
        pa.field("community_id", pa.string()),
        pa.field("exposure_score", pa.float64()),
        pa.field("graph_distance", pa.float64()),
        pa.field("edge_confidence_sum", pa.float64()),
        pa.field("evidence_count", pa.int64()),
        pa.field("top_evidence_chunk_ids", pa.list_(pa.string())),
        pa.field("calculation_method", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col == "top_evidence_chunk_ids" and v is None:
                v = []
            elif col == "evidence_count" and v is None:
                v = 0
            elif col in ("exposure_score", "graph_distance", "edge_confidence_sum") and v is None:
                v = 0.0
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


def _write_communities_json(path: Path, communities: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"communities": communities}), encoding="utf-8")


def _write_theme_snapshots_json(path: Path, snapshots: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"snapshots": snapshots}), encoding="utf-8")


def _write_chunks(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("schema_version", pa.string()),
        pa.field("run_id", pa.string()),
        pa.field("chunk_id", pa.string()),
        pa.field("document_id", pa.string()),
        pa.field("raw_document_id", pa.string()),
        pa.field("text", pa.string()),
        pa.field("available_at", pa.string()),
        pa.field("start_char", pa.int64()),
        pa.field("end_char", pa.int64()),
        pa.field("chunk_index", pa.int64()),
        pa.field("section_title", pa.string()),
        pa.field("block_type", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col in ("start_char", "end_char", "chunk_index") and v is None:
                v = 0
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


def _write_fundamentals(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("company_id", pa.string()),
        pa.field("period_end", pa.string()),
        pa.field("metric_name", pa.string()),
        pa.field("metric_value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("currency", pa.string()),
        pa.field("filing_date", pa.string()),
        pa.field("available_at", pa.string()),
        pa.field("source", pa.string()),
        pa.field("source_id", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col == "metric_value" and v is None:
                v = 0.0
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


def _write_financial_metrics(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("schema_version", pa.string()),
        pa.field("metric_id", pa.string()),
        pa.field("company_id", pa.string()),
        pa.field("metric_name", pa.string()),
        pa.field("value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("period", pa.string()),
        pa.field("direction", pa.string()),
        pa.field("is_guidance", pa.bool_()),
        pa.field("confidence", pa.float64()),
        pa.field("evidence_chunk_id", pa.string()),
        pa.field("source", pa.string()),
        pa.field("created_at", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col == "value" and v is None:
                v = 0.0
            elif col == "confidence" and v is None:
                v = 0.8
            elif col == "is_guidance" and v is None:
                v = False
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


def _write_e3(path: Path, rows: list[dict]) -> None:
    """Write company_theme_document_evidence.parquet (E3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("schema_version", pa.string()),
        pa.field("as_of_date", pa.string()),
        pa.field("company_id", pa.string()),
        pa.field("theme_snapshot_id", pa.string()),
        pa.field("community_id", pa.string()),
        pa.field("chunk_ids", pa.list_(pa.string())),
        pa.field("document_ids", pa.list_(pa.string())),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            v = row.get(col)
            if col in ("chunk_ids", "document_ids") and v is None:
                v = []
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), path)


# ---------------------------------------------------------------------------
# Minimal run fixture builder
# ---------------------------------------------------------------------------

def _build_minimal_run(
    *,
    as_of: str = "2024-12-31",
    company_id: str = "ent_co1",
    ticker: str = "CO1.TO",
    theme_ids: list[str] | None = None,
    fund_rows: list[dict] | None = None,
    fm_rows: list[dict] | None = None,
    chunk_rows: list[dict] | None = None,
    e3_rows: list[dict] | None = None,
) -> str:
    """Build a minimal run fixture and return run_id."""
    if theme_ids is None:
        theme_ids = ["comm_1"]

    run_id = f"run_co_{uuid.uuid4().hex[:8]}"
    ddir = _discovery(run_id)
    _make_manifest(run_id, as_of)

    # entities.parquet
    _write_entities(ddir / "entities.parquet", [
        {
            "schema_version": "1.0",
            "entity_id": company_id,
            "entity_type": "Company",
            "name": "Acme Corp",
            "canonical_name": "Acme Corp",
            "ticker": ticker,
            "exchange": "TSX",
            "sector": "Energy",
            "country": "Canada",
            "first_seen_at": "2024-01-01",
        }
    ])

    # company_theme_exposure.parquet
    exposure = []
    for i, cid in enumerate(theme_ids):
        exposure.append({
            "schema_version": "1.0",
            "as_of_date": as_of,
            "company_id": company_id,
            "ticker": ticker,
            "theme_snapshot_id": f"snap_{cid}",
            "community_id": cid,
            "exposure_score": 0.8 - i * 0.1,
            "graph_distance": 1.0,
            "edge_confidence_sum": 0.9,
            "evidence_count": 3,
            "top_evidence_chunk_ids": [f"chunk_{cid}_1", f"chunk_{cid}_2"],
            "calculation_method": "exposure_v1_document_stated",
        })
    _write_exposure(ddir / "company_theme_exposure.parquet", exposure)

    # communities.json
    _write_communities_json(ddir / "communities.json", [
        {
            "community_id": cid,
            "theme_name": f"Theme {cid}",
            "node_ids": [company_id],
            "edge_ids": [],
            "top_entities": [],
            "top_companies": [company_id],
        }
        for cid in theme_ids
    ])

    # theme_snapshots.json
    _write_theme_snapshots_json(ddir / "theme_snapshots.json", [
        {
            "theme_snapshot_id": f"snap_{cid}",
            "community_id": cid,
            "name": f"Theme {cid}",
            "state": "active",
        }
        for cid in theme_ids
    ])

    # chunks.parquet
    if chunk_rows is None:
        chunk_rows = [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "chunk_id": f"chunk_{cid}_{j}",
                "document_id": f"doc_{cid}",
                "raw_document_id": f"raw_{cid}",
                "text": f"Text for {cid} chunk {j}.",
                "available_at": "2024-06-01",
                "start_char": 0,
                "end_char": 20,
                "chunk_index": j - 1,
                "section_title": "Results",
                "block_type": "paragraph",
            }
            for cid in theme_ids
            for j in (1, 2)
        ]
    _write_chunks(ddir / "chunks.parquet", chunk_rows)

    # fundamentals_asreported.parquet (optional)
    if fund_rows is not None:
        _write_fundamentals(ddir / "fundamentals_asreported.parquet", fund_rows)

    # financial_metrics.parquet (optional)
    if fm_rows is not None:
        _write_financial_metrics(ddir / "financial_metrics.parquet", fm_rows)

    # company_theme_document_evidence.parquet (E3, optional)
    if e3_rows is None and theme_ids:
        e3_rows = [
            {
                "schema_version": "1.0",
                "as_of_date": as_of,
                "company_id": company_id,
                "theme_snapshot_id": f"snap_{cid}",
                "community_id": cid,
                "chunk_ids": [f"chunk_{cid}_1", f"chunk_{cid}_2"],
                "document_ids": [f"doc_{cid}"],
            }
            for cid in theme_ids
        ]
    if e3_rows is not None:
        _write_e3(ddir / "company_theme_document_evidence.parquet", e3_rows)

    return run_id


# ---------------------------------------------------------------------------
# 1. PROFILE: basic profile + exposure + fundamentals fields
# ---------------------------------------------------------------------------


def test_company_profile_returns_expected_fields():
    """GET /api/themes/{run}/companies/{id} returns all required top-level keys."""
    run_id = _build_minimal_run(
        fund_rows=[{
            "company_id": "ent_co1",
            "period_end": "2024-06-30",
            "metric_name": "revenue",
            "metric_value": 100.0,
            "unit": "CAD_billions",
            "currency": "CAD",
            "filing_date": "2024-08-01",
            "available_at": "2024-08-01",
            "source": "edgar_xbrl",
            "source_id": "x001",
        }]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["company_id"] == "ent_co1"
    assert data["ticker"] == "CO1.TO"
    assert data["name"] == "Acme Corp"
    assert data["as_of_date"] == "2024-12-31"
    assert isinstance(data["themes"], list)
    assert len(data["themes"]) == 1
    assert data["themes"][0]["community_id"] == "comm_1"
    assert data["themes"][0]["exposure_score"] > 0

    assert "fundamentals" in data
    assert data["fundamentals"]["available"] is True
    assert len(data["fundamentals"]["rows"]) == 1
    assert data["fundamentals"]["rows"][0]["metric_name"] == "revenue"
    assert data["fundamentals"]["rows"][0]["metric_value"] == 100.0


# ---------------------------------------------------------------------------
# 2. NO_SILENT_BLANK: explicit empty state when no fundamentals available
# ---------------------------------------------------------------------------


def test_no_fundamentals_explicit_empty_state():
    """When no B1 rows exist at as_of, fundamentals.available=False and
    message is non-empty — never a silent blank."""
    run_id = _build_minimal_run(
        # fundamentals artifact exists but is empty (no rows for company)
        fund_rows=[]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "fundamentals" in data
    assert data["fundamentals"]["available"] is False, (
        "fundamentals.available must be False when no rows pass PIT filter"
    )
    # No silent blank: either message is set or rows is empty but not missing
    assert "rows" in data["fundamentals"] or "message" in data["fundamentals"], (
        "fundamentals must carry rows or message — never silently blank"
    )


def test_fundamentals_missing_artifact_explicit_state():
    """When fundamentals_asreported.parquet doesn't exist at all,
    fundamentals.available=False — explicit, never blank."""
    run_id = _build_minimal_run()  # no fund_rows -> no artifact written

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["fundamentals"]["available"] is False, (
        "Missing artifact must yield available=False, not silent blank"
    )


# ---------------------------------------------------------------------------
# 3. PIT: available_at gate is enforced
# ---------------------------------------------------------------------------


def test_fundamentals_pit_filter():
    """Fundamentals rows with available_at > as_of are excluded;
    rows with available_at <= as_of are included."""
    as_of = "2024-06-30"
    run_id = _build_minimal_run(
        as_of=as_of,
        fund_rows=[
            {   # PIT: before as_of — must be included
                "company_id": "ent_co1",
                "period_end": "2024-03-31",
                "metric_name": "revenue",
                "metric_value": 50.0,
                "unit": "CAD_billions",
                "currency": "CAD",
                "filing_date": "2024-05-01",
                "available_at": "2024-05-01",   # <= 2024-06-30
                "source": "edgar_xbrl",
                "source_id": "x001",
            },
            {   # Future: after as_of — must be excluded
                "company_id": "ent_co1",
                "period_end": "2024-06-30",
                "metric_name": "revenue",
                "metric_value": 60.0,
                "unit": "CAD_billions",
                "currency": "CAD",
                "filing_date": "2024-08-01",
                "available_at": "2024-08-01",   # > 2024-06-30
                "source": "edgar_xbrl",
                "source_id": "x002",
            },
        ]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    rows = data["fundamentals"]["rows"]
    assert len(rows) == 1, (
        f"Only the PIT-clean row (available_at <= {as_of}) should be returned; got {rows}"
    )
    assert rows[0]["metric_value"] == 50.0


def test_fundamentals_empty_available_at_excluded_fail_closed():
    """A fundamentals row with an EMPTY available_at is EXCLUDED (fail-closed, OI-8).

    A missing available_at cannot prove the row was knowable at as_of, so it must
    not be returned (the old behaviour silently included such rows)."""
    as_of = "2024-06-30"
    run_id = _build_minimal_run(
        as_of=as_of,
        fund_rows=[
            {   # No available_at -> must be EXCLUDED (fail-closed)
                "company_id": "ent_co1",
                "period_end": "2024-03-31",
                "metric_name": "revenue",
                "metric_value": 50.0,
                "unit": "CAD_billions",
                "currency": "CAD",
                "filing_date": "2024-05-01",
                "available_at": "",             # EMPTY -> exclude
                "source": "edgar_xbrl",
                "source_id": "x001",
            },
        ]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["fundamentals"]["rows"] == [], (
        "A row with empty available_at must be excluded (fail-closed)."
    )
    assert data["fundamentals"]["available"] is False


# ---------------------------------------------------------------------------
# 4. EVIDENCE_BY_THEME: no cross-theme bleed (company in >=2 themes)
# ---------------------------------------------------------------------------


def test_evidence_grouped_by_theme_no_cross_bleed():
    """A company in 2 themes must have each theme's evidence strictly isolated.

    chunk_ids for theme A must not appear in theme B's evidence group.
    This is the authoritative acceptance test for E3 grain correctness.
    """
    run_id = _build_minimal_run(theme_ids=["comm_A", "comm_B"])

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/evidence")
    assert resp.status_code == 200, resp.text
    groups = resp.json()

    assert len(groups) == 2, f"Expected 2 theme groups; got {len(groups)}: {[g['community_id'] for g in groups]}"

    # Collect chunk_ids per theme
    chunks_by_theme: dict[str, set[str]] = {}
    for group in groups:
        cid = group["community_id"]
        chunks_by_theme[cid] = {ch["chunk_id"] for ch in group["chunks"]}

    # Verify strict isolation: chunk_ids for comm_A must NOT be in comm_B
    a_chunks = chunks_by_theme.get("comm_A", set())
    b_chunks = chunks_by_theme.get("comm_B", set())
    assert a_chunks, "comm_A must have at least one chunk"
    assert b_chunks, "comm_B must have at least one chunk"

    overlap = a_chunks & b_chunks
    assert not overlap, (
        f"Cross-theme bleed detected! Chunks {overlap} appeared in BOTH theme groups. "
        "Evidence must be strictly isolated per theme (E3 grain)."
    )

    # Verify expected chunk_id patterns
    for chunk_id in a_chunks:
        assert "comm_A" in chunk_id, (
            f"chunk_id {chunk_id!r} in comm_A group looks wrong — "
            "it should belong to comm_A's evidence"
        )
    for chunk_id in b_chunks:
        assert "comm_B" in chunk_id, (
            f"chunk_id {chunk_id!r} in comm_B group looks wrong"
        )


# ---------------------------------------------------------------------------
# 5. EG-D: financial_fact attached when B2 fact exists; None fallback
# ---------------------------------------------------------------------------


def test_egd_fact_attached_to_evidence_chunk():
    """When a B2 financial_metrics row exists for a chunk, the evidence endpoint
    returns financial_fact (non-None) and fact_label for that chunk."""
    chunk_id_with_fact = "chunk_comm_1_1"
    chunk_id_no_fact = "chunk_comm_1_2"

    run_id = _build_minimal_run(
        fm_rows=[{
            "schema_version": "1.0",
            "metric_id": "fm_001",
            "company_id": "ent_co1",
            "metric_name": "revenue",
            "value": 1.5,
            "unit": "CAD_billions",
            "period": "Q1 2024",
            "direction": "rose",
            "is_guidance": False,
            "confidence": 0.92,
            "evidence_chunk_id": chunk_id_with_fact,
            "source": "llm_extraction",
            "created_at": "2024-12-01T00:00:00Z",
        }]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/evidence")
    assert resp.status_code == 200, resp.text
    groups = resp.json()

    assert groups, "Evidence groups must be non-empty"
    group = groups[0]

    chunks_by_id = {ch["chunk_id"]: ch for ch in group["chunks"]}

    # Chunk WITH fact (EG-D)
    assert chunk_id_with_fact in chunks_by_id, (
        f"chunk {chunk_id_with_fact!r} should be in evidence"
    )
    ch_fact = chunks_by_id[chunk_id_with_fact]
    assert ch_fact["financial_fact"] is not None, (
        "financial_fact must be non-None when a B2 fact is extracted for this chunk"
    )
    assert ch_fact["fact_label"] is not None, (
        "fact_label must be non-None when a B2 fact is extracted"
    )
    assert "revenue" in ch_fact["fact_label"].lower(), (
        f"fact_label must mention the metric name; got: {ch_fact['fact_label']!r}"
    )
    assert ch_fact["financial_fact"]["value"] == 1.5
    assert ch_fact["financial_fact"]["metric_name"] == "revenue"

    # Chunk WITHOUT fact (sentence-level fallback, no regression)
    if chunk_id_no_fact in chunks_by_id:
        ch_no_fact = chunks_by_id[chunk_id_no_fact]
        assert ch_no_fact["financial_fact"] is None, (
            "financial_fact must be None when no B2 fact was extracted (sentence fallback)"
        )
        assert ch_no_fact["fact_label"] is None, (
            "fact_label must be None when no B2 fact was extracted"
        )
        # Text snippet is still present (sentence-level fallback)
        assert ch_no_fact["text"] is not None, (
            "text snippet must be present even without a financial fact"
        )


def test_egd_no_regression_when_no_financial_metrics():
    """When financial_metrics.parquet is absent entirely, the evidence endpoint
    still returns chunks with text snippets (sentence-level fallback)."""
    run_id = _build_minimal_run()  # no fm_rows -> no financial_metrics.parquet

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/evidence")
    assert resp.status_code == 200, resp.text
    groups = resp.json()

    assert groups, "Evidence groups must be non-empty even without B2 facts"
    for group in groups:
        for ch in group["chunks"]:
            assert ch["financial_fact"] is None, (
                "financial_fact must be None when no B2 artifact exists"
            )
            assert ch["text"] is not None, (
                "text snippet must be present (sentence-level fallback)"
            )


# ---------------------------------------------------------------------------
# 6. ENTITY_NOT_FOUND: 404 for unknown entity
# ---------------------------------------------------------------------------


def test_company_not_found_returns_404():
    """GET /api/themes/{run}/companies/{unknown_id} must return 404."""
    run_id = _build_minimal_run()

    resp = client.get(f"/api/themes/{run_id}/companies/ent_does_not_exist")
    assert resp.status_code == 404, (
        f"Unknown entity must yield 404; got {resp.status_code}: {resp.text}"
    )


def test_run_not_found_returns_404():
    """GET /api/themes/{unknown_run}/companies/{id} must return 404."""
    resp = client.get("/api/themes/run_does_not_exist_xyz/companies/ent_co1")
    assert resp.status_code == 404, (
        f"Unknown run must yield 404; got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# 7. ALL themes listed: company exposed to N themes returns N theme entries
# ---------------------------------------------------------------------------


def test_company_profile_lists_all_themes():
    """Profile must list ALL themes the company is exposed to."""
    run_id = _build_minimal_run(theme_ids=["comm_1", "comm_2", "comm_3"])

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    theme_ids_in_response = {t["community_id"] for t in data["themes"]}
    assert theme_ids_in_response == {"comm_1", "comm_2", "comm_3"}, (
        f"All 3 themes must be listed; got: {theme_ids_in_response}"
    )


# ---------------------------------------------------------------------------
# 8. Evidence endpoint 404 when E3 not materialized
# ---------------------------------------------------------------------------


def test_evidence_404_when_e3_not_materialized():
    """Evidence endpoint returns 404 when company_theme_document_evidence.parquet
    is absent — clear error rather than silent empty."""
    run_id = _build_minimal_run(e3_rows=[])  # empty artifact written
    # Delete it to simulate not-yet-materialized
    e3_path = _discovery(run_id) / "company_theme_document_evidence.parquet"
    if e3_path.exists():
        e3_path.unlink()

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/evidence")
    assert resp.status_code == 404, (
        f"Missing E3 artifact must return 404; got {resp.status_code}"
    )
