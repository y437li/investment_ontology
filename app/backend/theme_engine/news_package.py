"""News package assembly for downstream reporters (e.g., Yianbao).

Builds a compact JSON artifact that aggregates cleaned documents, top chunks, and
optional document-theme affinity information into a consumable package.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

PACKAGE_VERSION = "1.0"
OUTPUT_NAME = "news_report_package.json"
MACRO_MARKERS = {"macro", "macro_release", "macro_data", "macro_news", "macro_update"}


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _read_parquet_rows(path: Path, artifact_name: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"{artifact_name} missing for news package",
        )

    try:
        table = pq.read_table(path)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=409,
            detail=f"cannot read {artifact_name}: {exc}",
        ) from exc

    if table.num_rows == 0:
        return []
    return table.to_pylist()


def _read_optional_parquet_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        table = pq.read_table(path)
    except Exception:
        return []
    if table.num_rows == 0:
        return []
    return table.to_pylist()


def _normalize_values(values: list[str]) -> set[str]:
    return {value.strip().lower() for value in values if isinstance(value, str) and value.strip()}


def _safe_truncate(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _coalesce_sort_key(document: dict[str, Any]) -> str:
    available_at = _to_str(document.get("available_at"))
    if available_at:
        return available_at
    published_at = _to_str(document.get("published_at"))
    if published_at:
        return published_at
    return _to_str(document.get("created_at"), "")


def _build_excerpt(chunks: list[dict[str, Any]], max_chunk_chars: int) -> str:
    if not chunks:
        return ""
    text_parts: list[str] = []
    for chunk in chunks:
        text = _to_str(chunk.get("text"))
        if not text:
            continue
        text_parts.append(_safe_truncate(text, max_chunk_chars))
    return "\n\n".join(text_parts)


def create_news_package(
    run_id: str,
    max_documents: int,
    max_chunks_per_document: int,
    max_chunk_chars: int,
    include_document_types: list[str],
    include_companies: list[str],
    include_macro: bool,
    include_affinity: bool,
) -> tuple[str, int, int]:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    run_dir = runs.get_run_dir(run_id)
    discovery_dir = run_dir / "discovery"

    documents_rows = _read_parquet_rows(discovery_dir / "documents.parquet", "documents.parquet")
    chunks_rows = _read_parquet_rows(discovery_dir / "chunks.parquet", "chunks.parquet")
    affinity_rows = _read_optional_parquet_rows(
        discovery_dir / "document_theme_affinity.parquet"
    ) if include_affinity else []

    requested_types = _normalize_values(include_document_types)
    requested_companies = _normalize_values(include_companies)

    chunks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks_rows:
        doc_id = _to_str(chunk.get("document_id"))
        if not doc_id:
            continue
        chunk_index = _to_int(chunk.get("chunk_index"), 0)
        item = {
            "chunk_id": _to_str(chunk.get("chunk_id")),
            "chunk_index": chunk_index,
            "text": _to_str(chunk.get("text")),
            "section_title": _to_str(chunk.get("section_title")),
            "start_char": chunk.get("start_char"),
            "end_char": chunk.get("end_char"),
        }
        chunks_by_doc[doc_id].append(item)

    for item in chunks_by_doc.values():
        item.sort(key=lambda row: _to_int(row.get("chunk_index", 0), 0))

    affinity_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in affinity_rows:
        doc_id = _to_str(row.get("document_id"))
        if not doc_id:
            continue
        affinity_by_doc[doc_id].append(
            {
                "community_id": _to_str(row.get("community_id")),
                "theme_snapshot_id": _to_str(row.get("theme_snapshot_id")),
                "theme_name": _to_str(row.get("theme_name")),
                "document_community_rank": _to_int(row.get("document_community_rank"), 0),
                "normalized_affinity": row.get("normalized_affinity"),
            }
        )
    for values in affinity_by_doc.values():
        values.sort(key=lambda item: _to_int(item.get("document_community_rank"), 10**9))

    candidate_docs: list[dict[str, Any]] = []
    for row in documents_rows:
        source = _to_str(row.get("source")).lower()
        doc_type = _to_str(row.get("document_type")).lower()
        company_id = _to_str(row.get("company_id")).lower()

        if requested_companies and company_id and company_id not in requested_companies:
            continue

        if requested_types:
            match = False
            for token in requested_types:
                if token and (token in doc_type or token in source):
                    match = True
                    break
            if not match:
                continue

        if not include_macro:
            if doc_type in MACRO_MARKERS or source in MACRO_MARKERS:
                continue

        candidate_docs.append(row)

    if max_documents <= 0:
        max_documents = len(candidate_docs)

    candidate_docs.sort(
        key=lambda item: _coalesce_sort_key(item),
        reverse=True,
    )

    selected_docs = candidate_docs[:max_documents]

    package_items: list[dict[str, Any]] = []
    total_chunks = 0
    for row in selected_docs:
        doc_id = _to_str(row.get("document_id"))
        doc_chunks = chunks_by_doc.get(doc_id, [])
        if max_chunks_per_document > 0:
            doc_chunks = doc_chunks[:max_chunks_per_document]

        top_chunks = [
            {
                "chunk_id": item.get("chunk_id", ""),
                "chunk_index": _to_int(item.get("chunk_index"), 0),
                "section_title": item.get("section_title", ""),
                "text": _safe_truncate(_to_str(item.get("text")), max_chunk_chars),
                "start_char": item.get("start_char"),
                "end_char": item.get("end_char"),
            }
            for item in doc_chunks
        ]
        total_chunks += len(top_chunks)

        item = {
            "document_id": doc_id,
            "raw_document_id": _to_str(row.get("raw_document_id")),
            "title": _to_str(row.get("title")),
            "source": _to_str(row.get("source")),
            "source_id": _to_str(row.get("source_id")),
            "source_url": _to_str(row.get("source_url")),
            "document_type": _to_str(row.get("document_type")),
            "company_id": _to_str(row.get("company_id")),
            "published_at": _to_str(row.get("published_at")),
            "available_at": _to_str(row.get("available_at")),
            "document_excerpt": _build_excerpt(top_chunks, max_chunk_chars),
            "chunks": top_chunks,
            "top_themes": affinity_by_doc.get(doc_id, []),
            "included_in_discovery": bool(row.get("included_in_discovery", True)),
            "cleaning_status": _to_str(row.get("cleaning_status")),
        }
        package_items.append(item)

    payload = {
        "schema_version": PACKAGE_VERSION,
        "artifact_type": "news_report_package",
        "run_id": run_id,
        "as_of_date": manifest.as_of_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "document_count": len(package_items),
        "chunk_count": total_chunks,
        "filters": {
            "max_documents": max_documents,
            "max_chunks_per_document": max_chunks_per_document,
            "max_chunk_chars": max_chunk_chars,
            "include_document_types": sorted(requested_types),
            "include_companies": sorted(requested_companies),
            "include_macro": include_macro,
            "include_affinity": include_affinity,
        },
        "documents": package_items,
    }

    out_path = discovery_dir / OUTPUT_NAME
    discovery_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return f"discovery/{OUTPUT_NAME}", len(package_items), total_chunks
