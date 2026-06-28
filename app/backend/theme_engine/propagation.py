"""Deterministic forward-inference propagation engine (FI-B, GitHub #105).

Given a trigger node and a signed shock, propagates outward along signed
edges from graph.json and returns an ordinal impact ranking for company nodes.

Workstream P-B per docs/design_forward_inference.md.
Depends on FI-A: graph.json edges carry ``polarity`` and ``propagation_weight``.

Two-layer design
----------------
FI-B (this module) — deterministic core: propagate() returns in-memory impacts.
FI-C               — persistence layer: writes projected_impacts artifact.
FI-D               — LLM narrative layer: generates human-readable explanations.

Reads
-----
graph.json (io_contracts §13) produced by graph_build.py.  FI-B accepts the
parsed dict directly (caller loads from disk); it does NOT write any artifact
(FI-C handles persistence).

Algorithm
---------
For each path from trigger_id to a Company node (hop-count <= max_hops):

    sign(path)     = product of edge polarities along the path
    weight(path)   = product of propagation_weights along the path
    contribution   = shock * sign(path) * weight(path) * decay ^ len(path)

Contributions across all paths to the same company are sign-aware summed
(a positive path and a negative path partially cancel each other).  The
final aggregate determines:
    direction : int   — sign of aggregate (+1 or -1)
    strength  : float — absolute aggregate; ordinal ranking only
                        (NOT a calibrated probability / percentage)

Only edges with polarity != 0 propagate.  polarity=0 edges (co_occurs_with,
mentioned_in, located_in, …) carry no causal signal and are skipped.

Point-in-time
-------------
graph.json is built by graph_build.py with first_seen_at <= as_of_date applied
(fail-closed: undated items excluded).  FI-B trusts this PIT guarantee and does
NOT re-filter edges read from graph.json.

For raw graph dicts supplied in tests, if an edge carries an ``available_at``
field and ``as_of_date`` is known (from the graph dict's top-level field or the
``as_of_date`` kwarg), edges with available_at > as_of_date are excluded.  This
makes the PIT contract testable without double-filtering production graphs.

#110 (landed) — evidence-backed direction for causal / exposure edges
----------------------------------------------------------------------
``causes``, ``exposed_to``, and ``sensitive_to`` edges now carry a per-instance
``direction`` field in edges.parquet (+1/-1/0) derived from text evidence.
graph_build.py sets graph-edge ``polarity`` = that direction (0 if unknown).

Locked design decision: unknown direction -> polarity=0 -> edge is excluded
from signed propagation.  This replaces the old unconditional +1 and makes
the propagation honest: only explicitly-evidenced signs reach companies.

FI-B reads whatever polarity is present on the edge and needs no code change.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Public defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_HOPS: int = 3
DEFAULT_DECAY: float = 0.8

# Only Company nodes are impact targets
_COMPANY_TYPE: str = "Company"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_date_str(val: Any) -> str:
    """Coerce a value to YYYY-MM-DD string for PIT comparison."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


def _build_index(
    graph: dict,
    as_of_date: Optional[str] = None,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Build node lookup and directed adjacency from a graph dict.

    Returns
    -------
    node_by_id : dict[str, dict]
        entity_id -> node dict for every node in ``graph["nodes"]``.
    adj : dict[str, list[dict]]
        Directed adjacency: source_entity_id -> list of outgoing edge dicts
        that have polarity != 0 and pass the optional PIT gate.

    PIT gate
    --------
    Production graph.json is PIT-built by graph_build.py (first_seen_at <=
    as_of_date, fail-closed).  Edges in graph.json do NOT carry ``available_at``
    so no per-edge filtering is needed here.

    Test fixtures may add ``available_at`` to edges for explicit PIT testing.
    If an edge has ``available_at`` AND an effective as_of date is known, edges
    with available_at > as_of_date are excluded.  This avoids double-filtering
    production graphs while keeping PIT behaviour testable.
    """
    effective_as_of: str = (
        as_of_date
        or _to_date_str(graph.get("as_of_date"))
        or ""
    )

    # Node lookup: support both entity_id (graph.json) and generic node_id
    node_by_id: dict[str, dict] = {}
    for node in graph.get("nodes", []):
        nid = node.get("entity_id") or node.get("node_id") or ""
        if nid:
            node_by_id[nid] = node

    # Directed adjacency: source -> [edge, ...]
    adj: dict[str, list[dict]] = defaultdict(list)
    for edge in graph.get("edges", []):
        src = edge.get("source_entity_id") or edge.get("source_id") or ""
        tgt = edge.get("target_entity_id") or edge.get("target_id") or ""
        if not src or not tgt:
            continue

        # PIT gate — only when available_at is explicitly present on the edge
        available_at = _to_date_str(edge.get("available_at"))
        if available_at and effective_as_of and available_at > effective_as_of:
            continue  # future-dated edge excluded

        # Skip zero-polarity edges (undirected / evidence-only)
        polarity = edge.get("polarity", 0)
        if polarity == 0:
            continue

        adj[src].append(edge)

    return node_by_id, dict(adj)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def propagate(
    graph: dict,
    trigger_id: str,
    shock: float,
    max_hops: int = DEFAULT_MAX_HOPS,
    decay: float = DEFAULT_DECAY,
    as_of_date: Optional[str] = None,
) -> list[dict]:
    """Propagate a signed shock from ``trigger_id`` and return company impacts.

    Parameters
    ----------
    graph : dict
        Parsed graph.json dict with ``nodes`` and ``edges``.  Edges must carry
        ``polarity`` (int in {-1, 0, +1}) and ``propagation_weight`` (float in
        (0, 1]) as written by graph_build.py (FI-A substrate).
    trigger_id : str
        entity_id of the triggering node (e.g. a macro factor).
    shock : float
        Signed initial shock magnitude.  Positive = the trigger node improves;
        negative = it deteriorates.  Typical values: +1.0 or -1.0.
    max_hops : int
        Maximum path length (hop cap). Default 3.
    decay : float
        Per-hop decay multiplier in (0, 1]. Default 0.8.  A 1-hop path is
        multiplied by decay^1, a 2-hop path by decay^2, etc.
    as_of_date : str | None
        Optional YYYY-MM-DD.  Used to gate edges that carry ``available_at``
        (test fixtures).  Production graph.json is already PIT-filtered.

    Returns
    -------
    list[dict]
        One entry per impacted company (non-zero aggregate), sorted by
        descending ``strength`` (strongest first), ties broken by company_id.

        Each entry:
            ``company_id`` : str   — entity_id of the impacted company
            ``direction``  : int   — +1 (positive impact) or -1 (negative)
            ``strength``   : float — absolute aggregate; ordinal rank only
                                     (NOT a calibrated % or probability)
            ``paths``      : list[list[str]] — each path as a list of edge_ids

    Algorithm details
    -----------------
    DFS from trigger_id along directed, non-zero-polarity edges.

    For each path to a Company node at hop depth h:
        sign(path)     = product of edge polarities (int: +1 or -1 each)
        weight(path)   = product of propagation_weights (float)
        contribution   = shock * sign(path) * weight(path) * decay^h

    Sign-aware aggregation:
        company_aggregate[c] = sum of all contributions reaching c

    direction = sign(company_aggregate[c])
    strength  = abs(company_aggregate[c])

    Cycles are prevented by a per-path visited set (node ids already on the
    current path are never revisited).  The algorithm is seedless and produces
    identical output for identical inputs (deterministic).
    """
    if not trigger_id or shock == 0.0:
        return []

    node_by_id, adj = _build_index(graph, as_of_date=as_of_date)

    # Signed aggregate per company and their contributing paths
    company_aggregate: dict[str, float] = defaultdict(float)
    company_paths: dict[str, list[list[str]]] = defaultdict(list)

    # DFS stack entries:
    # (node_id, path_sign, path_weight, hop_count, edge_ids_so_far, visited_node_set)
    # path_sign is the product of polarities so far (int: +1 or -1).
    # path_weight is the product of propagation_weights so far (float).
    stack: list[tuple[str, int, float, int, list[str], frozenset[str]]] = [
        (trigger_id, 1, 1.0, 0, [], frozenset({trigger_id}))
    ]

    while stack:
        node_id, path_sign, path_weight, hops, edge_ids, visited = stack.pop()

        # Record impact when we reach a Company node (not the trigger itself)
        if hops > 0:
            node = node_by_id.get(node_id)
            if node is not None and node.get("entity_type") == _COMPANY_TYPE:
                contribution = shock * path_sign * path_weight * (decay ** hops)
                company_aggregate[node_id] += contribution
                company_paths[node_id].append(list(edge_ids))

        # Stop deepening beyond max_hops
        if hops >= max_hops:
            continue

        # Expand outgoing edges
        for edge in adj.get(node_id, []):
            tgt = edge.get("target_entity_id") or edge.get("target_id") or ""
            if not tgt or tgt in visited:
                continue  # skip missing targets and cycles

            polarity: int = int(edge.get("polarity", 0))
            if polarity == 0:
                continue  # guard (already filtered in _build_index)

            prop_weight: float = float(edge.get("propagation_weight") or 1.0)
            edge_id: str = edge.get("edge_id") or ""

            stack.append((
                tgt,
                path_sign * polarity,
                path_weight * prop_weight,
                hops + 1,
                edge_ids + [edge_id],
                visited | {tgt},
            ))

    # Build result list
    results: list[dict] = []
    for company_id, aggregate in company_aggregate.items():
        if aggregate == 0.0:
            continue  # fully cancelled paths: no net impact
        direction: int = 1 if aggregate > 0 else -1
        strength: float = abs(aggregate)
        # Sort paths lexicographically for a canonical, deterministic order
        # (DFS stack pops vary by edge-list order; sorting makes output stable)
        sorted_paths = sorted(company_paths[company_id])
        results.append(
            {
                "company_id": company_id,
                "direction": direction,
                "strength": strength,
                "paths": sorted_paths,
            }
        )

    # Sort deterministically: descending strength, then company_id for ties
    results.sort(key=lambda r: (-r["strength"], r["company_id"]))

    return results
