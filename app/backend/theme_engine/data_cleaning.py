"""Data cleaning and chunking service.

Reads `discovery/raw_documents.parquet` from a run and writes the L1 artifacts:
- `discovery/documents.parquet`
- `discovery/document_cleaning_log.parquet`
- `discovery/chunks.parquet`
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs
from .config import REPO_ROOT

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None

try:
    import pypdf
except Exception:  # pragma: no cover - optional dependency
    pypdf = None

SCHEMA_VERSION = "1.0"
CLEANING_VERSION = "cleaning-v1"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120

DOCUMENTS_COLUMNS = (
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
)

CLEANING_LOG_COLUMNS = (
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
)

CHUNKS_COLUMNS = (
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
)

DOCUMENTS_SCHEMA = pa.schema(
    {
        "schema_version": pa.string(),
        "run_id": pa.string(),
        "document_id": pa.string(),
        "raw_document_id": pa.string(),
        "source": pa.string(),
        "source_id": pa.string(),
        "title": pa.string(),
        "document_type": pa.string(),
        "company_id": pa.string(),
        "published_at": pa.string(),
        "available_at": pa.string(),
        "language": pa.string(),
        "raw_path": pa.string(),
        "clean_text_path": pa.string(),
        "content_hash": pa.string(),
        "raw_content_hash": pa.string(),
        "clean_content_hash": pa.string(),
        "cleaning_status": pa.string(),
        "cleaning_version": pa.string(),
        "cleaning_agent": pa.string(),
        "ingested_at": pa.string(),
        "cleaned_at": pa.string(),
        "included_in_discovery": pa.bool_(),
        "exclusion_reason": pa.string(),
    }
)

CLEANING_LOG_SCHEMA = pa.schema(
    {
        "schema_version": pa.string(),
        "run_id": pa.string(),
        "raw_document_id": pa.string(),
        "document_id": pa.string(),
        "cleaning_step": pa.string(),
        "action_type": pa.string(),
        "rule_id": pa.string(),
        "before_hash": pa.string(),
        "after_hash": pa.string(),
        "char_count_before": pa.int64(),
        "char_count_after": pa.int64(),
        "status": pa.string(),
        "warning_code": pa.string(),
        "warning_message": pa.string(),
        "cleaned_by": pa.string(),
        "created_at": pa.string(),
    }
)

CHUNKS_SCHEMA = pa.schema(
    {
        "schema_version": pa.string(),
        "run_id": pa.string(),
        "chunk_id": pa.string(),
        "document_id": pa.string(),
        "raw_document_id": pa.string(),
        "chunk_index": pa.int64(),
        "text": pa.string(),
        "token_count": pa.int64(),
        "start_char": pa.int64(),
        "end_char": pa.int64(),
        "page_start": pa.int64(),
        "page_end": pa.int64(),
        "section_title": pa.string(),
        "available_at": pa.string(),
        "content_hash": pa.string(),
        "cleaning_version": pa.string(),
    }
)

_WHITESPACE_LINE = re.compile(r"^\s*$")
_NOISE_PATTERNS = (
    re.compile(r"(?i)^page\s+\d+(\s+of\s+\d+)?$"),
    re.compile(r"^\d+$"),
    re.compile(r"^[-_]{3,}\s*\d+\s*[-_]{3,}$"),
    re.compile(r"^\[\s*\d+\s*\]$"),
)


def _safe_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip())
    return token or "item"


def _sha256_text(value: str) -> str:
    digest = hashlib.sha256()
    digest.update((value or "").encode("utf-8"))
    return digest.hexdigest()


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _normalize_timestamp(value: Any) -> str:
    text = _to_str(value).strip()
    if not text:
        return ""
    if len(text) >= 10 and text[:10][0].isdigit():
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat()
    except Exception:
        return text


def _resolve_path(path_text: str) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _read_raw_documents(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "discovery" / "raw_documents.parquet"
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail="raw_documents.parquet missing; run must complete /api/data/import first",
        )

    try:
        table = pq.read_table(path)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"cannot read raw_documents.parquet: {exc}",
        ) from exc

    if table.num_rows == 0:
        return []
    return table.to_pylist()


def _resolve_documents_root(run_id: str) -> Path:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    path_text = _to_str(manifest.pipeline_config)
    if not path_text:
        return _resolve_path("data/inputs/documents")

    if yaml is None:
        return _resolve_path("data/inputs/documents")

    config_path = _resolve_path(path_text)
    if not config_path.exists():
        return _resolve_path("data/inputs/documents")

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return _resolve_path("data/inputs/documents")

    if not isinstance(payload, dict):
        return _resolve_path("data/inputs/documents")

    root = _to_str(payload.get("input", {}).get("documents_dir", "")).strip()
    return _resolve_path(root or "data/inputs/documents")


def _read_pipeline_config(manifest: Any) -> dict[str, Any]:
    path_text = _to_str(manifest.pipeline_config)
    if not path_text:
        return {}
    if yaml is None:
        return {}

    config_path = _resolve_path(path_text)
    if not config_path.exists():
        return {}

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def _chunking_config(manifest: Any) -> tuple[int, int]:
    payload = _read_pipeline_config(manifest)
    chunking = payload.get("chunking", {}) if isinstance(payload, dict) else {}
    if not isinstance(chunking, dict):
        chunking = {}

    chunk_size = chunking.get("chunk_size")
    chunk_overlap = chunking.get("chunk_overlap")

    try:
        chunk_size_int = int(chunk_size)
        if chunk_size_int <= 0:
            raise ValueError
    except Exception:
        chunk_size_int = DEFAULT_CHUNK_SIZE

    try:
        overlap_int = int(chunk_overlap)
        if overlap_int < 0:
            raise ValueError
    except Exception:
        overlap_int = DEFAULT_CHUNK_OVERLAP

    if overlap_int >= chunk_size_int:
        overlap_int = max(0, chunk_size_int // 2)
    return chunk_size_int, overlap_int


def _read_text_file(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        if pypdf is None:
            raise ValueError("pdf parsing dependency unavailable (install pypdf)")
        try:
            reader = pypdf.PdfReader(str(file_path))
            pages = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                pages.append(extracted)
            return "\n".join(pages)
        except Exception as exc:
            raise ValueError(f"pdf extraction failed: {exc}") from exc

    try:
        payload = file_path.read_bytes()
    except Exception as exc:
        raise ValueError(f"read failed: {exc}") from exc
    try:
        text = payload.decode("utf-8")
    except Exception:
        text = payload.decode("utf-8", errors="ignore")

    if suffix in {".html", ".htm"}:
        text = re.sub(r"<[^>]+>", " ", text)

    return text


def _parse_date_for_as_of(value: Any) -> str:
    normalized = _normalize_timestamp(value)
    if not normalized:
        return ""

    if "T" in normalized:
        normalized = normalized.split("T", 1)[0]
    return normalized


def inclusion_within_as_of(value: Any, as_of_date: str) -> bool:
    available = _parse_date_for_as_of(value)
    as_of = _normalize_timestamp(as_of_date).split("T", 1)[0] if as_of_date else ""
    if not available or not as_of:
        return True
    return available <= as_of


def _normalize_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u0000", "").replace("\ufeff", "")
    lines = []
    for line in text.split("\n"):
        if _WHITESPACE_LINE.match(line):
            continue
        compacted = re.sub(r"\t", " ", line).strip()
        if not compacted:
            continue
        if any(pattern.match(compacted) for pattern in _NOISE_PATTERNS):
            continue
        compacted = re.sub(r"\s{2,}", " ", compacted)
        lines.append(compacted)
    return "\n".join(lines).strip()


def _write_empty_table(path: Path, schema: pa.Schema) -> None:
    table = pa.Table.from_pydict(
        {field.name: [] for field in schema},
        schema=schema,
    )
    pq.write_table(table, path)


def _write_table(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        _write_empty_table(path, schema)
        return
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path)


def clean_documents(run_id: str) -> tuple[int, int, list[str]]:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    run_dir = runs.get_run_dir(run_id)
    raw_rows = _read_raw_documents(run_dir)
    documents_root = _resolve_documents_root(run_id)

    documents_dir = run_dir / "discovery"
    text_dir = documents_dir / "clean_text"
    text_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    documents: list[dict[str, Any]] = []
    cleaning_log: list[dict[str, Any]] = []
    included_documents = 0
    quarantined_documents = 0

    for idx, row in enumerate(raw_rows):
        raw_document_id = _to_str(row.get("document_id") or row.get("raw_document_id"))
        if not raw_document_id:
            raw_document_id = _safe_token(
                f"{row.get('source_id','')}-{row.get('company_id','')}"
            ) + f"-{idx}"
            row["document_id"] = raw_document_id

        source = _to_str(row.get("source"))
        source_id = _to_str(row.get("source_id"))
        title = _to_str(row.get("title"))
        document_type = _to_str(row.get("document_type"))
        company_id = _to_str(row.get("company_id"))
        raw_path = _to_str(row.get("raw_path"))
        if not raw_path or not source or not source_id or not document_type:
            quarantined_documents += 1
            cleaning_log.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": "",
                    "cleaning_step": "validation",
                    "action_type": "quarantine",
                    "rule_id": "raw_metadata_v1",
                    "before_hash": None,
                    "after_hash": None,
                    "char_count_before": None,
                    "char_count_after": None,
                    "status": "quarantined",
                    "warning_code": "missing_metadata",
                    "warning_message": "missing required raw metadata fields",
                    "cleaned_by": CLEANING_VERSION,
                    "created_at": now,
                }
            )
            continue

        raw_abs_path = documents_root / raw_path
        if not raw_abs_path.exists():
            quarantined_documents += 1
            cleaning_log.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": raw_document_id,
                    "cleaning_step": "text_extraction",
                    "action_type": "quarantine",
                    "rule_id": "raw_file_exists_v1",
                    "before_hash": None,
                    "after_hash": None,
                    "char_count_before": None,
                    "char_count_after": None,
                    "status": "quarantined",
                    "warning_code": "raw_file_missing",
                    "warning_message": "raw_path not found",
                    "cleaned_by": CLEANING_VERSION,
                    "created_at": now,
                }
            )
            continue

        try:
            raw_text = _read_text_file(raw_abs_path)
        except Exception as exc:
            quarantined_documents += 1
            cleaning_log.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": raw_document_id,
                    "cleaning_step": "text_extraction",
                    "action_type": "quarantine",
                    "rule_id": "text_extraction_v1",
                    "before_hash": None,
                    "after_hash": None,
                    "char_count_before": None,
                    "char_count_after": None,
                    "status": "quarantined",
                    "warning_code": "text_extraction_failed",
                    "warning_message": str(exc),
                    "cleaned_by": CLEANING_VERSION,
                    "created_at": now,
                }
            )
            continue

        before = raw_text
        before_hash = _sha256_text(before)
        normalized = _normalize_text(raw_text)
        if not normalized:
            quarantined_documents += 1
            cleaning_log.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "raw_document_id": raw_document_id,
                    "document_id": raw_document_id,
                    "cleaning_step": "normalize_text",
                    "action_type": "quarantine",
                    "rule_id": "normalize_whitespace_v1",
                    "before_hash": before_hash,
                    "after_hash": None,
                    "char_count_before": len(before),
                    "char_count_after": 0,
                    "status": "quarantined",
                    "warning_code": "empty_clean_text",
                    "warning_message": "clean text is empty after normalization",
                    "cleaned_by": CLEANING_VERSION,
                    "created_at": now,
                }
            )
            continue

        after_hash = _sha256_text(normalized)
        action = "normalize_text" if before_hash != after_hash else "normalize_noop"
        action_type = "normalize" if before_hash != after_hash else "noop"
        status = "cleaned" if before_hash != after_hash else "ok"
        cleaning_rule = "normalize_whitespace_v1"
        included_in_discovery = _to_bool(row.get("included_in_discovery"))
        exclusion_reason = _to_str(row.get("exclusion_reason"))
        if not inclusion_within_as_of(row.get("available_at"), manifest.as_of_date):
            included_in_discovery = False
            if not exclusion_reason:
                exclusion_reason = "future_available_at_excludes_discovery"

        clean_doc_id = raw_document_id if raw_document_id else _safe_token(f"{source_id}-{idx}")
        clean_text_path = text_dir / f"{clean_doc_id}.txt"
        clean_text_path.write_text(normalized, encoding="utf-8")
        rel_clean_path = str(clean_text_path.relative_to(run_dir).as_posix())

        documents.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "document_id": clean_doc_id,
                "raw_document_id": raw_document_id,
                "source": source,
                "source_id": source_id,
                "title": title,
                "document_type": document_type,
                "company_id": company_id,
                "published_at": _normalize_timestamp(row.get("published_at")),
                "available_at": _normalize_timestamp(row.get("available_at")),
                "language": _to_str(row.get("language")),
                "raw_path": raw_path,
                "clean_text_path": rel_clean_path,
                "content_hash": after_hash,
                "raw_content_hash": _to_str(row.get("content_hash")),
                "clean_content_hash": after_hash,
                "cleaning_status": status,
                "cleaning_version": CLEANING_VERSION,
                "cleaning_agent": "theme-cleaning-agent",
                "ingested_at": _to_str(row.get("ingested_at")),
                "cleaned_at": now,
                "included_in_discovery": included_in_discovery,
                "exclusion_reason": exclusion_reason,
            }
        )

        if included_in_discovery:
            included_documents += 1

        cleaning_log.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "raw_document_id": raw_document_id,
                "document_id": clean_doc_id,
                "cleaning_step": action,
                "action_type": action_type,
                "rule_id": cleaning_rule,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "char_count_before": len(before),
                "char_count_after": len(normalized),
                "status": status,
                "warning_code": None,
                "warning_message": None,
                "cleaned_by": CLEANING_VERSION,
                "created_at": now,
            }
        )

    _write_table(documents_dir / "documents.parquet", documents, DOCUMENTS_SCHEMA)
    _write_table(
        documents_dir / "document_cleaning_log.parquet", cleaning_log, CLEANING_LOG_SCHEMA
    )

    return (
        included_documents,
        quarantined_documents,
        ["discovery/documents.parquet", "discovery/document_cleaning_log.parquet"],
    )


def _chunk_document(
    content: str, chunk_size: int, overlap: int
) -> list[tuple[str, int, int, int]]:
    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 2)
    if overlap < 0:
        overlap = 0

    step = max(chunk_size - overlap, 1)
    chunks: list[tuple[str, int, int, int]] = []
    if not content:
        return chunks

    length = len(content)
    start = 0
    index = 0
    while start < length:
        end = min(start + chunk_size, length)
        raw_piece = content[start:end]
        piece = raw_piece.strip()
        if piece:
            piece_start = start + len(raw_piece) - len(raw_piece.lstrip())
            piece_end = piece_start + len(piece)
            chunks.append((piece, index, piece_start, piece_end))
            index += 1
        if end >= length:
            break
        start = min(start + step, length - 1)
    return chunks


def chunk_documents(run_id: str) -> tuple[int, list[str]]:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    run_dir = runs.get_run_dir(run_id)
    documents_path = run_dir / "discovery" / "documents.parquet"
    if not documents_path.exists():
        raise HTTPException(
            status_code=409,
            detail="documents.parquet missing; run must complete /api/data/clean first",
        )

    chunk_size, chunk_overlap = _chunking_config(manifest)
    try:
        doc_table = pq.read_table(documents_path)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"cannot read documents.parquet: {exc}",
        ) from exc

    doc_rows = doc_table.to_pylist() if doc_table.num_rows else []
    chunks: list[dict[str, Any]] = []
    chunk_count = 0

    for idx, row in enumerate(doc_rows):
        if not _to_bool(row.get("included_in_discovery")):
            continue
        document_id = _to_str(row.get("document_id"))
        if not document_id:
            document_id = f"doc-{idx}"
        raw_document_id = _to_str(row.get("raw_document_id"))
        if not raw_document_id:
            raw_document_id = document_id

        clean_text_path = _to_str(row.get("clean_text_path"))
        if not clean_text_path:
            continue

        source_text_path = run_dir / clean_text_path
        if not source_text_path.exists():
            source_text_path = run_dir / "discovery" / clean_text_path
            if not source_text_path.exists():
                continue

        content = source_text_path.read_text(encoding="utf-8")
        chunk_results = _chunk_document(content, chunk_size, chunk_overlap)
        for chunk_text, chunk_index, start, end in chunk_results:
            piece = chunk_text.strip()
            if not piece:
                continue
            chunks.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "chunk_id": _sha256_text(
                        f"{document_id}:{piece}:{chunk_size}:{chunk_overlap}:{chunk_index}"
                    ),
                    "document_id": document_id,
                    "raw_document_id": raw_document_id,
                    "chunk_index": chunk_index,
                    "text": piece,
                    "token_count": len(piece.split()),
                    "start_char": start,
                    "end_char": end,
                    "page_start": None,
                    "page_end": None,
                    "section_title": None,
                    "available_at": _to_str(row.get("available_at")),
                    "content_hash": _sha256_text(piece),
                    "cleaning_version": CLEANING_VERSION,
                }
            )
            chunk_count += 1

    _write_table(run_dir / "discovery" / "chunks.parquet", chunks, CHUNKS_SCHEMA)
    return chunk_count, ["discovery/chunks.parquet"]
