"""Company-theme exposure computation service (M5).

Reads:
  - ``discovery/graph.json``           (io_contracts §13)
  - ``discovery/communities.json``     (io_contracts §14)
  - ``discovery/theme_snapshots.json`` (io_contracts §15)
  - ``discovery/entities.parquet``     (io_contracts §9)
  - ``discovery/edges.parquet``        (io_contracts §11)

Writes:
  - ``discovery/company_theme_exposure.parquet`` (io_contracts §18)

Exposure Formula (deterministic, evidence-traceable):
---------------------------------------------------------------------------
For each (Company, Theme/Community) pair, exposure_score is computed as the
weighted combination of five signals defined in spec §9.7:

  1. Graph Distance (graph_distance): shortest path length from the company
     node to any node in the community's node set, via document_stated
     structural edges. Closer = higher proximity.
     distance_score = 1.0 / (1.0 + min_distance)

  2. Edge Confidence Sum (edge_confidence_sum): sum of confidence values of
     all document_stated structural edges connecting the company to the
     community, either directly or via one intermediate node (1-hop).
     Normalized by dividing by max observed sum across all (company, community)
     pairs.

  3. Evidence Count (evidence_count): number of distinct evidence chunk ids
     across all contributing edges.
     evidence_score = log1p(evidence_count) / log1p(max_evidence_count)

  4. Recency Score (recency): for each contributing edge, recency =
     1 - (days_since_evidence / days_in_window) where days_in_window is
     capped at 365. The mean recency across contributing edges is used.
     Edges with first_seen_at closest to as_of_date score highest.

  5. Centrality (centrality): degree centrality of the company node in
     the document_stated structural graph (number of unique structural
     neighbors / (total_nodes - 1)).
     Capped at 1.0.

Final score:
  exposure_score = (
      0.30 * distance_score
    + 0.25 * edge_confidence_score
    + 0.20 * evidence_score
    + 0.15 * recency_score
    + 0.10 * centrality_score
  )

Rounded to 6 decimal places for stability.

OI-2 DEFAULT POLICY:
  Only ``document_stated`` edges contribute to exposure by default.
  ``llm_inferred`` and ``metadata_inferred`` edges are excluded unless
  the ``include_weak_signals`` config flag is set to True.

Point-in-time:
  Only edges and entities with first_seen_at / available_at <= as_of_date
  are used (enforced at the graph/edges layer, and double-checked here).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import graph_build, run_cache, runs

SCHEMA_VERSION = "1.0"

# Exposure score component weights (must sum to 1.0)
_W_DISTANCE = 0.30
_W_EDGE_CONFIDENCE = 0.25
_W_EVIDENCE = 0.20
_W_RECENCY = 0.15
_W_CENTRALITY = 0.10

# Default extraction method policy (OI-2)
_DEFAULT_STRONG_METHOD = "document_stated"
_WEAK_METHODS = frozenset({"llm_inferred", "metadata_inferred"})

# Recency window cap in days
_RECENCY_WINDOW_DAYS = 365.0

# io_contracts §18 columns (exact order)
EXPOSURE_COLUMNS: list[str] = [
    "schema_version",
    "as_of_date",
    "company_id",
    "ticker",
    "theme_snapshot_id",
    "community_id",
    "exposure_score",
    "graph_distance",
    "edge_confidence_sum",
    "evidence_count",
    "top_evidence_chunk_ids",
    "calculation_method",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_date_str(val: Any) -> str:
    """Coerce a value to YYYY-MM-DD string."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


def _days_before(date_str: str, reference_str: str) -> float:
    """Days between (reference - date), clamped to [0, _RECENCY_WINDOW_DAYS].

    A future-dated edge (date > reference) is treated as LEAST recent (window cap),
    NOT maximally recent (audit medium): it should not have passed the PIT gate, and
    must never be scored as fresh evidence. Missing/unparseable dates -> window cap.
    """
    if not date_str or not reference_str:
        return _RECENCY_WINDOW_DAYS
    try:
        d1 = datetime.strptime(date_str[:10], "%Y-%m-%d")
        d2 = datetime.strptime(reference_str[:10], "%Y-%m-%d")
        delta = (d2 - d1).days
        if delta < 0:  # future-dated -> not knowable -> least recent
            return _RECENCY_WINDOW_DAYS
        return min(float(delta), _RECENCY_WINDOW_DAYS)
    except ValueError:
        return _RECENCY_WINDOW_DAYS


def _read_graph(run_id: str) -> dict:
    artifact = runs.get_run_dir(run_id) / "discovery" / "graph.json"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"graph.json not found for run {run_id}; run graph/build first",
        )
    return run_cache.load_json(artifact)


def _read_communities(run_id: str) -> dict:
    artifact = runs.get_run_dir(run_id) / "discovery" / "communities.json"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"communities.json not found for run {run_id}; run themes/discover first",
        )
    return run_cache.load_json(artifact)


def _read_theme_snapshots(run_id: str) -> dict:
    artifact = runs.get_run_dir(run_id) / "discovery" / "theme_snapshots.json"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"theme_snapshots.json not found for run {run_id}; run themes/discover first",
        )
    return run_cache.load_json(artifact)


def _read_entities(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "entities.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"entities.parquet not found for run {run_id}; run extraction first",
        )
    return run_cache.load_parquet_rows(artifact)


def _read_edges(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "edges.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"edges.parquet not found for run {run_id}; run extraction first",
        )
    return run_cache.load_parquet_rows(artifact)


# ---------------------------------------------------------------------------
# Exposure computation
# ---------------------------------------------------------------------------


def compute_exposure(run_id: str, include_weak_signals: bool = False) -> int:
    """Compute company-theme exposure and write company_theme_exposure.parquet.

    Args:
        run_id: The run to process.
        include_weak_signals: When True, include llm_inferred and
            metadata_inferred edges in addition to document_stated ones.
            Defaults to False (OI-2 policy).

    Returns:
        Number of (company, theme) exposure rows written.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = manifest.as_of_date

    graph_doc = _read_graph(run_id)
    communities_doc = _read_communities(run_id)
    snapshots_doc = _read_theme_snapshots(run_id)
    entities = _read_entities(run_id)
    edges_raw = _read_edges(run_id)

    # Build entity lookups
    entity_by_id: dict[str, dict] = {}
    for ent in entities:
        eid = ent.get("entity_id") or ""
        if not eid:
            continue
        # Point-in-time: only entities first seen at or before as_of_date
        first_seen = _to_date_str(ent.get("first_seen_at", ""))
        if first_seen and first_seen > as_of_date:
            continue
        entity_by_id[eid] = ent

    # Identify Company entity ids
    company_ids: set[str] = {
        eid for eid, ent in entity_by_id.items()
        if ent.get("entity_type") == "Company"
    }

    # Company ticker lookup (from entities.parquet)
    company_ticker: dict[str, Optional[str]] = {
        eid: ent.get("ticker") or None
        for eid, ent in entity_by_id.items()
        if ent.get("entity_type") == "Company"
    }

    # Determine allowed extraction methods per OI-2 policy
    if include_weak_signals:
        allowed_methods: frozenset[str] = frozenset(
            {"document_stated", "llm_inferred", "metadata_inferred"}
        )
    else:
        # DEFAULT POLICY: only document_stated (OI-2)
        allowed_methods = frozenset({"document_stated"})

    # Filter edges to point-in-time + allowed extraction methods.
    # Structural edge types come from the shared graph_build set (ontology-derived,
    # excludes located_in) so exposure and community discovery stay consistent.
    _STRUCTURAL_EDGE_TYPES = frozenset(graph_build.STRUCTURAL_EDGE_TYPES)

    contributing_edges: list[dict] = []
    for edge in edges_raw:
        # Point-in-time gate (double-check at this layer)
        first_seen = _to_date_str(edge.get("first_seen_at", ""))
        if first_seen and first_seen > as_of_date:
            continue
        # OI-2 extraction method gate
        method = edge.get("extraction_method", "")
        if method not in allowed_methods:
            continue
        # Only structural edges contribute to exposure
        edge_type = edge.get("edge_type", "")
        if edge_type not in _STRUCTURAL_EDGE_TYPES:
            continue
        # Both endpoints must exist in entity_by_id
        src = edge.get("source_entity_id", "")
        tgt = edge.get("target_entity_id", "")
        if src not in entity_by_id or tgt not in entity_by_id:
            continue
        contributing_edges.append(edge)

    # Build adjacency: entity_id -> {neighbor_id: [edge]} for structural edges
    adjacency: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for edge in contributing_edges:
        src = edge["source_entity_id"]
        tgt = edge["target_entity_id"]
        adjacency[src][tgt].append(edge)
        adjacency[tgt][src].append(edge)  # undirected for distance computation

    # All node ids in the structural entity graph (non-Document, from graph.json)
    structural_node_ids: set[str] = {n["entity_id"] for n in graph_doc.get("nodes", [])}
    total_structural_nodes = len(structural_node_ids)

    # Compute degree centrality for company nodes
    # centrality = degree / (total_structural_nodes - 1)
    company_centrality: dict[str, float] = {}
    for cid in company_ids:
        if cid not in structural_node_ids:
            company_centrality[cid] = 0.0
            continue
        degree = len(adjacency.get(cid, {}))
        denom = max(1, total_structural_nodes - 1)
        company_centrality[cid] = min(1.0, degree / denom)

    # Build community_id -> theme_snapshot_id lookup
    community_to_snapshot: dict[str, str] = {}
    for snap in snapshots_doc.get("snapshots", []):
        cid = snap.get("community_id", "")
        sid = snap.get("theme_snapshot_id", "")
        if cid and sid:
            community_to_snapshot[cid] = sid

    # For each community, compute exposure for each Company
    exposure_rows: list[dict] = []

    communities = communities_doc.get("communities", [])

    for community in communities:
        community_id: str = community.get("community_id", "")
        community_node_set: set[str] = set(community.get("node_ids", []))

        # Skip communities with no nodes
        if not community_node_set:
            continue

        theme_snapshot_id = community_to_snapshot.get(community_id, "")
        if not theme_snapshot_id:
            continue

        # Precompute shortest distances from community member nodes to all reachable nodes
        # Use BFS from the community outward; more efficient than per-company BFS
        # for sparse graphs.
        # We compute BFS from each company node in company_ids outward to find
        # the distance to the nearest community node.
        # To avoid O(|companies| * |graph|) BFS, we do one BFS from the entire
        # community node set (multi-source BFS) to get distances to all nodes.

        community_distances: dict[str, float] = {}
        # Multi-source BFS: start from all community nodes simultaneously
        visited: dict[str, float] = {n: 0.0 for n in community_node_set if n in entity_by_id}
        queue: list[tuple[str, float]] = [(n, 0.0) for n in visited]
        qi = 0
        while qi < len(queue):
            node, dist = queue[qi]
            qi += 1
            for neighbor in adjacency.get(node, {}):
                if neighbor not in visited:
                    visited[neighbor] = dist + 1.0
                    queue.append((neighbor, dist + 1.0))
        community_distances = visited

        for company_id in company_ids:
            # Distance from company to community
            min_dist = community_distances.get(company_id)

            # If unreachable via structural edges, no exposure for this pair
            if min_dist is None:
                continue

            # If company is inside the community, distance = 0
            # Collect all structural edges connecting company to community nodes
            # (direct connections or via community members)
            contributing_edge_ids: list[str] = []
            all_evidence_chunk_ids: list[str] = []
            edge_confidences: list[float] = []
            recency_scores: list[float] = []

            # Direct edges: company <-> community node
            for comm_node in community_node_set:
                edges_to_comm = adjacency.get(company_id, {}).get(comm_node, [])
                for edge in edges_to_comm:
                    eid = edge.get("edge_id", "")
                    if not eid:
                        continue
                    if eid not in contributing_edge_ids:
                        contributing_edge_ids.append(eid)
                    conf = float(edge.get("confidence") or 0.0)
                    edge_confidences.append(conf)
                    chunk_ids = edge.get("evidence_chunk_ids") or []
                    for cid_ev in chunk_ids:
                        if cid_ev not in all_evidence_chunk_ids:
                            all_evidence_chunk_ids.append(cid_ev)
                    # Recency from first_seen_at
                    first_seen = _to_date_str(edge.get("first_seen_at", ""))
                    days_ago = _days_before(first_seen, as_of_date)
                    recency = 1.0 - (days_ago / _RECENCY_WINDOW_DAYS)
                    recency_scores.append(max(0.0, recency))

            # If company is a community member with no direct edges to other community
            # members found via adjacency, but has distance 0 (is IN community),
            # look at all its edges to community nodes
            if not contributing_edge_ids and company_id in community_node_set:
                for comm_node in community_node_set:
                    if comm_node == company_id:
                        continue
                    edges_to_comm = adjacency.get(company_id, {}).get(comm_node, [])
                    for edge in edges_to_comm:
                        eid = edge.get("edge_id", "")
                        if eid and eid not in contributing_edge_ids:
                            contributing_edge_ids.append(eid)
                            conf = float(edge.get("confidence") or 0.0)
                            edge_confidences.append(conf)
                            chunk_ids = edge.get("evidence_chunk_ids") or []
                            for cid_ev in chunk_ids:
                                if cid_ev not in all_evidence_chunk_ids:
                                    all_evidence_chunk_ids.append(cid_ev)
                            first_seen = _to_date_str(edge.get("first_seen_at", ""))
                            days_ago = _days_before(first_seen, as_of_date)
                            recency = 1.0 - (days_ago / _RECENCY_WINDOW_DAYS)
                            recency_scores.append(max(0.0, recency))

            # signal 1: distance score
            distance_score = 1.0 / (1.0 + min_dist)

            # signal 2: edge confidence sum (raw sum; normalize across all pairs below)
            edge_confidence_sum = sum(edge_confidences)

            # signal 3: evidence count (raw; normalize below)
            evidence_count = len(all_evidence_chunk_ids)

            # signal 4: recency
            recency_score = (
                sum(recency_scores) / len(recency_scores)
                if recency_scores else 0.5  # default mid-recency for in-community with no edges
            )

            # signal 5: centrality
            centrality_score = company_centrality.get(company_id, 0.0)

            # Store raw values; normalize signals 2 and 3 after collecting all rows
            exposure_rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "as_of_date": as_of_date,
                    "company_id": company_id,
                    "ticker": company_ticker.get(company_id),
                    "theme_snapshot_id": theme_snapshot_id,
                    "community_id": community_id,
                    # raw pre-normalization values stored temporarily
                    "_distance_score": distance_score,
                    "_edge_confidence_sum_raw": edge_confidence_sum,
                    "_evidence_count_raw": evidence_count,
                    "_recency_score": recency_score,
                    "_centrality_score": centrality_score,
                    # contract fields
                    "graph_distance": float(min_dist),
                    "edge_confidence_sum": float(edge_confidence_sum),
                    "evidence_count": evidence_count,
                    "top_evidence_chunk_ids": all_evidence_chunk_ids[:10],
                    "calculation_method": (
                        "exposure_v1_document_stated"
                        if not include_weak_signals
                        else "exposure_v1_include_weak_signals"
                    ),
                    # contributing edge ids stored for traceability
                    "_contributing_edge_ids": contributing_edge_ids,
                }
            )

    # --- Normalize signals 2 and 3 across all rows ---
    if exposure_rows:
        max_confidence_sum = max(
            r["_edge_confidence_sum_raw"] for r in exposure_rows
        )
        max_evidence = max(r["_evidence_count_raw"] for r in exposure_rows)
    else:
        max_confidence_sum = 1.0
        max_evidence = 1.0

    # Denominator for log normalization; avoid log(0)
    log_max_evidence = math.log1p(max(1, max_evidence))

    for row in exposure_rows:
        # Normalize confidence sum to [0, 1]
        conf_norm = (
            row["_edge_confidence_sum_raw"] / max_confidence_sum
            if max_confidence_sum > 0
            else 0.0
        )
        # Normalize evidence count to [0, 1] via log1p
        evid_norm = math.log1p(row["_evidence_count_raw"]) / log_max_evidence

        exposure_score = (
            _W_DISTANCE * row["_distance_score"]
            + _W_EDGE_CONFIDENCE * conf_norm
            + _W_EVIDENCE * evid_norm
            + _W_RECENCY * row["_recency_score"]
            + _W_CENTRALITY * row["_centrality_score"]
        )
        row["exposure_score"] = round(exposure_score, 6)

    # Remove internal fields before writing
    output_rows: list[dict] = []
    for row in exposure_rows:
        clean = {k: row[k] for k in EXPOSURE_COLUMNS}
        output_rows.append(clean)

    # Sort deterministically by (community_id, company_id) for reproducibility
    output_rows.sort(key=lambda r: (r["community_id"], r["company_id"]))

    # Write parquet
    _write_exposure_table(output_rows, runs.get_run_dir(run_id) / "discovery" / "company_theme_exposure.parquet")

    return len(output_rows)


def _write_exposure_table(rows: list[dict], out_path: Path) -> None:
    """Write company_theme_exposure.parquet with correct schema."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("as_of_date", pa.string()),
            ("company_id", pa.string()),
            ("ticker", pa.string()),
            ("theme_snapshot_id", pa.string()),
            ("community_id", pa.string()),
            ("exposure_score", pa.float64()),
            ("graph_distance", pa.float64()),
            ("edge_confidence_sum", pa.float64()),
            ("evidence_count", pa.int64()),
            ("top_evidence_chunk_ids", pa.list_(pa.string())),
            ("calculation_method", pa.string()),
        ])
        empty: dict = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    # Build typed arrays column by column
    arrays: dict[str, pa.Array] = {}
    for col in EXPOSURE_COLUMNS:
        values = [row.get(col) for row in rows]
        if col in {"exposure_score", "graph_distance", "edge_confidence_sum"}:
            arrays[col] = pa.array(values, type=pa.float64())
        elif col == "evidence_count":
            arrays[col] = pa.array(values, type=pa.int64())
        elif col == "top_evidence_chunk_ids":
            arrays[col] = pa.array(values, type=pa.list_(pa.string()))
        else:
            # string columns (nullable)
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )

    table = pa.table(arrays)
    pq.write_table(table, out_path)
