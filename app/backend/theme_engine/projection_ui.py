"""FI-F: Projection UI backend helpers (GitHub #109).

Workstream P-F per docs/design_forward_inference.md.

Reads projected_impacts.parquet (FI-C artifact) and graph.json, then shapes
data for the ScenarioView:

  list_projection_triggers(run_id) -> list of triggers present in the artifact
  get_projections(run_id, trigger_id) -> ranked impacts + path_graph per impact

PIT-clean by inheritance
-------------------------
projected_impacts.parquet is already PIT-clean (built by FI-C from the
PIT-filtered graph.json).  This module reads it as-is without re-filtering.

Sign-blind caveat (issue #110)
-------------------------------
``causes``, ``exposed_to``, and ``sensitive_to`` edges have base_polarity = +1
unconditionally in v1.  Impacts whose path contains only those edge types have
provisional direction.  We surface this caveat in the response so the UI can
display the appropriate warning.

v1 scope
---------
Data-driven browse only (no user scenario input).  ScenarioView shows the
system's auto-generated projections.  Projections are HYPOTHETICAL — the UI
must never style them as stated facts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from . import run_cache, runs

# Edge types whose direction is provisional until issue #110 is resolved.
_SIGN_BLIND_EDGE_TYPES: frozenset[str] = frozenset({
    "causes", "exposed_to", "sensitive_to",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_projected_impacts(run_id: str, as_of: str | None = None) -> list[dict]:
    """Read projected_impacts.parquet or raise 404/409."""
    p = runs.discovery_point_dir(run_id, as_of) / "projected_impacts.parquet"
    if not p.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"projected_impacts.parquet not found for run {run_id}. "
                "Run POST /api/fi/compute-projections first."
            ),
        )
    return run_cache.load_parquet_rows(p)


def _read_graph(run_id: str, as_of: str | None = None) -> dict:
    """Read graph.json or return an empty graph structure (graceful)."""
    p = runs.discovery_point_dir(run_id, as_of) / "graph.json"
    if not p.exists():
        return {"nodes": [], "edges": []}
    try:
        return run_cache.load_json(p)
    except Exception:
        return {"nodes": [], "edges": []}


def _build_node_index(graph: dict) -> dict[str, dict]:
    return {
        n["entity_id"]: n
        for n in graph.get("nodes", [])
        if n.get("entity_id")
    }


def _build_edge_index(graph: dict) -> dict[str, dict]:
    return {
        e["edge_id"]: e
        for e in graph.get("edges", [])
        if e.get("edge_id")
    }


def _path_graph(
    path_edge_ids: list[str],
    edge_index: dict[str, dict],
    node_index: dict[str, dict],
) -> dict:
    """Build a minimal LayeredGraph-compatible subgraph for the given path.

    The path is a list of edge_ids.  We resolve each edge to its source and
    target, collect the unique node_ids, and return:

        {
          "nodes": [{"id": ..., "label": ..., "entity_type": ..., "level": ...}],
          "edges": [{"source": ..., "target": ..., "edge_type": ...}],
        }

    Nodes or edges missing from the index are silently skipped so the graph
    is always well-formed (empty in the worst case).
    """
    seen_nodes: set[str] = set()
    graph_nodes: list[dict] = []
    graph_edges: list[dict] = []

    for eid in path_edge_ids:
        edge = edge_index.get(eid)
        if edge is None:
            continue
        src = edge.get("source_entity_id", "")
        tgt = edge.get("target_entity_id", "")
        edge_type = edge.get("edge_type") or edge.get("type") or "relates_to"

        graph_edges.append({"source": src, "target": tgt, "edge_type": edge_type})

        for nid in (src, tgt):
            if nid and nid not in seen_nodes:
                seen_nodes.add(nid)
                node = node_index.get(nid, {})
                graph_nodes.append({
                    "id": nid,
                    "label": node.get("label") or node.get("name") or nid,
                    "entity_type": node.get("entity_type", "Unknown"),
                    "level": node.get("level") or "unknown",
                })

    return {"nodes": graph_nodes, "edges": graph_edges}


def _sign_blind_flag(
    path_edge_ids: list[str],
    edge_index: dict[str, dict],
) -> bool:
    """Return True when ALL path edges are sign-blind (issue #110).

    A True value means the direction on this impact is provisional.
    If any edge in the path uses a directional type (benefits, hurts, etc.),
    the sign is considered reliable.
    """
    if not path_edge_ids:
        return False
    for eid in path_edge_ids:
        edge = edge_index.get(eid, {})
        etype = (edge.get("edge_type") or edge.get("type") or "").lower()
        if etype not in _SIGN_BLIND_EDGE_TYPES:
            return False  # at least one directional edge
    return True  # all edges are sign-blind


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_projection_triggers(run_id: str, as_of: str | None = None) -> dict:
    """Return the set of Event triggers present in projected_impacts.parquet.

    Each trigger entry carries:
      - trigger_id: str
      - trigger_kind: str (always "Event" in v1)
      - label: str — human-readable label from graph.json (falls back to trigger_id)
      - company_count: int — number of distinct companies reached

    Returns { as_of_date, triggers: [...] } sorted alphabetically by label.

    Raises 404 if projected_impacts.parquet does not exist yet.
    """
    rows = _read_projected_impacts(run_id, as_of)
    graph = _read_graph(run_id, as_of)
    node_index = _build_node_index(graph)

    manifest = runs.load_manifest(run_id)
    as_of_date = as_of if as_of is not None else (manifest.as_of_date if manifest else "")

    # Aggregate: trigger_id -> {kind, companies set}
    seen: dict[str, dict] = {}
    for row in rows:
        tid = row.get("trigger_id") or ""
        if not tid:
            continue
        if tid not in seen:
            seen[tid] = {
                "trigger_id": tid,
                "trigger_kind": row.get("trigger_kind", "Event"),
                "companies": set(),
            }
        company_id = row.get("company_id") or ""
        if company_id:
            seen[tid]["companies"].add(company_id)

    triggers = []
    for tid, info in seen.items():
        node = node_index.get(tid, {})
        label = node.get("label") or node.get("name") or tid
        triggers.append({
            "trigger_id": tid,
            "trigger_kind": info["trigger_kind"],
            "label": label,
            "company_count": len(info["companies"]),
        })

    triggers.sort(key=lambda t: t["label"].lower())

    return {
        "as_of_date": as_of_date,
        "trigger_count": len(triggers),
        "triggers": triggers,
    }


def get_projections(run_id: str, trigger_id: str, as_of: str | None = None) -> dict:
    """Return ranked projected impacts for a single trigger.

    Each impact in the response carries:
      - company_id: str
      - company_name: str
      - direction: int (+1 | -1)
      - strength: float (ordinal, NOT a probability)
      - confidence: float (mean propagation_weight along primary path)
      - sign_blind: bool — True when direction is provisional (issue #110)
      - path: [edge_id, ...]  — edge chain from trigger to company
      - path_graph: { nodes, edges } — LayeredGraph-compatible subgraph
      - evidence_chunk_ids: [chunk_id, ...]

    Impacts are ranked by abs(strength) descending (strongest first).

    When the trigger exists in the artifact but reaches no companies, returns
    an empty impacts list with empty_reason set — never silently blank.

    Raises 404 if projected_impacts.parquet does not exist, or if trigger_id
    is not present in the artifact (no-reach triggers produce empty list with
    empty_reason, not 404).
    """
    all_rows = _read_projected_impacts(run_id, as_of)
    graph = _read_graph(run_id, as_of)
    node_index = _build_node_index(graph)
    edge_index = _build_edge_index(graph)

    manifest = runs.load_manifest(run_id)
    as_of_date = as_of if as_of is not None else (manifest.as_of_date if manifest else "")

    # Filter to requested trigger
    rows = [r for r in all_rows if r.get("trigger_id") == trigger_id]

    # Validate trigger_id exists (even with zero company impacts)
    # A trigger is "present" if it had at least one impact row.
    # If NO row has this trigger_id at all, it might be a typo.
    all_trigger_ids = {r.get("trigger_id") for r in all_rows if r.get("trigger_id")}
    trigger_in_artifact = trigger_id in all_trigger_ids

    # Resolve trigger label
    trigger_node = node_index.get(trigger_id, {})
    trigger_label = trigger_node.get("label") or trigger_node.get("name") or trigger_id
    trigger_kind = rows[0].get("trigger_kind", "Event") if rows else "Event"

    # Build impacts
    impacts: list[dict] = []
    for row in rows:
        company_id = row.get("company_id") or ""
        cnode = node_index.get(company_id, {})
        company_name = cnode.get("label") or cnode.get("name") or company_id

        path_edge_ids: list[str] = row.get("path") or []
        evidence_chunk_ids: list[str] = row.get("evidence_chunk_ids") or []
        direction: int = int(row.get("direction") or 0)
        strength: float = float(row.get("strength") or 0.0)
        confidence: float = float(row.get("confidence") or 0.0)

        pg = _path_graph(path_edge_ids, edge_index, node_index)
        sign_blind = _sign_blind_flag(path_edge_ids, edge_index)

        impacts.append({
            "company_id": company_id,
            "company_name": company_name,
            "direction": direction,
            "strength": strength,
            "confidence": confidence,
            "sign_blind": sign_blind,
            "path": path_edge_ids,
            "path_graph": pg,
            "evidence_chunk_ids": evidence_chunk_ids,
        })

    # Sort by absolute strength descending
    impacts.sort(key=lambda x: abs(x["strength"]), reverse=True)

    # Explicit empty reason — never silently blank
    empty_reason: Optional[str] = None
    if not impacts:
        if trigger_in_artifact:
            empty_reason = (
                f"Trigger '{trigger_label}' exists in the graph but its forward "
                "propagation did not reach any Company nodes at the current PIT date."
            )
        else:
            empty_reason = (
                f"Trigger '{trigger_id}' was not found in projected_impacts.parquet. "
                "It may not be an Event node in this run's graph."
            )

    return {
        "trigger_id": trigger_id,
        "trigger_kind": trigger_kind,
        "trigger_label": trigger_label,
        "as_of_date": as_of_date,
        "impact_count": len(impacts),
        "empty_reason": empty_reason,
        "impacts": impacts,
    }
