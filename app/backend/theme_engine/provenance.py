"""Provenance materialization service (EG-E, Workstream E).

Materializes two reverse-join artifacts that let the UI answer provenance
questions in ONE read without a client-side graph walk:

E2  theme_document_evidence.parquet
    community_id -> contributing chunk_ids / document_ids (deduped, PIT-clean)
    Reads: communities.json, theme_snapshots.json, edges.parquet, chunks.parquet

E3  company_theme_document_evidence.parquet
    (company_id, theme_snapshot_id, community_id) -> chunk_ids / document_ids
    Reads: company_theme_exposure.parquet, chunks.parquet

CORRECTNESS NOTE — E3 company join:
    company_id in the exposure artifact is the COMPANY ENTITY id (an entity_id
    of type Company in entities.parquet).  It is NOT document.company_id.
    A news article whose document.company_id is X can mention company Y; we
    only attribute that document to company Y because the exposure computation
    found an edge where company Y's entity_id is a node — the resulting
    top_evidence_chunk_ids are Y-specific.  Using document.company_id for this
    join would be wrong and is deliberately avoided here.

io_contracts: sections E1, E2, E3 (docs/io_contracts.md)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

SCHEMA_VERSION = "1.0"

# io_contracts §E2 — theme_document_evidence.parquet
THEME_DOC_EVIDENCE_COLUMNS: list[str] = [
    "schema_version",
    "as_of_date",
    "community_id",
    "theme_snapshot_id",
    "chunk_ids",
    "document_ids",
]

# io_contracts §E3 — company_theme_document_evidence.parquet
# Keyed on (company_id, theme_snapshot_id, community_id) — matches the
# grain of company_theme_exposure.parquet.
COMPANY_THEME_DOC_EVIDENCE_COLUMNS: list[str] = [
    "schema_version",
    "as_of_date",
    "company_id",
    "theme_snapshot_id",
    "community_id",
    "chunk_ids",
    "document_ids",
]


# ---------------------------------------------------------------------------
# Internal readers
# ---------------------------------------------------------------------------


def _load_parquet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _get_discovery_dir(run_id: str, as_of: str | None = None) -> Path:
    return runs.discovery_point_dir(run_id, as_of)


def _require_artifact(path: Path, name: str, run_id: str) -> None:
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{name} not found for run {run_id}; run the upstream stage first",
        )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _write_list_parquet(rows: list[dict], columns: list[str], out_path: Path) -> None:
    """Write a parquet file where chunk_ids and document_ids are list[string]."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _list_cols = frozenset({"chunk_ids", "document_ids"})

    if not rows:
        fields = []
        for col in columns:
            if col in _list_cols:
                fields.append(pa.field(col, pa.list_(pa.string())))
            elif col == "evidence_count":
                fields.append(pa.field(col, pa.int64()))
            else:
                fields.append(pa.field(col, pa.string()))
        schema = pa.schema(fields)
        empty: dict[str, pa.Array] = {}
        for f in schema:
            empty[f.name] = pa.array([], type=f.type)
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    arrays: dict[str, pa.Array] = {}
    for col in columns:
        values = [row.get(col) for row in rows]
        if col in _list_cols:
            arrays[col] = pa.array(values, type=pa.list_(pa.string()))
        else:
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )

    pq.write_table(pa.table(arrays), out_path)


# ---------------------------------------------------------------------------
# E2: Theme -> Documents
# ---------------------------------------------------------------------------


def materialize_theme_document_evidence(run_id: str, as_of: str | None = None) -> int:
    """Build theme_document_evidence.parquet (E2).

    For each community, collects all evidence chunks from ALL structural edges
    where at least one endpoint is a node in that community, then resolves
    chunk_ids -> document_ids.  PIT-clean: only edges/chunks available at the
    run's as_of_date are included.

    Returns the number of community rows written.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = as_of if as_of is not None else manifest.as_of_date

    ddir = _get_discovery_dir(run_id, as_of)

    # Load required artifacts
    for fname in ("communities.json", "theme_snapshots.json", "edges.parquet", "chunks.parquet"):
        _require_artifact(ddir / fname, fname, run_id)

    communities_doc = _load_json(ddir / "communities.json")
    snapshots_doc = _load_json(ddir / "theme_snapshots.json")
    edges_raw: list[dict] = _load_parquet(ddir / "edges.parquet")
    chunks_raw: list[dict] = _load_parquet(ddir / "chunks.parquet")

    # Build chunk_id -> document_id lookup (PIT is already baked in by chunking)
    chunk_doc_id: dict[str, str] = {
        ch["chunk_id"]: ch.get("document_id", "")
        for ch in chunks_raw
        if ch.get("chunk_id")
    }

    # Build community_id -> theme_snapshot_id lookup
    community_to_snapshot: dict[str, str] = {}
    for snap in snapshots_doc.get("snapshots", []):
        cid = snap.get("community_id", "")
        sid = snap.get("theme_snapshot_id", "")
        if cid and sid:
            community_to_snapshot[cid] = sid

    # PIT-filter edges
    pit_edges: list[dict] = []
    for edge in edges_raw:
        first_seen = str(edge.get("first_seen_at", ""))[:10]
        if first_seen and first_seen > as_of_date:
            continue
        pit_edges.append(edge)

    # Build entity_id -> set of (edge) indices for fast lookup
    # We want all edges that touch any node in a community
    from collections import defaultdict  # noqa: PLC0415
    node_to_edge_chunk_ids: dict[str, list[str]] = defaultdict(list)
    for edge in pit_edges:
        chunk_ids = edge.get("evidence_chunk_ids") or []
        for nid in (edge.get("source_entity_id", ""), edge.get("target_entity_id", "")):
            if nid:
                for cid in chunk_ids:
                    node_to_edge_chunk_ids[nid].append(cid)

    rows: list[dict] = []
    for community in communities_doc.get("communities", []):
        community_id = community.get("community_id", "")
        node_ids: list[str] = community.get("node_ids", [])
        if not community_id:
            continue

        theme_snapshot_id = community_to_snapshot.get(community_id, "")
        if not theme_snapshot_id:
            continue

        # Collect all chunk_ids from edges touching this community's nodes
        seen_chunks: set[str] = set()
        for nid in node_ids:
            for cid in node_to_edge_chunk_ids.get(nid, []):
                seen_chunks.add(cid)

        # Resolve to document_ids (deduped, preserving insertion order)
        ordered_chunks: list[str] = sorted(seen_chunks)
        seen_docs: set[str] = set()
        ordered_docs: list[str] = []
        for cid in ordered_chunks:
            doc_id = chunk_doc_id.get(cid, "")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                ordered_docs.append(doc_id)

        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "as_of_date": as_of_date,
                "community_id": community_id,
                "theme_snapshot_id": theme_snapshot_id,
                "chunk_ids": ordered_chunks,
                "document_ids": ordered_docs,
            }
        )

    _write_list_parquet(rows, THEME_DOC_EVIDENCE_COLUMNS, ddir / "theme_document_evidence.parquet")
    return len(rows)


# ---------------------------------------------------------------------------
# E3: (Company, Theme) -> Documents
# ---------------------------------------------------------------------------


def materialize_company_theme_evidence(run_id: str, as_of: str | None = None) -> int:
    """Build company_theme_document_evidence.parquet (E3).

    Joins company_theme_exposure.parquet on company_id (which is a Company
    ENTITY id, NOT document.company_id) to produce one row per
    (company_id, theme_snapshot_id, community_id) with the specific
    chunk_ids and document_ids that back THAT company's exposure to THAT theme.

    A company spanning N themes produces N rows with DISTINCT evidence groups;
    there is no collapse or cross-theme bleed.

    PIT-clean: top_evidence_chunk_ids already come from the PIT-gated exposure
    computation; we only look up document_ids for those chunk_ids.

    Returns the number of rows written.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = as_of if as_of is not None else manifest.as_of_date

    ddir = _get_discovery_dir(run_id, as_of)

    for fname in ("company_theme_exposure.parquet", "chunks.parquet"):
        _require_artifact(ddir / fname, fname, run_id)

    exposure_rows: list[dict] = _load_parquet(ddir / "company_theme_exposure.parquet")
    chunks_raw: list[dict] = _load_parquet(ddir / "chunks.parquet")

    # Build chunk_id -> document_id lookup
    chunk_doc_id: dict[str, str] = {
        ch["chunk_id"]: ch.get("document_id", "")
        for ch in chunks_raw
        if ch.get("chunk_id")
    }

    rows: list[dict] = []
    for exp in exposure_rows:
        company_id = exp.get("company_id", "")
        theme_snapshot_id = exp.get("theme_snapshot_id", "")
        community_id = exp.get("community_id", "")
        if not company_id or not theme_snapshot_id:
            continue

        # top_evidence_chunk_ids are already the company-specific, PIT-gated
        # chunk ids from the edges that connect THIS company entity to THIS
        # community.  They were gathered in exposure.py's adjacency traversal,
        # which keys on the company's entity_id — never on document.company_id.
        top_chunk_ids: list[str] = exp.get("top_evidence_chunk_ids") or []

        # Resolve to document_ids (deduped, order-preserving)
        seen_docs: set[str] = set()
        ordered_docs: list[str] = []
        for cid in top_chunk_ids:
            doc_id = chunk_doc_id.get(cid, "")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                ordered_docs.append(doc_id)

        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "as_of_date": as_of_date,
                "company_id": company_id,
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "chunk_ids": list(top_chunk_ids),
                "document_ids": ordered_docs,
            }
        )

    _write_list_parquet(
        rows, COMPANY_THEME_DOC_EVIDENCE_COLUMNS,
        ddir / "company_theme_document_evidence.parquet",
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Lookup helpers for API endpoints
# ---------------------------------------------------------------------------


def get_theme_documents(run_id: str, community_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Return the E2 provenance record for a community (single read, no graph walk).

    Returns a dict with:
      community_id, theme_snapshot_id, chunk_ids, document_ids, as_of_date
    Raises HTTPException 404 if the artifact or community is not found.
    """
    ddir = _get_discovery_dir(run_id, as_of)
    artifact = ddir / "theme_document_evidence.parquet"
    _require_artifact(artifact, "theme_document_evidence.parquet", run_id)

    rows = _load_parquet(artifact)
    row = next((r for r in rows if r.get("community_id") == community_id), None)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"no provenance found for community {community_id!r} in run {run_id!r}; "
            "POST /api/provenance/materialize first",
        )
    # Convert list fields (pyarrow may return pyarrow arrays)
    return {
        "community_id": row["community_id"],
        "theme_snapshot_id": row.get("theme_snapshot_id", ""),
        "as_of_date": row.get("as_of_date", ""),
        "chunk_ids": list(row.get("chunk_ids") or []),
        "document_ids": list(row.get("document_ids") or []),
    }


def get_company_theme_documents(run_id: str, company_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
    """Return E3 provenance records for all themes a company is exposed to.

    Each item in the returned list corresponds to one (theme, community) pair;
    evidence groups are DISTINCT per theme — there is no cross-theme bleed.
    The company_id is the Company ENTITY id, not document.company_id.

    Raises HTTPException 404 if the artifact is missing.
    """
    ddir = _get_discovery_dir(run_id, as_of)
    artifact = ddir / "company_theme_document_evidence.parquet"
    _require_artifact(artifact, "company_theme_document_evidence.parquet", run_id)

    rows = _load_parquet(artifact)
    matched = [r for r in rows if r.get("company_id") == company_id]

    result: list[dict[str, Any]] = []
    for r in matched:
        result.append(
            {
                "company_id": r["company_id"],
                "theme_snapshot_id": r.get("theme_snapshot_id", ""),
                "community_id": r.get("community_id", ""),
                "as_of_date": r.get("as_of_date", ""),
                "chunk_ids": list(r.get("chunk_ids") or []),
                "document_ids": list(r.get("document_ids") or []),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Combined materialization entry point
# ---------------------------------------------------------------------------


def materialize_provenance(run_id: str, as_of: str | None = None) -> dict[str, int]:
    """Materialize E2 and E3 provenance artifacts in one call.

    Returns dict with keys 'theme_rows' and 'company_theme_rows'.
    E3 requires exposure to be computed first.
    """
    theme_rows = materialize_theme_document_evidence(run_id, as_of)
    company_theme_rows = materialize_company_theme_evidence(run_id, as_of)
    return {
        "theme_rows": theme_rows,
        "company_theme_rows": company_theme_rows,
    }
