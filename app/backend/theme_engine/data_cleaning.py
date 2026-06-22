"""Document cleaning service (L0 -> L1).

Reads ``discovery/raw_documents.parquet`` (produced by ``data_import``) and
writes two contract artifacts:

- ``discovery/documents.parquet``           (io_contracts.md section 6)
- ``discovery/document_cleaning_log.parquet`` (io_contracts.md section 7)

Cleaning is deterministic and auditable per ``docs/data_schema.md`` section 3.

Allowed actions (data_schema section 3):
  - normalize line endings and whitespace
  - remove repeated page headers / footers / page numbers by deterministic rules
  - preserve meaning, section titles, page references

Forbidden actions (NOT performed here):
  - summarize, translate, paraphrase, or rewrite meaning
  - infer a missing ``available_at``
  - merge different source documents
  - drop negative / contradictory / low-confidence evidence

Quarantine (logged with a reason): unreadable files, missing metadata,
duplicates, and future documents (``available_at > as_of_date``).
"""

from __future__ import annotations

import hashlib
import html
import html.parser
import re
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from .config import REPO_ROOT
from . import runs


SCHEMA_VERSION = "1.0"
CLEANING_VERSION = "clean_v1"
CLEANING_AGENT = "data_cleaning_agent"

# io_contracts.md section 6: documents.parquet
DOCUMENTS_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "document_id",
    "raw_document_id",
    "source",
    "source_id",
    "title",
    "document_type",
    "company_id",
    "published_at",
    "available_at",
    "language",
    "raw_path",
    "clean_text_path",
    "content_hash",
    "raw_content_hash",
    "clean_content_hash",
    "cleaning_status",
    "cleaning_version",
    "cleaning_agent",
    "ingested_at",
    "cleaned_at",
    "included_in_discovery",
    "exclusion_reason",
]

# io_contracts.md section 7: document_cleaning_log.parquet
CLEANING_LOG_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "raw_document_id",
    "document_id",
    "cleaning_step",
    "action_type",
    "rule_id",
    "before_hash",
    "after_hash",
    "char_count_before",
    "char_count_after",
    "status",
    "warning_code",
    "warning_message",
    "cleaned_by",
    "created_at",
]

# Deterministic cleaning rule ids (stable for audit).
RULE_HTML_EXTRACT = "html_extract"
RULE_NORMALIZE_LINE_ENDINGS = "norm_line_endings_v1"
RULE_NORMALIZE_WHITESPACE = "norm_whitespace_v1"
RULE_STRIP_PAGE_NUMBERS = "strip_page_numbers_v1"
RULE_STRIP_REPEATED_HEADERS = "strip_repeated_headers_v1"
RULE_QUARANTINE = "quarantine_v1"

# Lines matching these deterministic patterns are page-number boilerplate.
_PAGE_NUMBER_PATTERNS = [
    re.compile(r"^\s*[-—]?\s*\d+\s*[-—]?\s*$"),              # "12", "- 12 -"
    re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.I),    # "Page 3 of 10"
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),                     # "3 / 10"
]


# Patterns that identify EDGAR SGML wrapper tags (case-insensitive, line-level).
_SGML_WRAPPER_TAG_RE = re.compile(
    r"^<(DOCUMENT|TYPE|SEQUENCE|FILENAME|DESCRIPTION|TEXT|/DOCUMENT|/TEXT)\b.*",
    re.IGNORECASE,
)

# Sniff patterns: we treat content as HTML/SGML when the raw text begins with
# any of these tokens (after stripping leading whitespace).
_HTML_SNIFF_RE = re.compile(
    r"^\s*(<DOCUMENT\b|<html\b|<!DOCTYPE\b|<\?xml\b)",
    re.IGNORECASE | re.DOTALL,
)


def _is_html_sgml(raw_text: str, raw_path: str = "") -> bool:
    """Return True when the content should be treated as HTML/SGML.

    Detection is by file extension (.htm/.html) or a content sniff for the
    leading tokens used by EDGAR filings and standard HTML/XML documents.
    """
    ext = Path(raw_path).suffix.lower()
    if ext in (".htm", ".html"):
        return True
    return bool(_HTML_SNIFF_RE.match(raw_text))


class _TextExtractor(html.parser.HTMLParser):
    """Minimal stdlib HTML parser that collects visible text, skipping
    <script> and <style> blocks.  Deterministic: produces the same output
    for identical input."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0  # depth counter for skip-tags
        self._SKIP_TAGS = frozenset(("script", "style"))

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_html_text(raw_text: str) -> str:
    """Extract readable plain text from an HTML/SGML document.

    Steps (all deterministic, meaning-preserving):
    1. Strip EDGAR SGML wrapper lines (<DOCUMENT>, <TYPE>, <SEQUENCE> etc.)
       that sit outside any HTML block.
    2. Feed the remaining content through a stdlib HTML parser that collects
       visible text while skipping <script>/<style> blocks.
    3. Decode any residual HTML entities (html.unescape).
    4. Collapse runs of whitespace and blank lines.
    """
    # --- Step 1: strip EDGAR SGML wrapper lines --------------------------
    # EDGAR wraps HTML filings in an outer SGML envelope.  Lines that are
    # purely SGML wrapper tags are removed; everything else is kept verbatim
    # so the HTML parser can see the real markup.
    stripped_lines: list[str] = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if _SGML_WRAPPER_TAG_RE.match(stripped):
            continue
        stripped_lines.append(line)
    sgml_stripped = "\n".join(stripped_lines)

    # --- Step 2: parse HTML and collect visible text ----------------------
    parser = _TextExtractor()
    # Feed the content; errors in malformed HTML are tolerated (HTMLParser
    # is lenient by default).
    try:
        parser.feed(sgml_stripped)
    except Exception:
        # Fallback: strip all tags with a regex if the parser throws.
        sgml_stripped = re.sub(r"<[^>]+>", " ", sgml_stripped)
        return html.unescape(sgml_stripped)

    extracted = parser.get_text()

    # --- Step 3: decode residual HTML entities ----------------------------
    extracted = html.unescape(extracted)

    # --- Step 4: collapse whitespace / blank lines -----------------------
    # Replace non-newline whitespace runs with a single space, then
    # collapse 2+ consecutive blank lines to one.
    lines = extracted.split("\n")
    normalised: list[str] = []
    for line in lines:
        normalised.append(re.sub(r"[ \t]+", " ", line).strip())
    out: list[str] = []
    blank_run = 0
    for line in normalised:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out).strip("\n")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_raw_path(raw_path: str, documents_root: Path | None) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    if documents_root is not None:
        return documents_root / p
    return REPO_ROOT / p


def _normalize_line_endings(text: str) -> str:
    """CRLF / CR -> LF. Deterministic, meaning-preserving."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs and trailing line whitespace; cap blank
    runs at one blank line. Does not reorder or rewrite any token."""
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # collapse interior runs of spaces/tabs to a single space, strip ends
        collapsed = re.sub(r"[ \t]+", " ", line).strip()
        cleaned_lines.append(collapsed)
    # collapse 2+ consecutive blank lines into a single blank line
    out: list[str] = []
    blank_run = 0
    for line in cleaned_lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out).strip("\n")


def _strip_page_numbers(text: str) -> tuple[str, int]:
    """Remove standalone page-number lines by deterministic patterns."""
    removed = 0
    kept: list[str] = []
    for line in text.split("\n"):
        if any(pat.match(line) for pat in _PAGE_NUMBER_PATTERNS):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _strip_repeated_headers(text: str) -> tuple[str, int]:
    """Remove lines that repeat across many 'pages' (deterministic header /
    footer detection).

    A line is treated as a repeated header/footer when the identical
    non-empty line appears 3+ times AND on at least 3 distinct page blocks
    (page blocks are separated by form-feed or already-stripped boundaries).
    To stay deterministic and avoid removing genuine repeated prose, we only
    strip short lines (<= 80 chars) that repeat 3+ times.
    """
    lines = text.split("\n")
    counts: dict[str, int] = {}
    for line in lines:
        s = line.strip()
        if s and len(s) <= 80:
            counts[s] = counts.get(s, 0) + 1
    repeated = {s for s, c in counts.items() if c >= 3}
    if not repeated:
        return text, 0
    removed = 0
    kept: list[str] = []
    for line in lines:
        if line.strip() in repeated:
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _clean_text(raw_text: str, raw_path: str = "") -> tuple[str, list[dict]]:
    """Apply deterministic cleaning steps, returning the cleaned text and a
    list of per-step action descriptors for the cleaning log."""
    actions: list[dict] = []

    # --- Pre-step: HTML/SGML extraction (runs before normalization) -------
    if _is_html_sgml(raw_text, raw_path):
        extracted = _extract_html_text(raw_text)
        actions.append(
            {
                "cleaning_step": "html_extract",
                "action_type": "extract",
                "rule_id": RULE_HTML_EXTRACT,
                "before": raw_text,
                "after": extracted,
            }
        )
        raw_text = extracted

    step_text = _normalize_line_endings(raw_text)
    actions.append(
        {
            "cleaning_step": "normalize_line_endings",
            "action_type": "normalize",
            "rule_id": RULE_NORMALIZE_LINE_ENDINGS,
            "before": raw_text,
            "after": step_text,
        }
    )

    before = step_text
    step_text = _normalize_whitespace(step_text)
    actions.append(
        {
            "cleaning_step": "normalize_whitespace",
            "action_type": "normalize",
            "rule_id": RULE_NORMALIZE_WHITESPACE,
            "before": before,
            "after": step_text,
        }
    )

    before = step_text
    step_text, n_pages = _strip_page_numbers(step_text)
    if n_pages:
        step_text = _normalize_whitespace(step_text)
        actions.append(
            {
                "cleaning_step": "strip_page_numbers",
                "action_type": "remove",
                "rule_id": RULE_STRIP_PAGE_NUMBERS,
                "before": before,
                "after": step_text,
            }
        )

    before = step_text
    step_text, n_headers = _strip_repeated_headers(step_text)
    if n_headers:
        step_text = _normalize_whitespace(step_text)
        actions.append(
            {
                "cleaning_step": "strip_repeated_headers",
                "action_type": "remove",
                "rule_id": RULE_STRIP_REPEATED_HEADERS,
                "before": before,
                "after": step_text,
            }
        )

    return step_text, actions


def _read_raw_documents(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "raw_documents.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"raw_documents.parquet not found for run {run_id}; run import first",
        )
    table = pq.read_table(artifact)
    return table.to_pylist()


def _empty_table(columns: list[str]) -> pa.Table:
    return pa.table({col: pa.array([], type=pa.string()) for col in columns})


def _write_table(rows: list[dict], columns: list[str], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        pq.write_table(_empty_table(columns), out_path)
        return
    # Ensure every row has exactly the contract columns, in order.
    pydict = {col: [row.get(col) for row in rows] for col in columns}
    table = pa.Table.from_pydict(pydict)
    pq.write_table(table, out_path)


def clean_documents(
    run_id: str,
    documents_dir: str | None = None,
) -> tuple[int, int, list[str]]:
    """Clean raw documents into ``documents.parquet`` and write the cleaning log.

    Returns ``(included_count, quarantined_count, quarantine_reasons)``.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date = manifest.as_of_date

    documents_root: Path | None = None
    if documents_dir:
        dr = Path(documents_dir)
        documents_root = dr if dr.is_absolute() else (REPO_ROOT / dr)

    raw_rows = _read_raw_documents(run_id)
    run_dir = runs.get_run_dir(run_id)
    discovery_dir = run_dir / "discovery"
    clean_text_dir = discovery_dir / "clean_text"

    document_rows: list[dict] = []
    log_rows: list[dict] = []
    quarantine_reasons: list[str] = []
    included = 0
    quarantined = 0
    seen_content_hashes: dict[str, str] = {}

    for idx, raw in enumerate(raw_rows):
        # The import artifact uses `document_id` + `content_hash`; treat that
        # `document_id` as the stable raw_document_id link (data_schema sec 3:
        # "Each cleaned document must link back to raw_document_id").
        raw_document_id = (
            raw.get("raw_document_id")
            or raw.get("document_id")
            or f"raw_{idx}"
        )
        raw_content_hash = raw.get("raw_content_hash") or raw.get("content_hash") or ""
        document_id = f"doc_{raw_document_id}"
        created_at = _utc_now_iso()
        ingested_at = raw.get("ingested_at") or created_at

        def _quarantine(warning_code: str, message: str) -> None:
            nonlocal quarantined
            quarantined += 1
            quarantine_reasons.append(f"{raw_document_id}: {message}")
            log_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": None,
                    "cleaning_step": "quarantine",
                    "action_type": "quarantine",
                    "rule_id": RULE_QUARANTINE,
                    "before_hash": raw_content_hash or None,
                    "after_hash": None,
                    "char_count_before": None,
                    "char_count_after": None,
                    "status": "quarantined",
                    "warning_code": warning_code,
                    "warning_message": message,
                    "cleaned_by": CLEANING_AGENT,
                    "created_at": created_at,
                }
            )

        available_at = raw.get("available_at")

        # --- Quarantine: missing required metadata (do NOT infer available_at).
        missing = [
            f
            for f in ("source", "source_id", "available_at", "raw_path")
            if not raw.get(f)
        ]
        if missing:
            _quarantine("missing_metadata", f"missing required metadata: {missing}")
            continue

        # --- Quarantine: future document (point-in-time leakage guard).
        if str(available_at) > str(as_of_date):
            _quarantine(
                "future_document",
                f"available_at {available_at} is after run as_of_date {as_of_date}",
            )
            continue

        # --- Read raw text (quarantine unreadable files).
        raw_path_value = raw.get("raw_path") or ""
        abs_raw_path = _resolve_raw_path(raw_path_value, documents_root)
        try:
            raw_text = abs_raw_path.read_text(encoding="utf-8")
        except Exception as exc:  # unreadable / missing file
            _quarantine("unreadable_file", f"unreadable raw file: {exc}")
            continue

        # --- Clean (deterministic).
        clean_text, actions = _clean_text(raw_text, raw_path=raw_path_value)
        clean_content_hash = _sha256_text(clean_text)

        # --- Quarantine: duplicate (same cleaned content).
        if clean_content_hash in seen_content_hashes:
            _quarantine(
                "duplicate",
                f"duplicate of {seen_content_hashes[clean_content_hash]} "
                f"(identical cleaned content)",
            )
            continue
        seen_content_hashes[clean_content_hash] = raw_document_id

        # --- Persist cleaned text artifact (text not stored inline in parquet).
        clean_text_dir.mkdir(parents=True, exist_ok=True)
        clean_text_path = clean_text_dir / f"{document_id}.txt"
        clean_text_path.write_text(clean_text, encoding="utf-8")
        clean_text_rel = clean_text_path.relative_to(run_dir).as_posix()

        # --- Log every material cleaning action.
        for act in actions:
            before_text = act["before"]
            after_text = act["after"]
            if before_text == after_text:
                # Not a material change; still record normalize steps as no-op
                # so the audit shows the rule ran.
                pass
            log_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": document_id,
                    "cleaning_step": act["cleaning_step"],
                    "action_type": act["action_type"],
                    "rule_id": act["rule_id"],
                    "before_hash": _sha256_text(before_text),
                    "after_hash": _sha256_text(after_text),
                    "char_count_before": len(before_text),
                    "char_count_after": len(after_text),
                    "status": "applied" if before_text != after_text else "noop",
                    "warning_code": None,
                    "warning_message": None,
                    "cleaned_by": CLEANING_AGENT,
                    "created_at": created_at,
                }
            )

        document_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "document_id": document_id,
                "raw_document_id": raw_document_id,
                "source": raw.get("source"),
                "source_id": raw.get("source_id"),
                "title": raw.get("title"),
                "document_type": raw.get("document_type"),
                "company_id": raw.get("company_id"),
                "published_at": raw.get("published_at"),
                "available_at": available_at,
                "language": raw.get("language"),
                "raw_path": raw_path_value,
                "clean_text_path": clean_text_rel,
                # content_hash == clean_content_hash for canonical cleaned text.
                "content_hash": clean_content_hash,
                "raw_content_hash": raw_content_hash,
                "clean_content_hash": clean_content_hash,
                "cleaning_status": "cleaned",
                "cleaning_version": CLEANING_VERSION,
                "cleaning_agent": CLEANING_AGENT,
                "ingested_at": ingested_at,
                "cleaned_at": created_at,
                "included_in_discovery": True,
                "exclusion_reason": None,
            }
        )
        included += 1

    _write_table(
        document_rows,
        DOCUMENTS_COLUMNS,
        discovery_dir / "documents.parquet",
    )
    _write_table(
        log_rows,
        CLEANING_LOG_COLUMNS,
        discovery_dir / "document_cleaning_log.parquet",
    )

    return included, quarantined, quarantine_reasons
