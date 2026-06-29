"""Full-text source for an evidence chunk.

Given a chunk_id (the evidence handle carried on every relationship), return the
full chunk text PLUS the whole source document (its chunks joined in order) and
attribution (title / source / url / date). Lets the UI read the full text behind
an evidence snippet instead of only the cited sentence. Read-only, deterministic.
"""

from __future__ import annotations

import pyarrow.parquet as pq

from . import runs


def _load(run_id: str, name: str, as_of: str | None = None) -> list[dict]:
    p = runs.discovery_point_dir(run_id, as_of) / name
    if not p.exists():
        return []
    return pq.read_table(p).to_pylist()


def chunk_source(run_id: str, chunk_id: str, as_of: str | None = None) -> dict:
    """Full chunk + full source document + attribution for an evidence chunk."""
    chunks = _load(run_id, "chunks.parquet", as_of)
    ch = next((c for c in chunks if c.get("chunk_id") == chunk_id), None)
    if ch is None:
        raise ValueError(f"chunk not found: {chunk_id}")

    doc_id = ch.get("document_id")
    # Full document text = this document's chunks, in order.
    doc_chunks = sorted(
        (c for c in chunks if c.get("document_id") == doc_id),
        key=lambda c: c.get("chunk_index") if c.get("chunk_index") is not None else 0,
    )
    document_text = "\n".join(c.get("text", "") for c in doc_chunks)

    doc = next((d for d in _load(run_id, "documents.parquet", as_of) if d.get("document_id") == doc_id), {})
    raw_id = ch.get("raw_document_id") or doc.get("raw_document_id")
    raw = next((r for r in _load(run_id, "raw_documents.parquet", as_of) if r.get("document_id") == raw_id), {})

    return {
        "chunk_id": chunk_id,
        "chunk_text": ch.get("text", ""),
        "document_id": doc_id,
        "available_at": ch.get("available_at"),
        "section_title": ch.get("section_title"),
        "document": {
            "title": doc.get("title") or raw.get("title"),
            "source": doc.get("source") or raw.get("source"),
            "source_url": raw.get("source_url"),
            "published_at": doc.get("published_at") or raw.get("published_at"),
            "document_type": doc.get("document_type") or raw.get("document_type"),
        },
        "document_text": document_text,
    }
