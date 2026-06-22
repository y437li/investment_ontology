"""End-to-end L1 contract test: import -> clean -> chunk.

Asserts:
  (a) cleaned documents.parquet exists with the io_contracts section 6 columns,
  (b) document_cleaning_log.parquet records quarantine reasons,
  (c) chunks inherit available_at and link to document_id (io_contracts sec 8),
  (d) a document whose available_at is after the run as_of_date is quarantined
      by the cleaning stage (point-in-time guard).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from theme_engine import data_cleaning
from theme_engine.config import settings
from theme_engine.data_cleaning import DOCUMENTS_COLUMNS, CLEANING_LOG_COLUMNS
from theme_engine.chunking import CHUNKS_COLUMNS
from theme_engine.main import app

client = TestClient(app)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cleaning"


def _create_run(as_of_date: str) -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_import_clean_chunk_end_to_end():
    run_id = _create_run("2024-06-30")

    # --- Import.
    resp = client.post(
        "/api/data/import",
        json={
            "run_id": run_id,
            "documents_dir": str(FIXTURES),
            "source_manifest_path": str(FIXTURES / "source_manifest.csv"),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["raw_documents"] == 2

    # --- Clean.
    resp = client.post(
        "/api/data/clean",
        json={"run_id": run_id, "documents_dir": str(FIXTURES)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["included_documents"] == 2
    assert set(body["artifacts"]) == {
        "discovery/documents.parquet",
        "discovery/document_cleaning_log.parquet",
    }

    run_dir = Path(settings.run_output_dir) / run_id
    docs_path = run_dir / "discovery" / "documents.parquet"
    log_path = run_dir / "discovery" / "document_cleaning_log.parquet"
    assert docs_path.exists()
    assert log_path.exists()

    # (a) cleaned documents exist with EXACT contract columns.
    docs_table = pq.read_table(docs_path)
    assert docs_table.column_names == DOCUMENTS_COLUMNS
    docs = docs_table.to_pylist()
    assert len(docs) == 2
    for d in docs:
        assert d["raw_document_id"]  # links back to raw
        assert d["available_at"]
        assert d["content_hash"] == d["clean_content_hash"]
        assert d["included_in_discovery"] is True

    # Cleaning must preserve meaning, not summarize: cleaned text still
    # contains source tokens, and page-number boilerplate is stripped.
    clean_text = (run_dir / docs[0]["clean_text_path"]).read_text(encoding="utf-8")
    assert "widgets" in clean_text or "demand" in clean_text
    assert "\r" not in clean_text

    # Log has the contract columns and at least one applied normalize action.
    log_table = pq.read_table(log_path)
    assert log_table.column_names == CLEANING_LOG_COLUMNS
    log = log_table.to_pylist()
    assert any(r["action_type"] == "normalize" for r in log)

    # --- Chunk.
    resp = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    cbody = resp.json()
    assert cbody["success"] is True
    assert cbody["artifacts"] == ["discovery/chunks.parquet"]
    assert cbody["chunk_count"] >= 2

    chunks_path = run_dir / "discovery" / "chunks.parquet"
    assert chunks_path.exists()
    chunks_table = pq.read_table(chunks_path)
    assert chunks_table.column_names == CHUNKS_COLUMNS
    chunks = chunks_table.to_pylist()
    assert len(chunks) == cbody["chunk_count"]

    # (c) chunks link to a real document_id and inherit available_at.
    doc_by_id = {d["document_id"]: d for d in docs}
    for ch in chunks:
        assert ch["document_id"] in doc_by_id
        assert ch["text"]
        assert ch["available_at"] == doc_by_id[ch["document_id"]]["available_at"]
        assert ch["chunk_index"] is not None

    # Stable chunk ids: re-running chunk yields identical ids.
    first_ids = [c["chunk_id"] for c in chunks]
    resp2 = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp2.status_code == 200
    chunks2 = pq.read_table(chunks_path).to_pylist()
    assert [c["chunk_id"] for c in chunks2] == first_ids


def _write_raw_documents(run_id: str, rows: list[dict]) -> None:
    run_dir = Path(settings.run_output_dir) / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())
    table = pa.Table.from_pydict({c: [r[c] for r in rows] for c in columns})
    pq.write_table(table, discovery / "raw_documents.parquet")


def test_cleaning_quarantines_future_and_missing_metadata():
    """(b) + (d): cleaning stage quarantines a future document (PIT) and a
    document missing required metadata, each with a logged reason."""
    run_id = _create_run("2024-06-30")

    acme_path = str(FIXTURES / "acme_10k.txt")
    rows = [
        {
            "source": "sec",
            "source_id": "ok-1",
            "title": "Acme",
            "document_type": "10-k",
            "company_id": "ACME",
            "raw_path": acme_path,
            "published_at": "2024-01-15",
            "available_at": "2024-01-20",
            "vintage": "v1",
            "language": "en",
            "source_url": "https://example.com/acme",
            "license": "public",
            "confidentiality": "public",
            "notes": "",
            "document_id": "ok-1",
            "content_hash": "hash-ok-1",
            "ingested_at": "2024-03-01T00:00:00Z",
        },
        {
            # FUTURE document: available_at after as_of_date -> quarantine (PIT).
            "source": "sec",
            "source_id": "future-1",
            "title": "Future Filing",
            "document_type": "10-k",
            "company_id": "FUT",
            "raw_path": acme_path,
            "published_at": "2024-12-01",
            "available_at": "2024-12-31",
            "vintage": "v1",
            "language": "en",
            "source_url": "https://example.com/fut",
            "license": "public",
            "confidentiality": "public",
            "notes": "",
            "document_id": "future-1",
            "content_hash": "hash-future-1",
            "ingested_at": "2024-03-01T00:00:00Z",
        },
        {
            # MISSING metadata: no available_at -> quarantine, do NOT infer.
            "source": "sec",
            "source_id": "missing-1",
            "title": "Missing Meta",
            "document_type": "10-k",
            "company_id": "MIS",
            "raw_path": acme_path,
            "published_at": "2024-01-01",
            "available_at": "",
            "vintage": "v1",
            "language": "en",
            "source_url": "https://example.com/mis",
            "license": "public",
            "confidentiality": "public",
            "notes": "",
            "document_id": "missing-1",
            "content_hash": "hash-missing-1",
            "ingested_at": "2024-03-01T00:00:00Z",
        },
    ]
    _write_raw_documents(run_id, rows)

    included, quarantined, reasons = data_cleaning.clean_documents(run_id)
    assert included == 1
    assert quarantined == 2
    joined = " | ".join(reasons)
    assert "future-1" in joined
    assert "after run as_of_date" in joined
    assert "missing-1" in joined

    run_dir = Path(settings.run_output_dir) / run_id
    log = pq.read_table(run_dir / "discovery" / "document_cleaning_log.parquet").to_pylist()

    quarantine_rows = [r for r in log if r["status"] == "quarantined"]
    assert len(quarantine_rows) == 2
    for r in quarantine_rows:
        # Every quarantined record must carry a reason (io_contracts sec 7).
        assert r["warning_code"]
        assert r["warning_message"]
        assert r["document_id"] is None

    codes = {r["warning_code"] for r in quarantine_rows}
    assert "future_document" in codes
    assert "missing_metadata" in codes

    # The only included doc must satisfy available_at <= as_of_date.
    docs = pq.read_table(run_dir / "discovery" / "documents.parquet").to_pylist()
    assert len(docs) == 1
    assert docs[0]["available_at"] <= "2024-06-30"
