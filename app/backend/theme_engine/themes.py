"""Theme discovery service (M4): community detection + theme snapshots.

Reads:
  - ``discovery/graph.json``          (io_contracts.md section 13)

Writes:
  - ``discovery/communities.json``    (io_contracts.md section 14)
  - ``discovery/theme_snapshots.json`` (io_contracts.md section 15)
  - ``discovery/theme_lineage.json``  (io_contracts.md section 16)
  - ``discovery/theme_metrics.parquet`` (io_contracts.md section 17)

Algorithm:
  - Community detection uses networkx Louvain (deterministic, fixed seed=42).
  - Community ids are produced by the algorithm (not manual labels).
  - theme_name / theme_summary are interpretation metadata only.
    In this stage: deterministic placeholders using top entity names.
    naming_model='deterministic' — NO LLM calls in this stage.

Single-snapshot gating (spec §20, MVP Caveats):
  - Only single-snapshot metrics (strength, cohesion, saturation) are computed.
  - Temporal metrics (momentum, birth_score, novelty, acceleration) are None.
  - theme_lineage.json is written with lineage_mode='single_snapshot' and
    an empty lineages list.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

SCHEMA_VERSION = "1.0"

# Fixed community detection seed for determinism
_LOUVAIN_SEED = 42

# Columns for theme_metrics.parquet (io_contracts §17)
THEME_METRICS_COLUMNS: list[str] = [
    "schema_version",
    "theme_snapshot_id",
    "community_id",
    "as_of_date",
    "strength",
    "momentum",
    "birth_score",
    "cohesion",
    "novelty",
    "saturation",
    "macro_linkage",
    "commodity_linkage",
]


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_community_id(run_id: str, community_index: int) -> str:
    """Deterministic community_id for a given run and community index."""
    basis = f"community:{run_id}:{community_index}"
    suffix = _sha256_hex(basis)[:8]
    return f"community_{community_index:03d}_{suffix}"


def _stable_snapshot_id(as_of_date: str, community_id: str) -> str:
    """Deterministic theme_snapshot_id for a community at a given date."""
    date_part = as_of_date.replace("-", "")
    basis = f"snapshot:{as_of_date}:{community_id}"
    suffix = _sha256_hex(basis)[:8]
    return f"theme_{date_part}_{suffix}"


def _read_graph(run_id: str) -> dict:
    artifact = runs.get_run_dir(run_id) / "discovery" / "graph.json"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"graph.json not found for run {run_id}; run graph/build first",
        )
    return json.loads(artifact.read_text(encoding="utf-8"))


def _graph_density(num_nodes: int, num_edges: int) -> float:
    """Compute graph density for an undirected graph."""
    if num_nodes < 2:
        return 0.0
    max_edges = num_nodes * (num_nodes - 1) / 2
    return min(1.0, num_edges / max_edges) if max_edges > 0 else 0.0


def _community_strength(
    community_nodes: set[str],
    community_edge_ids: set[str],
    edge_lookup: dict[str, dict],
) -> float:
    """Strength = sum of edge confidences (weights) within the community.

    Spec §20: weighted evidence and edge count.
    Normalized to [0, 1] by dividing by max possible weight assuming all edges weight 1.0.
    """
    if not community_edge_ids:
        return 0.0
    total_weight = sum(
        float(edge_lookup[eid].get("weight", 0.0))
        for eid in community_edge_ids
        if eid in edge_lookup
    )
    # Normalize by number of edges (average weight)
    return total_weight / max(1, len(community_edge_ids))


def discover_themes(run_id: str) -> int:
    """Run community detection on the entity-only structural graph.

    Returns the number of communities discovered.

    Writes communities.json, theme_snapshots.json, theme_lineage.json,
    and theme_metrics.parquet.
    """
    import networkx as nx  # noqa: PLC0415

    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date = manifest.as_of_date

    graph_doc = _read_graph(run_id)
    nodes: list[dict] = graph_doc.get("nodes", [])
    edges: list[dict] = graph_doc.get("edges", [])
    community_input_edge_ids: list[str] = graph_doc.get("community_input_edges", [])

    # Build a lookup from edge_id -> edge dict
    edge_lookup: dict[str, dict] = {e["edge_id"]: e for e in edges}

    # Build entity type lookup for top_companies / top_entities extraction
    entity_type_lookup: dict[str, str] = {
        n["entity_id"]: n.get("entity_type", "") for n in nodes
    }
    entity_label_lookup: dict[str, str] = {
        n["entity_id"]: n.get("label", n["entity_id"]) for n in nodes
    }

    # Build a set of community input edges
    structural_edge_set: set[str] = set(community_input_edge_ids)

    # Collect structural edges for the networkx graph
    structural_edges_data: list[tuple[str, str, dict]] = []
    for eid in community_input_edge_ids:
        if eid not in edge_lookup:
            continue
        e = edge_lookup[eid]
        src = e.get("source_entity_id", "")
        tgt = e.get("target_entity_id", "")
        weight = float(e.get("weight", 1.0)) or 1.0
        if src and tgt:
            structural_edges_data.append((src, tgt, {"weight": weight, "edge_id": eid}))

    # Build networkx undirected graph for Louvain
    G = nx.Graph()

    # Add all structural nodes
    for node in nodes:
        G.add_node(node["entity_id"])

    # Add structural edges
    for src, tgt, attrs in structural_edges_data:
        if G.has_edge(src, tgt):
            # Accumulate weight if duplicate edge
            G[src][tgt]["weight"] = G[src][tgt].get("weight", 0.0) + attrs["weight"]
        else:
            G.add_edge(src, tgt, weight=attrs["weight"])

    # Run community detection (deterministic, fixed seed)
    # Use Louvain with a fixed seed for determinism.
    # Handle isolated nodes: Louvain on connected graph; isolated nodes get their own
    # community or are merged as singletons.
    if G.number_of_nodes() == 0:
        raw_communities: list[set[str]] = []
    elif G.number_of_edges() == 0:
        # All nodes isolated: each gets its own singleton community
        raw_communities = [{n} for n in G.nodes()]
    else:
        raw_communities = list(
            nx.community.louvain_communities(G, weight="weight", seed=_LOUVAIN_SEED)
        )

    # Build community records
    communities_list: list[dict] = []
    snapshot_list: list[dict] = []
    metric_rows: list[dict] = []

    for idx, node_set in enumerate(raw_communities):
        community_id = _stable_community_id(run_id, idx)
        node_ids = sorted(node_set)

        # Find structural edges within this community
        community_node_set: set[str] = set(node_ids)
        community_edge_ids: list[str] = []
        for eid in community_input_edge_ids:
            if eid not in edge_lookup:
                continue
            e = edge_lookup[eid]
            src = e.get("source_entity_id", "")
            tgt = e.get("target_entity_id", "")
            if src in community_node_set and tgt in community_node_set:
                community_edge_ids.append(eid)

        size = len(node_ids)
        density = _graph_density(size, len(community_edge_ids))
        strength = _community_strength(community_node_set, set(community_edge_ids), edge_lookup)

        # top_entities: non-Company canonical names sorted alphabetically (up to 5)
        non_company = sorted(
            entity_label_lookup[nid]
            for nid in node_ids
            if entity_type_lookup.get(nid) not in ("Company",)
        )[:5]

        # top_companies: Company labels sorted alphabetically (up to 5)
        top_companies = sorted(
            entity_label_lookup[nid]
            for nid in node_ids
            if entity_type_lookup.get(nid) == "Company"
        )[:5]

        # Deterministic theme_name placeholder from top entity labels
        name_parts = (top_companies + non_company)[:3]
        theme_name = " + ".join(name_parts) if name_parts else f"Community_{idx}"
        theme_summary = (
            f"Community of {size} entities connected by {len(community_edge_ids)} "
            f"structural edges. (deterministic placeholder)"
        )

        community_record: dict = {
            "community_id": community_id,
            "node_ids": node_ids,
            "edge_ids": community_edge_ids,
            "size": size,
            "density": density,
            "top_entities": non_company,
            "top_companies": top_companies,
            "theme_name": theme_name,
            "theme_summary": theme_summary,
            "naming_model": "deterministic",
        }
        communities_list.append(community_record)

        # Theme snapshot
        snapshot_id = _stable_snapshot_id(as_of_date, community_id)
        snapshot_record: dict = {
            "theme_snapshot_id": snapshot_id,
            "community_id": community_id,
            "theme_family_id": None,
            "state": "Emerging",
            "theme_name": theme_name,
            "summary": theme_summary,
            "evidence_edge_ids": community_edge_ids,
        }
        snapshot_list.append(snapshot_record)

        # Theme metrics (single-snapshot only)
        # Saturation: coverage ratio = community_size / total_structural_nodes
        total_structural_nodes = len(nodes)
        saturation = size / max(1, total_structural_nodes)

        metric_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "theme_snapshot_id": snapshot_id,
                "community_id": community_id,
                "as_of_date": as_of_date,
                "strength": float(strength),
                "momentum": None,        # temporal — requires lineage; SKIPPED
                "birth_score": None,     # temporal — requires lineage; SKIPPED
                "cohesion": float(density),
                "novelty": None,         # temporal — requires lineage; SKIPPED
                "saturation": float(saturation),
                "macro_linkage": None,
                "commodity_linkage": None,
            }
        )

    # Write communities.json
    communities_doc: dict = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_date": as_of_date,
        "algorithm": "louvain",
        "communities": communities_list,
    }

    # Write theme_snapshots.json
    snapshots_doc: dict = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_date": as_of_date,
        "snapshots": snapshot_list,
    }

    # Write theme_lineage.json (single_snapshot: empty lineages)
    lineage_doc: dict = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_date": as_of_date,
        "lineage_mode": "single_snapshot",
        "lineages": [],
    }

    discovery_dir = runs.get_run_dir(run_id) / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)

    (discovery_dir / "communities.json").write_text(
        json.dumps(communities_doc, indent=2), encoding="utf-8"
    )
    (discovery_dir / "theme_snapshots.json").write_text(
        json.dumps(snapshots_doc, indent=2), encoding="utf-8"
    )
    (discovery_dir / "theme_lineage.json").write_text(
        json.dumps(lineage_doc, indent=2), encoding="utf-8"
    )

    # Write theme_metrics.parquet
    _write_metrics_table(metric_rows, discovery_dir / "theme_metrics.parquet")

    return len(communities_list)


def _write_metrics_table(rows: list[dict], out_path: Path) -> None:
    """Write theme_metrics.parquet with correct types for list-able nullable columns."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        # Empty schema-valid table
        schema = pa.schema(
            [
                ("schema_version", pa.string()),
                ("theme_snapshot_id", pa.string()),
                ("community_id", pa.string()),
                ("as_of_date", pa.string()),
                ("strength", pa.float64()),
                ("momentum", pa.float64()),
                ("birth_score", pa.float64()),
                ("cohesion", pa.float64()),
                ("novelty", pa.float64()),
                ("saturation", pa.float64()),
                ("macro_linkage", pa.float64()),
                ("commodity_linkage", pa.float64()),
            ]
        )
        empty: dict = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    pydict: dict[str, list] = {col: [row.get(col) for row in rows] for col in THEME_METRICS_COLUMNS}

    # Cast numeric columns to float64 (None -> null)
    float_cols = {
        "strength", "momentum", "birth_score", "cohesion",
        "novelty", "saturation", "macro_linkage", "commodity_linkage",
    }
    arrays: dict[str, pa.Array] = {}
    for col, values in pydict.items():
        if col in float_cols:
            arrays[col] = pa.array(values, type=pa.float64())
        else:
            arrays[col] = pa.array([str(v) if v is not None else None for v in values], type=pa.string())

    table = pa.table(arrays)
    pq.write_table(table, out_path)
