"""Document-theme affinity computation.

Builds a soft-link artifact between documents and communities based on:
- entity evidence in chunks
- edge evidence in chunks
- community membership of entities

All associations are score-based and non-exclusive (multi-theme support by design).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

SCHEMA_VERSION = "1.0"
DEFAULT_METHOD = "document_theme_affinity_v1"
DEFAULT_ROWS_PER_DOCUMENT = 20

OUTPUT_COLUMNS = (
    "schema_version",
    "run_id",
    "as_of_date",
    "document_id",
    "raw_document_id",
    "document_title",
    "company_id",
    "community_id",
    "theme_snapshot_id",
    "theme_name",
    "document_community_rank",
    "evidence_chunk_count",
    "evidence_chunk_ids",
    "entity_signal_count",
    "edge_signal_count",
    "raw_score",
    "normalized_affinity",
    "method",
    "created_at",
)

OUTPUT_SCHEMA = pa.schema(
    {
        "schema_version": pa.string(),
        "run_id": pa.string(),
        "as_of_date": pa.string(),
        "document_id": pa.string(),
        "raw_document_id": pa.string(),
        "document_title": pa.string(),
        "company_id": pa.string(),
        "community_id": pa.string(),
        "theme_snapshot_id": pa.string(),
        "theme_name": pa.string(),
        "document_community_rank": pa.int64(),
        "evidence_chunk_count": pa.int64(),
        "evidence_chunk_ids": pa.list_(pa.string()),
        "entity_signal_count": pa.int64(),
        "edge_signal_count": pa.int64(),
        "raw_score": pa.float64(),
        "normalized_affinity": pa.float64(),
        "method": pa.string(),
        "created_at": pa.string(),
    }
)


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(_to_str(value))
    except Exception:
        return default


def _to_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [
            _to_str(item)
            for item in value
            if isinstance(item, (str, int, float, bool)) and _to_str(item)
        ]
    if isinstance(value, tuple):
        return [
            _to_str(item)
            for item in value
            if isinstance(item, (str, int, float, bool)) and _to_str(item)
        ]
    if isinstance(value, str):
        return [_to_str(value)] if _to_str(value) else []
    return []


def _read_parquet_rows(path: Path, artifact_name: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"{artifact_name} missing for affinity mapping",
        )
    try:
        table = pq.read_table(path)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"cannot read {artifact_name}: {exc}",
        ) from exc

    if table.num_rows == 0:
        return []
    return table.to_pylist()


def _read_json(path: Path, artifact_name: str) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"{artifact_name} missing for affinity mapping",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=f"cannot read {artifact_name}: {exc}",
        ) from exc

    if isinstance(payload, dict):
        return payload
    raise HTTPException(status_code=409, detail=f"invalid {artifact_name} content")


def _write_empty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    empty = pa.Table.from_pydict({field.name: [] for field in OUTPUT_SCHEMA}, schema=OUTPUT_SCHEMA)
    pq.write_table(empty, path)


def _write_affinity(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        _write_empty(path)
        return
    table = pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA)
    pq.write_table(table, path)


def _coalesce_document_metadata(run_id: str, rows: list[dict[str, Any]]) -> tuple[
    dict[str, dict[str, str]],
    dict[str, str],
]:
    docs_by_id: dict[str, dict[str, str]] = {}
    raw_to_document: dict[str, str] = {}

    for row in rows:
        document_id = _to_str(row.get("document_id"))
        if not document_id:
            continue
        raw_document_id = _to_str(row.get("raw_document_id"))
        if raw_document_id:
            raw_to_document[raw_document_id] = document_id

        docs_by_id[document_id] = {
            "run_id": run_id,
            "raw_document_id": raw_document_id,
            "title": _to_str(row.get("title")),
            "company_id": _to_str(row.get("company_id")),
            "available_at": _to_str(row.get("available_at")),
        }

    return docs_by_id, raw_to_document


def _build_chunk_document_map(chunks_rows: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in chunks_rows:
        chunk_id = _to_str(row.get("chunk_id"))
        document_id = _to_str(row.get("document_id"))
        if chunk_id and document_id:
            mapping.setdefault(chunk_id, document_id)
    return mapping


def _build_community_index(communities_payload: dict[str, Any]) -> tuple[
    dict[str, set[str]],
    dict[str, str],
    dict[str, str],
]:
    communities = communities_payload.get("communities")
    if not isinstance(communities, list) or not communities:
        raise HTTPException(
            status_code=409,
            detail="communities.json has no communities for affinity mapping",
        )

    node_to_communities: dict[str, set[str]] = defaultdict(set)
    community_to_theme: dict[str, str] = {}
    community_to_name: dict[str, str] = {}

    for item in communities:
        if not isinstance(item, dict):
            continue
        community_id = _to_str(item.get("community_id"))
        if not community_id:
            continue
        theme_name = _to_str(item.get("theme_name"))
        node_ids = _to_string_list(item.get("node_ids"))
        if not node_ids:
            continue
        community_to_theme[community_id] = theme_name
        community_to_name[community_id] = community_name = _to_str(item.get("theme_name"))
        for node_id in node_ids:
            node_to_communities[node_id].add(community_id)

    if not node_to_communities:
        raise HTTPException(
            status_code=409,
            detail="communities.json does not contain node membership",
        )

    return node_to_communities, community_to_theme, community_to_name


def _build_snapshot_map(theme_snapshots_payload: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(theme_snapshots_payload, dict):
        return {}

    snapshots = theme_snapshots_payload.get("snapshots")
    if not isinstance(snapshots, list):
        return {}

    mapping: dict[str, str] = {}
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        community_id = _to_str(item.get("community_id"))
        snapshot_id = _to_str(item.get("theme_snapshot_id"))
        if community_id and snapshot_id:
            mapping[community_id] = snapshot_id
    return mapping


def compute_document_theme_affinity(
    run_id: str,
    max_themes_per_document: int = DEFAULT_ROWS_PER_DOCUMENT,
) -> tuple[int, int, list[str]]:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    run_dir = runs.get_run_dir(run_id)
    documents_rows = _read_parquet_rows(
        run_dir / "discovery" / "documents.parquet",
        "documents.parquet",
    )
    chunks_rows = _read_parquet_rows(
        run_dir / "discovery" / "chunks.parquet",
        "chunks.parquet",
    )
    entities_rows = _read_parquet_rows(
        run_dir / "discovery" / "entities.parquet",
        "entities.parquet",
    )
    edges_rows = _read_parquet_rows(
        run_dir / "discovery" / "edges.parquet",
        "edges.parquet",
    )
    communities_payload = _read_json(
        run_dir / "discovery" / "communities.json",
        "communities.json",
    )

    theme_snapshots = _read_json(
        run_dir / "discovery" / "theme_snapshots.json",
        "theme_snapshots.json",
    ) if (run_dir / "discovery" / "theme_snapshots.json").exists() else None

    docs_by_id, raw_to_document = _coalesce_document_metadata(run_id, documents_rows)
    if not docs_by_id:
        out = run_dir / "discovery" / "document_theme_affinity.parquet"
        _write_affinity(out, [])
        return 0, 0, ["discovery/document_theme_affinity.parquet"]

    chunk_to_document = _build_chunk_document_map(chunks_rows)
    node_to_communities, community_to_theme, _ = _build_community_index(communities_payload)
    community_to_snapshot = _build_snapshot_map(theme_snapshots)

    accum: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "raw_score": 0.0,
            "entity_signal_count": 0,
            "edge_signal_count": 0,
            "evidence_chunk_ids": set(),
        }
    )

    def _add_signal(
        document_id: str,
        chunk_id: str,
        community_id: str,
        amount: float,
        via: str,
    ) -> None:
        if amount <= 0:
            return
        key = (document_id, community_id)
        state = accum[key]
        state["raw_score"] += amount
        if via == "entity":
            state["entity_signal_count"] += 1
        else:
            state["edge_signal_count"] += 1
        if chunk_id:
            state["evidence_chunk_ids"].add(chunk_id)

    for row in entities_rows:
        entity_id = _to_str(row.get("entity_id"))
        if not entity_id:
            continue
        communities = node_to_communities.get(entity_id)
        if not communities:
            continue

        confidence = _to_float(row.get("confidence"), 1.0)
        if confidence <= 0:
            continue

        chunk_ids = _to_string_list(row.get("source_chunk_ids"))
        if not chunk_ids:
            continue

        for chunk_id in sorted(set(_to_string_list(chunk_ids))):
            document_id = chunk_to_document.get(chunk_id)
            if not document_id:
                continue
            for community_id in communities:
                _add_signal(
                    document_id=document_id,
                    chunk_id=chunk_id,
                    community_id=community_id,
                    amount=confidence,
                    via="entity",
                )

    for row in edges_rows:
        source_entity = _to_str(row.get("source_entity_id"))
        target_entity = _to_str(row.get("target_entity_id"))
        if not source_entity or not target_entity:
            continue

        edge_comms = set(node_to_communities.get(source_entity, set()))
        edge_comms.update(node_to_communities.get(target_entity, set()))
        if not edge_comms:
            continue

        confidence = _to_float(row.get("confidence"), 0.5)
        if confidence <= 0:
            continue

        chunk_ids = _to_string_list(row.get("evidence_chunk_ids"))
        if not chunk_ids:
            continue

        for chunk_id in sorted(set(_to_string_list(chunk_ids))):
            document_id = chunk_to_document.get(chunk_id)
            if not document_id:
                continue
            for community_id in sorted(edge_comms):
                _add_signal(
                    document_id=document_id,
                    chunk_id=chunk_id,
                    community_id=community_id,
                    amount=0.5 * confidence,
                    via="edge",
                )

    doc_totals: dict[str, float] = defaultdict(float)
    for (document_id, _), values in accum.items():
        doc_totals[document_id] += values["raw_score"]

    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    documents_in_scope = sorted(docs_by_id.keys())
    for document_id in documents_in_scope:
        pairs = [
            (community_id, values)
            for (doc_id, community_id), values in accum.items()
            if doc_id == document_id
        ]
        if not pairs:
            continue

        total = doc_totals.get(document_id, 0.0)
        if total <= 0:
            continue

        ordered = sorted(
            pairs,
            key=lambda item: item[1]["raw_score"],
            reverse=True,
        )

        if max_themes_per_document > 0:
            ordered = ordered[:max_themes_per_document]

        for rank, (community_id, values) in enumerate(ordered, start=1):
            raw_score = float(values["raw_score"])
            affinity = raw_score / total if total > 0 else 0.0
            if affinity <= 0:
                continue

            metadata = docs_by_id[document_id]
            row: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "as_of_date": manifest.as_of_date,
                "document_id": document_id,
                "raw_document_id": metadata.get("raw_document_id", ""),
                "document_title": metadata.get("title", ""),
                "company_id": metadata.get("company_id", ""),
                "community_id": community_id,
                "theme_snapshot_id": community_to_snapshot.get(community_id, ""),
                "theme_name": community_to_theme.get(community_id, ""),
                "document_community_rank": rank,
                "evidence_chunk_count": len(values["evidence_chunk_ids"]),
                "evidence_chunk_ids": sorted(values["evidence_chunk_ids"]),
                "entity_signal_count": int(values["entity_signal_count"]),
                "edge_signal_count": int(values["edge_signal_count"]),
                "raw_score": raw_score,
                "normalized_affinity": affinity,
                "method": DEFAULT_METHOD,
                "created_at": now,
            }
            rows.append(row)

    out_path = run_dir / "discovery" / "document_theme_affinity.parquet"
    _write_affinity(out_path, rows)

    mapped_documents = len({row["document_id"] for row in rows})
    return mapped_documents, len(rows), ["discovery/document_theme_affinity.parquet"]
