"""Document chunking service (L1).

Reads ``discovery/documents.parquet`` and writes ``discovery/chunks.parquet``
per io_contracts.md section 8.

Each chunk:
  - links to its ``document_id`` (and ``raw_document_id``),
  - carries a ``chunk_index`` and cleaned ``text``,
  - INHERITS ``available_at`` from its document (point-in-time),
  - has a stable ``chunk_id`` for the same input text + chunking config,
  - carries a ``block_type`` (prose | table | heading) on every chunk,
  - carries a ``table_data`` JSON string for table blocks (null otherwise),
  - carries a ``section_title`` derived from headings and EDGAR section patterns.

Chunking is deterministic: fixed character window + overlap from the config
constants below. Same cleaned text + same config => identical chunks and ids.

Structure-preserving additions (EG-A):
- ``[[[TABLE_DATA:{json}]]]`` markers (written by data_cleaning) become single
  ``block_type="table"`` chunks carrying the normalized cell grid.
- ``[[[SECTION_TITLE:text]]]`` markers (from HTML heading tags) update the
  running section context without emitting separate chunks.
- Plain-text EDGAR section headings ("Item N." / MD&A / all-caps patterns)
  are detected line-by-line and also update the section context.
- Prose blocks are chunked with the same sentence/paragraph-aware algorithm
  as PR #88 — behavior is UNCHANGED for prose.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs
from .data_cleaning import (
    TABLE_MARKER_PREFIX,
    TABLE_MARKER_SUFFIX,
    SECTION_MARKER_PREFIX,
    SECTION_MARKER_SUFFIX,
)


SCHEMA_VERSION = "1.0"
CLEANING_VERSION = "clean_v1"

# Deterministic chunking config (constants -> stable chunk ids).
CHUNK_SIZE_CHARS = 800
CHUNK_OVERLAP_CHARS = 100
# v2: sentence/paragraph-aware packing (never cut mid-sentence) -> cleaner evidence.
# EG-A does NOT change the config ID so prose-only chunk_ids remain stable.
CHUNK_CONFIG_ID = f"chunk_v2_sent_size{CHUNK_SIZE_CHARS}_ov{CHUNK_OVERLAP_CHARS}"

# Sentence/paragraph boundary: end punctuation followed by whitespace, or a blank line.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")

# io_contracts.md section 8: chunks.parquet
# EG-A additions: block_type, table_data (additive; existing readers unaffected).
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
    "block_type",
    "table_data",
    "available_at",
    "content_hash",
    "cleaning_version",
]

# ---------------------------------------------------------------------------
# Section-heading detection patterns (plain-text EDGAR filings)
# ---------------------------------------------------------------------------

# EDGAR "Item N." headings (e.g. "Item 1.", "Item 1A.", "Item 7.")
_ITEM_HEADING_RE = re.compile(
    r"^\s*(Item\s+\d+[A-Za-z]*\..*)",
    re.IGNORECASE,
)
# MD&A and common SEC filing part/section labels (any mix of case).
_MDAT_HEADING_RE = re.compile(
    r"^\s*((?:MANAGEMENT['']?S?\s+DISCUSSION|MD&A|PART\s+[IVX]+\.?|"
    r"RISK\s+FACTORS|SELECTED\s+FINANCIAL|QUANTITATIVE\s+AND\s+QUALITATIVE|"
    r"CRITICAL\s+ACCOUNTING)\b.*)",
    re.IGNORECASE,
)
# Short all-caps heading lines (4–80 chars, only word characters and spaces).
_ALLCAPS_HEADING_RE = re.compile(r"^\s*([A-Z][A-Z\s]{3,79})\s*$")

# Regex matching the structured-block markers we parse here.
# Markers use '[[[' / ']]]' (triple square brackets, escaped in regex).
_TABLE_MARKER_RE = re.compile(
    r"\[\[\[TABLE_DATA:(.*?)\]\]\]",
    re.DOTALL,
)
_SECTION_MARKER_RE = re.compile(
    r"\[\[\[SECTION_TITLE:(.*?)\]\]\]",
)


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


# ---------------------------------------------------------------------------
# Structure-aware text parser
# ---------------------------------------------------------------------------

def _is_section_heading_line(line: str) -> str | None:
    """Return the heading text if the line looks like a section heading,
    else None.  Only applied to plain-text content (after markers are
    consumed); HTML headings arrive via [[[SECTION_TITLE:...]]] markers.
    """
    stripped = line.strip()
    if not stripped:
        return None
    m = _ITEM_HEADING_RE.match(stripped)
    if m:
        return m.group(1).strip()
    m = _MDAT_HEADING_RE.match(stripped)
    if m:
        return m.group(1).strip()
    # All-caps short headings (avoid tagging long prose as headings).
    if len(stripped) <= 80:
        m = _ALLCAPS_HEADING_RE.match(stripped)
        if m:
            # Avoid false-positive: must have at least 2 words OR ≤5 chars (acronym).
            words = stripped.split()
            if len(words) >= 2 or len(stripped) <= 5:
                return stripped
    return None


def _parse_structured_blocks(text: str) -> list[dict]:
    """Split the cleaned text into typed blocks:

    Each block is a dict with keys:
    - ``type``: "table" | "prose"
    - ``text``: the original marker string (for tables) or prose text
    - ``rows``: list of row lists (for table blocks only)
    - ``section_title``: the current section heading at the start of the block
      (updated as we scan; prose blocks inherit this from prior headings).

    Section markers (``[[[SECTION_TITLE:...]]]``) are consumed silently to
    update the running section title without producing a separate block.
    Plain-text heading lines in prose blocks are also tracked and update the
    title for the NEXT prose block.
    """
    blocks: list[dict] = []
    current_section: str | None = None

    # Split on table and section markers — but capture the delimiters.
    # Pattern: split on [[[TABLE_DATA:...]]] OR [[[SECTION_TITLE:...]]]
    splitter = re.compile(
        r"(\[\[\[TABLE_DATA:.*?\]\]\]|\[\[\[SECTION_TITLE:.*?\]\]\])",
        re.DOTALL,
    )
    segments = splitter.split(text)

    for seg in segments:
        seg_stripped = seg.strip()
        if not seg_stripped:
            continue

        # --- Table marker ---
        tm = _TABLE_MARKER_RE.fullmatch(seg_stripped)
        if tm:
            try:
                payload = json.loads(tm.group(1))
                rows = payload.get("rows", [])
            except (json.JSONDecodeError, AttributeError):
                rows = []
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "text": seg_stripped,
                        "rows": rows,
                        "section_title": current_section,
                    }
                )
            continue

        # --- Section-title marker (from HTML heading tags) ---
        sm = _SECTION_MARKER_RE.fullmatch(seg_stripped)
        if sm:
            title = sm.group(1).strip()
            if title:
                current_section = title
            continue

        # --- Prose segment: scan for in-text section headings ---
        # We do NOT split the prose so that _split_text() later sees the
        # same input as PR #88 (regression guard).  Instead we scan for
        # headings and:
        # 1. Track the FIRST heading found in the block so the block gets a
        #    non-null section_title when it contains an "Item N." line.
        # 2. Update current_section so subsequent blocks inherit the latest
        #    heading seen.
        prose_section_at_start = current_section
        first_heading_in_block: str | None = None
        lines = seg.split("\n")
        for line in lines:
            heading = _is_section_heading_line(line)
            if heading:
                if first_heading_in_block is None:
                    first_heading_in_block = heading
                current_section = heading

        if seg.strip():
            # Use the first in-block heading if the block has one; otherwise
            # fall through to the inherited context from before this block.
            section_for_block = first_heading_in_block or prose_section_at_start
            blocks.append(
                {
                    "type": "prose",
                    "text": seg,
                    "rows": None,
                    "section_title": section_for_block,
                }
            )

    return blocks


# ---------------------------------------------------------------------------
# Parquet helpers
# ---------------------------------------------------------------------------

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

        # --- Parse into typed blocks (prose + table) ----------------------
        blocks = _parse_structured_blocks(clean_text)
        chunk_index = 0

        for block in blocks:
            block_type = block["type"]
            section_title = block["section_title"]

            if block_type == "table":
                # Each table is a single atomic chunk.
                rows = block["rows"]
                table_json = json.dumps({"rows": rows}, ensure_ascii=False)
                # Render a human-readable text representation for the chunk.
                text_repr = _table_to_text(rows)
                if not text_repr.strip():
                    continue
                chunk_rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "run_id": run_id,
                        "chunk_id": _stable_chunk_id(doc_content_hash, chunk_index),
                        "document_id": document_id,
                        "raw_document_id": raw_document_id,
                        "chunk_index": chunk_index,
                        "text": text_repr,
                        "token_count": len(text_repr.split()),
                        "start_char": None,
                        "end_char": None,
                        "page_start": None,
                        "page_end": None,
                        "section_title": section_title,
                        "block_type": "table",
                        "table_data": table_json,
                        "available_at": available_at,
                        "content_hash": _sha256_text(text_repr),
                        "cleaning_version": doc.get("cleaning_version") or CLEANING_VERSION,
                    }
                )
                chunk_index += 1

            else:
                # Prose block: use sentence-aware chunking (PR #88 behavior).
                prose_text = block["text"]
                for start_char, end_char, text in _split_text(prose_text):
                    if not text.strip():
                        continue
                    # Determine section_title for this sub-chunk: use the
                    # block-level title (it already reflects the most recent
                    # heading seen before this block).
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
                            "section_title": section_title,
                            "block_type": "prose",
                            "table_data": None,
                            "available_at": available_at,
                            "content_hash": _sha256_text(text),
                            "cleaning_version": doc.get("cleaning_version") or CLEANING_VERSION,
                        }
                    )
                    chunk_index += 1

    if not chunk_rows:
        pq.write_table(_empty_table(), out_path)
        return 0

    pydict = {col: [row.get(col) for row in chunk_rows] for col in CHUNKS_COLUMNS}
    pq.write_table(pa.Table.from_pydict(pydict), out_path)
    return len(chunk_rows)


def _table_to_text(rows: list[list[str]]) -> str:
    """Render a cell grid as a pipe-delimited text table.

    This is the human-readable ``text`` field for a table chunk.  Numbers
    stay attached to their labels (e.g. ``Revenue | $100 | $90``) because
    we never split across rows.
    """
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows:
        lines.append(" | ".join(str(c) for c in row))
    return "\n".join(lines)
