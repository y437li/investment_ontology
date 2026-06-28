"""Tests for SENT-D: management-sentiment panel endpoint (GitHub #102).

Acceptance criteria:
1. SENTIMENT_AVAILABLE: GET /api/themes/{run}/companies/{id}/sentiment returns
   available=True and a populated readings list when fused artifact has rows
   for the company.

2. SENTIMENT_UNAVAILABLE: When fused artifact is absent, returns available=False
   with a non-empty message — never a 404, never silent blank.

3. NO_ROWS: When artifact exists but no rows for this company, returns
   available=False with an explicit message.

4. PIT: rows with available_at > as_of_date are excluded; rows at or before
   are included.

5. AGREEMENT_FIELDS: each reading carries fused_tone, agreement (agree|hedged|conflict),
   fused_tone_severity, agreement_severity, and agreement_label.

6. CONFLICT_HEDGED_FLAGS: fused_tone_summary.has_conflict=True when at least one
   conflict reading is present; has_hedged=True for hedged; these must not be
   hidden when positive readings also exist.

7. CHUNK_RESOLVED: each reading with a known evidence_chunk_id has chunk_text
   populated from chunks.parquet.

8. SOURCE_LINK: each reading carries evidence_chunk_id so the UI can call
   "read full source" via the existing /api/themes/{run}/chunks/{id} route.

9. RUN_NOT_FOUND: 404 when run does not exist.
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
# Helpers
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


def _write_chunks(ddir: Path, rows: list[dict]) -> None:
    ddir.mkdir(parents=True, exist_ok=True)
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
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), ddir / "chunks.parquet")


def _write_documents(ddir: Path, rows: list[dict]) -> None:
    ddir.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("document_id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("source", pa.string()),
        pa.field("document_type", pa.string()),
        pa.field("published_at", pa.string()),
        pa.field("available_at", pa.string()),
    ])
    pydict: dict[str, list] = {col: [] for col in schema.names}
    for row in rows:
        for col in schema.names:
            pydict[col].append(row.get(col))
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema), ddir / "documents.parquet")


def _write_fused(ddir: Path, rows: list[dict]) -> None:
    """Write management_sentiment_fused.parquet (SENT-C schema)."""
    ddir.mkdir(parents=True, exist_ok=True)

    from theme_engine.sentiment_fusion import MANAGEMENT_SENTIMENT_FUSED_COLUMNS

    field_map: dict[str, pa.DataType] = {
        "schema_version": pa.string(),
        "fusion_id": pa.string(),
        "sentiment_id": pa.string(),
        "company_id": pa.string(),
        "speaker_role": pa.string(),
        "direction": pa.string(),
        "confidence_tone": pa.string(),
        "hedging": pa.bool_(),
        "forward_stance": pa.string(),
        "evidence_chunk_id": pa.string(),
        "lexicon_hits": pa.string(),
        "tone_positive": pa.float64(),
        "tone_negative": pa.float64(),
        "tone_uncertainty": pa.float64(),
        "tone_litigious": pa.float64(),
        "tone_strong_modal": pa.float64(),
        "tone_weak_modal": pa.float64(),
        "lm_direction": pa.string(),
        "fused_tone": pa.string(),
        "agreement": pa.string(),
        "fused_confidence": pa.float64(),
        "available_at": pa.string(),
        "created_at": pa.string(),
    }

    cols = MANAGEMENT_SENTIMENT_FUSED_COLUMNS
    schema = pa.schema([(c, field_map[c]) for c in cols])
    pydict: dict[str, list] = {col: [] for col in cols}
    for row in rows:
        for col in cols:
            v = row.get(col)
            if v is None:
                if field_map[col] == pa.bool_():
                    v = False
                elif field_map[col] == pa.float64():
                    v = 0.0
                else:
                    v = ""
            pydict[col].append(v)
    pq.write_table(pa.Table.from_pydict(pydict, schema=schema),
                   ddir / "management_sentiment_fused.parquet")


def _make_fused_row(
    company_id: str = "ent_co1",
    evidence_chunk_id: str = "chunk_001",
    fused_tone: str = "positive",
    agreement: str = "agree",
    available_at: str = "2024-06-01",
    direction: str = "positive",
    fused_confidence: float = 0.85,
    **kwargs,
) -> dict:
    row = {
        "schema_version": "1.0",
        "fusion_id": f"fusion_{uuid.uuid4().hex[:16]}",
        "sentiment_id": f"sent_{uuid.uuid4().hex[:8]}",
        "company_id": company_id,
        "speaker_role": "management",
        "direction": direction,
        "confidence_tone": "high",
        "hedging": False,
        "forward_stance": "optimistic",
        "evidence_chunk_id": evidence_chunk_id,
        "lexicon_hits": '{"positive": ["strong", "grew"], "negative": []}',
        "tone_positive": 0.12,
        "tone_negative": 0.01,
        "tone_uncertainty": 0.02,
        "tone_litigious": 0.0,
        "tone_strong_modal": 0.0,
        "tone_weak_modal": 0.0,
        "lm_direction": "positive",
        "fused_tone": fused_tone,
        "agreement": agreement,
        "fused_confidence": fused_confidence,
        "available_at": available_at,
        "created_at": "2024-12-01T00:00:00Z",
    }
    row.update(kwargs)
    return row


def _build_minimal_run(
    *,
    as_of: str = "2024-12-31",
    company_id: str = "ent_co1",
    fused_rows: list[dict] | None = None,
    chunk_rows: list[dict] | None = None,
    write_fused: bool = True,
) -> str:
    run_id = f"run_sent_{uuid.uuid4().hex[:8]}"
    ddir = _discovery(run_id)
    _make_manifest(run_id, as_of)

    # Default chunk
    if chunk_rows is None:
        chunk_rows = [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "chunk_id": "chunk_001",
                "document_id": "doc_001",
                "raw_document_id": "raw_001",
                "text": "Revenue grew strongly in the quarter, reflecting robust demand.",
                "available_at": "2024-06-01",
                "section_title": "Results of Operations",
                "block_type": "paragraph",
            }
        ]
    _write_chunks(ddir, chunk_rows)

    # Default documents
    _write_documents(ddir, [
        {
            "document_id": "doc_001",
            "title": "Q2 2024 Earnings Call",
            "source": "earnings_call",
            "document_type": "transcript",
            "published_at": "2024-08-01",
            "available_at": "2024-08-01",
        }
    ])

    if write_fused:
        if fused_rows is None:
            fused_rows = [_make_fused_row(company_id=company_id)]
        _write_fused(ddir, fused_rows)

    return run_id


# ---------------------------------------------------------------------------
# 1. SENTIMENT_AVAILABLE
# ---------------------------------------------------------------------------

def test_sentiment_available_basic():
    """GET /api/themes/{run}/companies/{id}/sentiment returns available=True and readings."""
    run_id = _build_minimal_run()

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["company_id"] == "ent_co1"
    assert data["available"] is True
    assert data["message"] is None
    assert data["as_of_date"] == "2024-12-31"

    assert data["fused_tone_summary"] is not None
    assert data["fused_tone_summary"]["reading_count"] == 1
    assert isinstance(data["readings"], list)
    assert len(data["readings"]) == 1


# ---------------------------------------------------------------------------
# 2. SENTIMENT_UNAVAILABLE: artifact absent
# ---------------------------------------------------------------------------

def test_sentiment_unavailable_artifact_absent():
    """When fused artifact is absent, returns available=False with message — not 404."""
    run_id = _build_minimal_run(write_fused=False)

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["available"] is False
    assert data["message"], "message must be non-empty when artifact is absent"
    assert data["readings"] == []
    assert data["fused_tone_summary"] is None


# ---------------------------------------------------------------------------
# 3. NO_ROWS: artifact present but no rows for company
# ---------------------------------------------------------------------------

def test_sentiment_no_rows_for_company():
    """When fused artifact has rows but none for this company, returns available=False."""
    run_id = _build_minimal_run(
        fused_rows=[_make_fused_row(company_id="ent_other_co")]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["available"] is False
    assert data["message"], "message must be set when no rows pass the company filter"
    assert data["readings"] == []


# ---------------------------------------------------------------------------
# 4. PIT: available_at gate
# ---------------------------------------------------------------------------

def test_sentiment_pit_filter():
    """Rows with available_at > as_of are excluded; rows at or before are included."""
    as_of = "2024-06-30"
    run_id = _build_minimal_run(
        as_of=as_of,
        chunk_rows=[
            {
                "schema_version": "1.0",
                "run_id": "dummy",
                "chunk_id": "chunk_early",
                "document_id": "doc_001",
                "raw_document_id": "raw_001",
                "text": "Early chunk text.",
                "available_at": "2024-05-01",
                "section_title": None,
                "block_type": "paragraph",
            },
            {
                "schema_version": "1.0",
                "run_id": "dummy",
                "chunk_id": "chunk_future",
                "document_id": "doc_001",
                "raw_document_id": "raw_001",
                "text": "Future chunk text.",
                "available_at": "2024-08-01",
                "section_title": None,
                "block_type": "paragraph",
            },
        ],
        fused_rows=[
            _make_fused_row(
                evidence_chunk_id="chunk_early",
                available_at="2024-05-01",   # <= as_of: included
            ),
            _make_fused_row(
                evidence_chunk_id="chunk_future",
                available_at="2024-08-01",   # > as_of: excluded
            ),
        ],
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["available"] is True
    assert len(data["readings"]) == 1, (
        f"Only the PIT-clean row (available_at <= {as_of}) should be returned; "
        f"got {len(data['readings'])}"
    )
    assert data["readings"][0]["evidence_chunk_id"] == "chunk_early"


# ---------------------------------------------------------------------------
# 5. AGREEMENT_FIELDS
# ---------------------------------------------------------------------------

def test_reading_has_required_agreement_fields():
    """Each reading must carry fused_tone, agreement, *_severity, *_label fields."""
    run_id = _build_minimal_run(
        fused_rows=[
            _make_fused_row(fused_tone="hedged", agreement="hedged")
        ]
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    reading = resp.json()["readings"][0]

    required = [
        "fused_tone", "fused_tone_label", "fused_tone_severity",
        "agreement", "agreement_label", "agreement_severity",
        "fused_confidence", "evidence_chunk_id",
    ]
    for field in required:
        assert field in reading, f"reading must carry '{field}'"

    assert reading["fused_tone"] == "hedged"
    assert reading["agreement"] == "hedged"
    # Hedged must have a distinct severity — never "positive"
    assert reading["fused_tone_severity"] != "positive", (
        "hedged fused_tone must NOT have severity='positive' — would mislead the UI"
    )
    assert reading["agreement_severity"] != "positive", (
        "hedged agreement must NOT have severity='positive'"
    )


# ---------------------------------------------------------------------------
# 6. CONFLICT_HEDGED_FLAGS visible alongside positive readings
# ---------------------------------------------------------------------------

def test_conflict_and_hedged_flags_not_hidden_by_positive_readings():
    """has_conflict and has_hedged must be True even when positive readings co-exist."""
    run_id = _build_minimal_run(
        chunk_rows=[
            {
                "schema_version": "1.0",
                "run_id": "dummy",
                "chunk_id": f"chunk_{i}",
                "document_id": "doc_001",
                "raw_document_id": "raw_001",
                "text": f"Text {i}.",
                "available_at": "2024-06-01",
                "section_title": None,
                "block_type": "paragraph",
            }
            for i in range(3)
        ],
        fused_rows=[
            _make_fused_row(
                evidence_chunk_id="chunk_0",
                fused_tone="positive", agreement="agree",
            ),
            _make_fused_row(
                evidence_chunk_id="chunk_1",
                fused_tone="hedged", agreement="hedged",
            ),
            _make_fused_row(
                evidence_chunk_id="chunk_2",
                fused_tone="negative", agreement="conflict",
            ),
        ],
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    summary = resp.json()["fused_tone_summary"]

    assert summary["has_conflict"] is True, (
        "has_conflict must be True when at least one conflict reading exists"
    )
    assert summary["has_hedged"] is True, (
        "has_hedged must be True when at least one hedged reading exists"
    )


def test_conflict_readings_sorted_first():
    """Conflict readings must appear before hedged, which appear before agree."""
    run_id = _build_minimal_run(
        chunk_rows=[
            {
                "schema_version": "1.0",
                "run_id": "dummy",
                "chunk_id": f"chunk_{i}",
                "document_id": "doc_001",
                "raw_document_id": "raw_001",
                "text": f"Text {i}.",
                "available_at": "2024-06-01",
                "section_title": None,
                "block_type": "paragraph",
            }
            for i in range(3)
        ],
        fused_rows=[
            _make_fused_row(
                evidence_chunk_id="chunk_0",
                fused_tone="positive", agreement="agree",
            ),
            _make_fused_row(
                evidence_chunk_id="chunk_1",
                fused_tone="hedged", agreement="hedged",
            ),
            _make_fused_row(
                evidence_chunk_id="chunk_2",
                fused_tone="negative", agreement="conflict",
            ),
        ],
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    readings = resp.json()["readings"]

    agreements = [r["agreement"] for r in readings]
    # conflict must come before hedged; hedged before agree
    conflict_idx = agreements.index("conflict")
    hedged_idx = agreements.index("hedged")
    agree_idx = agreements.index("agree")
    assert conflict_idx < hedged_idx < agree_idx, (
        f"Sort order wrong: conflict={conflict_idx} hedged={hedged_idx} agree={agree_idx}"
    )


# ---------------------------------------------------------------------------
# 7. CHUNK_RESOLVED
# ---------------------------------------------------------------------------

def test_chunk_text_resolved_from_chunks_parquet():
    """Reading's chunk_text must come from chunks.parquet for the evidence_chunk_id."""
    run_id = _build_minimal_run(
        chunk_rows=[{
            "schema_version": "1.0",
            "run_id": "dummy",
            "chunk_id": "chunk_001",
            "document_id": "doc_001",
            "raw_document_id": "raw_001",
            "text": "Management expressed strong confidence in long-term growth.",
            "available_at": "2024-06-01",
            "section_title": "CEO Remarks",
            "block_type": "paragraph",
        }],
        fused_rows=[_make_fused_row(evidence_chunk_id="chunk_001")],
    )

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    reading = resp.json()["readings"][0]

    assert reading["chunk_text"] is not None, "chunk_text must be populated when chunk exists"
    assert "confidence" in reading["chunk_text"].lower() or "growth" in reading["chunk_text"].lower(), (
        f"chunk_text must contain text from the chunk; got: {reading['chunk_text']!r}"
    )
    assert reading["section_title"] == "CEO Remarks"


# ---------------------------------------------------------------------------
# 8. SOURCE_LINK: evidence_chunk_id present for "read full source"
# ---------------------------------------------------------------------------

def test_source_link_chunk_id_present():
    """Every reading must carry a non-empty evidence_chunk_id for 'read full source'."""
    run_id = _build_minimal_run()

    resp = client.get(f"/api/themes/{run_id}/companies/ent_co1/sentiment")
    assert resp.status_code == 200, resp.text
    readings = resp.json()["readings"]

    assert readings, "Must have at least one reading"
    for reading in readings:
        assert reading["evidence_chunk_id"], (
            "evidence_chunk_id must be non-empty in every reading "
            "(required for 'read full source' link)"
        )


# ---------------------------------------------------------------------------
# 9. RUN_NOT_FOUND
# ---------------------------------------------------------------------------

def test_run_not_found_returns_404():
    """GET /api/themes/{unknown_run}/companies/{id}/sentiment must return 404."""
    resp = client.get("/api/themes/run_does_not_exist_xyz/companies/ent_co1/sentiment")
    assert resp.status_code == 404, (
        f"Unknown run must yield 404; got {resp.status_code}: {resp.text}"
    )
