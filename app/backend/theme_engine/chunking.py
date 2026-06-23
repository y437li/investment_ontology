"""Document chunking service (L1).

Reads ``discovery/documents.parquet`` and writes ``discovery/chunks.parquet``
per io_contracts.md section 8.

Each chunk:
  - links to its ``document_id`` (and ``raw_document_id``),
  - carries a ``chunk_index`` and cleaned ``text``,
  - INHERITS ``available_at`` from its document (point-in-time),
  - has a stable ``chunk_id`` for the same input text + chunking config.

Chunking is deterministic: fixed character window + overlap from the config
constants below. Same cleaned text + same config => identical chunks and ids.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs


SCHEMA_VERSION = "1.0"
CLEANING_VERSION = "clean_v1"

# Deterministic chunking config (constants -> stable chunk ids).
CHUNK_SIZE_CHARS = 800
CHUNK_OVERLAP_CHARS = 100
# v2: sentence/paragraph-aware packing (never cut mid-sentence) -> cleaner evidence.
CHUNK_CONFIG_ID = f"chunk_v2_sent_size{CHUNK_SIZE_CHARS}_ov{CHUNK_OVERLAP_CHARS}"

# Sentence/paragraph boundary: end punctuation followed by whitespace, or a blank line.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")

# io_contracts.md section 8: chunks.parquet
CHUNKS_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "chunk_id",
    "document_id",
    "raw_document_id",
    "chunk_index",
    "text",
    "token_count",
    "start_char",
    "end_char",
    "page_start",
    "page_end",
    "section_title",
    "available_at",
    "content_hash",
    "cleaning_version",
]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_chunk_id(content_hash: str, chunk_index: int) -> str:
    """Stable for the same document content hash, config, and index."""
    basis = f"{content_hash}:{CHUNK_CONFIG_ID}:{chunk_index}"
    return f"chunk_{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) char spans for each sentence/paragraph, covering the whole text."""
    bounds = [0]
    for m in _BOUNDARY_RE.finditer(text):
        bounds.append(m.end())
    if bounds[-1] != len(text):
        bounds.append(len(text))
    spans = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    return [(s, e) for (s, e) in spans if text[s:e].strip()] or [(0, len(text))]


def _split_text(text: str) -> list[tuple[int, int, str]]:
    """Sentence/paragraph-aware chunking: greedily pack WHOLE sentences up to
    CHUNK_SIZE_CHARS (never cutting mid-sentence) with sentence-granular overlap.
    A single sentence longer than the window is taken whole. Returns (start, end, text).
    Deterministic: same text + config => identical spans and ids."""
    n = len(text)
    if n == 0:
        return []
    sentences = _sentence_spans(text)
    chunks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(sentences):
        start = sentences[i][0]
        end = start
        j = i
        while j < len(sentences) and (sentences[j][1] - start) <= CHUNK_SIZE_CHARS:
            end = sentences[j][1]
            j += 1
        if j == i:  # lone sentence longer than the window -> take it whole
            end = sentences[i][1]
            j = i + 1
        chunks.append((start, end, text[start:end]))
        if j >= len(sentences):
            break
        # overlap: step back whole sentences until ~CHUNK_OVERLAP_CHARS are re-included
        k = j
        while k > i + 1 and (end - sentences[k - 1][0]) < CHUNK_OVERLAP_CHARS:
            k -= 1
        i = max(k, i + 1)  # always make progress
    return chunks


def _read_documents(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "documents.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"documents.parquet not found for run {run_id}; run clean first",
        )
    return pq.read_table(artifact).to_pylist()


def _load_clean_text(run_id: str, clean_text_rel: str | None) -> str:
    if not clean_text_rel:
        return ""
    path = runs.get_run_dir(run_id) / clean_text_rel
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _empty_table() -> pa.Table:
    return pa.table({col: pa.array([], type=pa.string()) for col in CHUNKS_COLUMNS})


def chunk_documents(run_id: str) -> int:
    """Chunk cleaned documents into ``chunks.parquet``. Returns chunk count."""
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    docs = _read_documents(run_id)
    run_dir = runs.get_run_dir(run_id)
    out_path = run_dir / "discovery" / "chunks.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunk_rows: list[dict] = []

    for doc in docs:
        # Only chunk documents included in discovery (point-in-time safe).
        if doc.get("included_in_discovery") is False:
            continue

        document_id = doc.get("document_id")
        raw_document_id = doc.get("raw_document_id")
        available_at = doc.get("available_at")  # inherited, point-in-time
        doc_content_hash = doc.get("content_hash") or ""

        clean_text = _load_clean_text(run_id, doc.get("clean_text_path"))

        for chunk_index, (start_char, end_char, text) in enumerate(_split_text(clean_text)):
            chunk_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "chunk_id": _stable_chunk_id(doc_content_hash, chunk_index),
                    "document_id": document_id,
                    "raw_document_id": raw_document_id,
                    "chunk_index": chunk_index,
                    "text": text,
                    "token_count": len(text.split()),
                    "start_char": start_char,
                    "end_char": end_char,
                    "page_start": None,
                    "page_end": None,
                    "section_title": None,
                    "available_at": available_at,
                    "content_hash": _sha256_text(text),
                    "cleaning_version": doc.get("cleaning_version") or CLEANING_VERSION,
                }
            )

    if not chunk_rows:
        pq.write_table(_empty_table(), out_path)
        return 0

    pydict = {col: [row.get(col) for row in chunk_rows] for col in CHUNKS_COLUMNS}
    pq.write_table(pa.Table.from_pydict(pydict), out_path)
    return len(chunk_rows)
