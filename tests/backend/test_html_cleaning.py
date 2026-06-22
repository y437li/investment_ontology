"""Tests for HTML/SGML -> text extraction in the cleaning stage.

Covers Bug #26: EDGAR .htm filings were passed through verbatim, polluting
downstream extraction with raw markup like '<DOCUMENT>', '<TYPE>6-K', etc.

Assertions:
  (a) cleaned document text and resulting chunks contain NO '<' tag markup,
  (b) readable body text survives (meaning preserved),
  (c) determinism: clean twice -> identical result,
  (d) existing non-HTML behaviour is unaffected.
"""

from __future__ import annotations

import re
from pathlib import Path

import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from theme_engine import data_cleaning
from theme_engine.data_cleaning import (
    DOCUMENTS_COLUMNS,
    CLEANING_LOG_COLUMNS,
    RULE_HTML_EXTRACT,
    _is_html_sgml,
    _extract_html_text,
    _clean_text,
)
from theme_engine.chunking import CHUNKS_COLUMNS
from theme_engine.config import settings
from theme_engine.main import app

client = TestClient(app)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cleaning"
HTML_FIXTURE = FIXTURES / "globex_6k.htm"

# Regex to detect any HTML/SGML tag in the cleaned text.
_TAG_RE = re.compile(r"<[^>]+>")


def _create_run(as_of_date: str) -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _write_raw_documents(run_id: str, rows: list[dict]) -> None:
    import pyarrow as pa

    run_dir = Path(settings.run_output_dir) / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())
    table = pa.Table.from_pydict({c: [r[c] for r in rows] for c in columns})
    pq.write_table(table, discovery / "raw_documents.parquet")


# ---------------------------------------------------------------------------
# Unit tests for the helper functions
# ---------------------------------------------------------------------------


def test_is_html_sgml_by_extension():
    """Extension .htm or .html triggers HTML mode regardless of content."""
    assert _is_html_sgml("plain text", "report.htm") is True
    assert _is_html_sgml("plain text", "report.html") is True
    assert _is_html_sgml("plain text", "report.HTML") is True
    assert _is_html_sgml("plain text", "report.txt") is False


def test_is_html_sgml_by_content_sniff():
    """Content sniff triggers HTML mode for EDGAR SGML and HTML leading tokens."""
    assert _is_html_sgml("<DOCUMENT>\n<TYPE>6-K\n") is True
    assert _is_html_sgml("<html><head>") is True
    assert _is_html_sgml("<!DOCTYPE html>") is True
    assert _is_html_sgml("<?xml version='1.0'?>") is True
    # Plain text should NOT trigger.
    assert _is_html_sgml("Just a normal document.\nWith text.") is False


def test_extract_html_text_strips_tags():
    """Extracted text contains no HTML/SGML tag markup."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    extracted = _extract_html_text(raw)
    assert not _TAG_RE.search(extracted), (
        f"Tag markup leaked into extracted text: {_TAG_RE.search(extracted).group()!r}"
    )


def test_extract_html_text_preserves_body_content():
    """Meaningful body text survives extraction."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    extracted = _extract_html_text(raw)
    # Key phrases from the fixture body must be present.
    assert "Globex" in extracted
    assert "energy storage" in extracted
    assert "renewable grid" in extracted
    assert "18%" in extracted
    assert "lithium carbonate" in extracted
    assert "14.2%" in extracted


def test_extract_html_text_strips_script_style():
    """Content inside <script> and <style> blocks does not appear in output."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    extracted = _extract_html_text(raw)
    # From the <style> block.
    assert "font-family" not in extracted
    # From the <script> block.
    assert "_tracking" not in extracted


def test_extract_html_text_strips_edgar_sgml_wrapper():
    """EDGAR SGML wrapper tags do not appear in extracted text."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    extracted = _extract_html_text(raw)
    assert "<DOCUMENT>" not in extracted
    assert "<TYPE>" not in extracted
    assert "<SEQUENCE>" not in extracted
    assert "<FILENAME>" not in extracted
    assert "</DOCUMENT>" not in extracted


def test_extract_html_text_decodes_entities():
    """HTML entities are decoded to their Unicode equivalents."""
    snippet = "<p>Revenue &amp; profit &ndash; Q1&nbsp;2024 &copy; Corp.</p>"
    result = _extract_html_text(snippet)
    assert "&amp;" not in result
    assert "&ndash;" not in result
    assert "&nbsp;" not in result
    # Decoded characters should be present.
    assert "Revenue" in result
    assert "profit" in result


def test_clean_text_html_deterministic():
    """Calling _clean_text on an HTML file twice yields identical results."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    result1, actions1 = _clean_text(raw, raw_path=str(HTML_FIXTURE))
    result2, actions2 = _clean_text(raw, raw_path=str(HTML_FIXTURE))
    assert result1 == result2
    assert len(actions1) == len(actions2)
    for a1, a2 in zip(actions1, actions2):
        assert a1["rule_id"] == a2["rule_id"]
        assert a1["after"] == a2["after"]


def test_clean_text_html_logs_html_extract_action():
    """_clean_text records an html_extract action for HTML input."""
    raw = HTML_FIXTURE.read_text(encoding="utf-8")
    _, actions = _clean_text(raw, raw_path=str(HTML_FIXTURE))
    rule_ids = [a["rule_id"] for a in actions]
    assert RULE_HTML_EXTRACT in rule_ids, (
        f"Expected rule_id '{RULE_HTML_EXTRACT}' in cleaning actions; got {rule_ids}"
    )
    html_action = next(a for a in actions if a["rule_id"] == RULE_HTML_EXTRACT)
    assert html_action["action_type"] == "extract"
    assert html_action["cleaning_step"] == "html_extract"


# ---------------------------------------------------------------------------
# Integration tests via the full clean -> chunk pipeline
# ---------------------------------------------------------------------------


def test_html_filing_clean_no_markup():
    """End-to-end: cleaned text for an HTML filing contains no tag markup."""
    run_id = _create_run("2024-06-30")
    html_path = str(HTML_FIXTURE)
    rows = [
        {
            "source": "sec",
            "source_id": "globex-6k",
            "title": "Globex 6-K",
            "document_type": "6-k",
            "company_id": "GLOBEX",
            "raw_path": html_path,
            "published_at": "2024-03-15",
            "available_at": "2024-03-20",
            "vintage": "v1",
            "language": "en",
            "source_url": "https://example.com/globex",
            "license": "public",
            "confidentiality": "public",
            "notes": "",
            "document_id": "globex-6k",
            "content_hash": "hash-globex",
            "ingested_at": "2024-03-21T00:00:00Z",
        }
    ]
    _write_raw_documents(run_id, rows)

    included, quarantined, _ = data_cleaning.clean_documents(run_id)
    assert included == 1
    assert quarantined == 0

    run_dir = Path(settings.run_output_dir) / run_id
    docs = pq.read_table(run_dir / "discovery" / "documents.parquet").to_pylist()
    assert len(docs) == 1

    clean_text = (run_dir / docs[0]["clean_text_path"]).read_text(encoding="utf-8")

    # (a) No tag markup in the cleaned text.
    assert not _TAG_RE.search(clean_text), (
        f"Tag markup in cleaned text: {_TAG_RE.search(clean_text).group()!r}"
    )

    # (b) Readable body text survived.
    assert "Globex" in clean_text
    assert "energy storage" in clean_text

    # (c) Determinism: clean again -> identical result.
    data_cleaning.clean_documents(run_id)
    docs2 = pq.read_table(run_dir / "discovery" / "documents.parquet").to_pylist()
    clean_text2 = (run_dir / docs2[0]["clean_text_path"]).read_text(encoding="utf-8")
    assert clean_text == clean_text2

    # Check the cleaning log records the html_extract action.
    log = pq.read_table(run_dir / "discovery" / "document_cleaning_log.parquet").to_pylist()
    assert log_table_has_rule(log, RULE_HTML_EXTRACT)


def log_table_has_rule(log: list[dict], rule_id: str) -> bool:
    return any(r["rule_id"] == rule_id for r in log)


def test_html_filing_chunks_no_markup():
    """End-to-end: chunks produced from an HTML filing contain no tag markup."""
    run_id = _create_run("2024-06-30")
    html_path = str(HTML_FIXTURE)
    rows = [
        {
            "source": "sec",
            "source_id": "globex-6k-chunk",
            "title": "Globex 6-K Chunk Test",
            "document_type": "6-k",
            "company_id": "GLOBEX",
            "raw_path": html_path,
            "published_at": "2024-03-15",
            "available_at": "2024-03-20",
            "vintage": "v1",
            "language": "en",
            "source_url": "https://example.com/globex2",
            "license": "public",
            "confidentiality": "public",
            "notes": "",
            "document_id": "globex-6k-chunk",
            "content_hash": "hash-globex-chunk",
            "ingested_at": "2024-03-21T00:00:00Z",
        }
    ]
    _write_raw_documents(run_id, rows)
    data_cleaning.clean_documents(run_id)

    resp = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    run_dir = Path(settings.run_output_dir) / run_id
    chunks = pq.read_table(run_dir / "discovery" / "chunks.parquet").to_pylist()
    assert len(chunks) >= 1

    for chunk in chunks:
        text = chunk["text"]
        assert text, "Chunk text should not be empty"
        # (a) No tag markup in any chunk.
        assert not _TAG_RE.search(text), (
            f"Tag markup in chunk text: {_TAG_RE.search(text).group()!r}"
        )


# ---------------------------------------------------------------------------
# (d) Non-HTML behaviour unchanged
# ---------------------------------------------------------------------------


def test_non_html_behaviour_unchanged():
    """Plain-text documents are unaffected: no html_extract action is logged."""
    txt_path = str(FIXTURES / "acme_10k.txt")
    raw = Path(txt_path).read_text(encoding="utf-8")
    _, actions = _clean_text(raw, raw_path=txt_path)
    rule_ids = [a["rule_id"] for a in actions]
    assert RULE_HTML_EXTRACT not in rule_ids, (
        f"html_extract action was recorded for a plain-text file: {rule_ids}"
    )
    # Meaningful content still present after cleaning.
    final_text = actions[-1]["after"] if actions else raw
    assert "widgets" in final_text or "Acme" in final_text
