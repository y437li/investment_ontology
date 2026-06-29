"""Theme Slice Engine.

A slice = (anchor + propagation + filter): a deterministic, connected projection of
the PIT structural graph reachable from one anchor node. Pure graph traversal, no LLM,
no NetworkX. Mirrors subgraph.py conventions and main.py error mapping.

Source of truth is discovery/graph.json ONLY — graph_build already dropped every
entity/edge with first_seen_at > as_of_date, so the slice inherits PIT correctness
with zero date logic here.
"""

from __future__ import annotations

from . import registry, run_cache, runs, graph_build

_EVIDENCE_EDGE_TYPES = set(graph_build.EVIDENCE_EDGE_TYPES)  # {"mentioned_in","co_occurs_with"}
# Default structural admit set = exactly what community discovery uses.
_DEFAULT_EDGE_TYPES = set(graph_build.STRUCTURAL_EDGE_TYPES)
_DEFAULT_METHODS = set(graph_build.COMMUNITY_INPUT_METHODS)  # {"document_stated","metadata_inferred"}

_LEVEL_VOCAB = {"macro", "industry", "company", "idiosyncratic", "contextual", "evidence"}


class AnchorNotFound(ValueError):
    """Raised when an anchor token resolves to zero nodes. Carries .candidates."""

    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class AnchorAmbiguous(ValueError):
    """Raised when an anchor token resolves to >1 node (tier 2/3). Carries .candidates."""

    def __init__(self, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


def _load_graph(run_id: str, as_of: str | None = None) -> dict:
    """Read discovery/graph.json (PIT-filtered at build time). FileNotFoundError if absent."""
    dd = runs.discovery_point_dir(run_id, as_of)
    return run_cache.load_json(dd / "graph.json")


def _candidate(node: dict) -> dict:
    return {"entity_id": node["entity_id"], "label": node.get("label") or node["entity_id"]}


def resolve_anchor(graph: dict, anchor: str) -> dict:
    """Resolve a token to a node dict from graph['nodes'].

    Match priority (first non-empty tier wins):
      1. exact entity_id
      2. exact label, case-insensitive
      3. unique case-insensitive substring of label
    Returns the raw node dict {entity_id, entity_type, label}.
    Raises AnchorNotFound (zero matches) or AnchorAmbiguous (tier 2 or 3 yields >1).
    Both exceptions carry .candidates = up to 10 closest {entity_id,label}.
    """
    nodes = graph.get("nodes", [])

    # Tier 1: exact entity_id.
    for n in nodes:
        if n["entity_id"] == anchor:
            return n

    token = anchor.casefold()

    # Tier 2: exact label, case-insensitive.
    tier2 = [n for n in nodes if (n.get("label") or "").casefold() == token]
    if len(tier2) == 1:
        return tier2[0]
    if len(tier2) > 1:
        cands = sorted(tier2, key=lambda n: n["entity_id"])[:10]
        raise AnchorAmbiguous(
            f"anchor '{anchor}' matches {len(tier2)} nodes by label",
            [_candidate(n) for n in cands],
        )

    # Tier 3: unique case-insensitive substring of label.
    tier3 = [n for n in nodes if token in (n.get("label") or "").casefold()]
    if len(tier3) == 1:
        return tier3[0]
    if len(tier3) > 1:
        cands = sorted(tier3, key=lambda n: n["entity_id"])[:10]
        raise AnchorAmbiguous(
            f"anchor '{anchor}' matches {len(tier3)} nodes by substring",
            [_candidate(n) for n in cands],
        )

    # Zero matches: surface a few near tokens for the caller to re-query.
    cands = sorted(nodes, key=lambda n: n["entity_id"])[:10]
    raise AnchorNotFound(
        f"anchor '{anchor}' not found",
        [_candidate(n) for n in cands],
    )


def extract_slice(
    run_id: str,
    anchor: str,
    *,
    depth: int = 2,
    direction: str = "both",
    edge_types: list[str] | None = None,
    levels: list[str] | None = None,
    extraction_methods: list[str] | None = None,
    min_weight: float = 0.0,
    max_nodes: int = 200,
    as_of: str | None = None,
) -> dict:
    """Deterministic BFS over the PIT structural graph from the resolved anchor.

    Returns a self-contained slice document. Raises:
      - FileNotFoundError if graph.json absent (propagated from _load_graph)
      - AnchorNotFound / AnchorAmbiguous from resolve_anchor
      - ValueError for invalid direction, depth<0, max_nodes<1, unknown tokens.
    """
    # ---- Validation (raise ValueError before traversal) ----
    if direction not in {"out", "in", "both"}:
        raise ValueError(f"invalid direction: {direction}")
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if edge_types is not None:
        for tok in edge_types:
            if tok not in _DEFAULT_EDGE_TYPES and tok not in _EVIDENCE_EDGE_TYPES:
                raise ValueError(f"unknown edge_type: {tok}")
    if levels is not None:
        for tok in levels:
            if tok not in _LEVEL_VOCAB:
                raise ValueError(f"unknown level: {tok}")

    graph = _load_graph(run_id, as_of)
    anchor_node = resolve_anchor(graph, anchor)

    # ---- Node index with attached level ----
    index: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        eid = n["entity_id"]
        et = n.get("entity_type")
        index[eid] = {
            "id": eid,
            "label": n.get("label") or eid,
            "entity_type": et,
            "level": registry.entity_level(et),
        }

    anchor_id = anchor_node["entity_id"]

    # ---- Effective admit sets ----
    eff_edge_types = (set(edge_types) if edge_types is not None else set(_DEFAULT_EDGE_TYPES))
    eff_edge_types -= _EVIDENCE_EDGE_TYPES  # evidence never traversable
    eff_methods = (set(extraction_methods) if extraction_methods is not None else set(_DEFAULT_METHODS))
    # Honesty/PIT guard: a structural slice never traverses weak (llm_inferred) edges,
    # even if a caller asks for them. Only ontology-admitted methods are allowed.
    eff_methods &= set(_DEFAULT_METHODS)

    level_allow = set(levels) if levels is not None else None

    # ---- Edge admission (computed once, before direction) ----
    admissible: list[dict] = []
    for e in graph.get("edges", []):
        if e.get("edge_type") not in eff_edge_types:
            continue
        if e.get("extraction_method") not in eff_methods:
            continue
        if float(e.get("weight") or 0.0) < min_weight:
            continue
        s = e.get("source_entity_id")
        t = e.get("target_entity_id")
        if s not in index or t not in index:
            continue
        admissible.append(e)

    def _level_ok(eid: str) -> bool:
        if eid == anchor_id:
            return True  # anchor always kept
        if level_allow is None:
            return True
        return index[eid]["level"] in level_allow

    # ---- Adjacency for expansion (direction-aware) ----
    # For node U being expanded, find (neighbor, weight) reachable per direction.
    def _neighbors(u: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for e in admissible:
            s = e["source_entity_id"]
            t = e["target_entity_id"]
            if s == t:
                continue  # self-loop ignored for expansion
            w = float(e.get("weight") or 0.0)
            if direction in ("out", "both") and s == u:
                out.append((t, w))
            if direction in ("in", "both") and t == u:
                out.append((s, w))
        return out

    # ---- BFS by hop with deterministic ranked node cap ----
    hop_of: dict[str, int] = {anchor_id: 0}
    kept: set[str] = {anchor_id}
    truncated = False
    frontier = [anchor_id]

    for h in range(1, depth + 1):
        if truncated:
            break
        # Gather candidates from the current frontier (nodes first seen at hop h-1).
        # candidate -> best discovering weight (max), for ranking.
        candidates: dict[str, float] = {}
        for u in sorted(frontier):
            for nbr, w in _neighbors(u):
                if nbr in hop_of:
                    continue  # already has a (<=) hop
                if not _level_ok(nbr):
                    continue  # filtered node: not enqueued, cannot relay
                if nbr not in candidates or w > candidates[nbr]:
                    candidates[nbr] = w
        if not candidates:
            frontier = []
            continue
        # Rank: (hop, -discovering_weight, entity_id). hop is constant (h) within this batch.
        ranked = sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0]))
        next_frontier: list[str] = []
        for nbr, _w in ranked:
            if len(kept) >= max_nodes:
                truncated = True
                break
            hop_of[nbr] = h
            kept.add(nbr)
            next_frontier.append(nbr)
        frontier = next_frontier

    # ---- Induced edges over kept set (not just BFS tree) ----
    out_edges: list[dict] = []
    for e in admissible:
        s = e["source_entity_id"]
        t = e["target_entity_id"]
        if s not in kept or t not in kept:
            continue
        # Induced subgraph: every admissible edge with both endpoints kept is emitted
        # in its stored source->target orientation. Reachability (not display) is what
        # direction restricts, so cross-links/back-edges among kept nodes render for all
        # three directions.
        out_edges.append({
            "source": s,
            "target": t,
            "edge_type": e.get("edge_type"),
            "weight": float(e.get("weight") or 0.0),
        })

    # ---- Build node list ----
    nodes_out = []
    for eid in kept:
        n = index[eid]
        nodes_out.append({
            "id": n["id"],
            "label": n["label"],
            "entity_type": n["entity_type"],
            "level": n["level"],
            "hop": hop_of[eid],
        })
    nodes_out.sort(key=lambda n: (n["hop"], n["id"]))
    out_edges.sort(key=lambda e: (e["source"], e["target"], e["edge_type"]))

    return {
        "run_id": run_id,
        "as_of_date": graph.get("as_of_date"),
        "anchor": {
            "id": anchor_node["entity_id"],
            "label": anchor_node.get("label") or anchor_node["entity_id"],
            "entity_type": anchor_node.get("entity_type"),
            "level": registry.entity_level(anchor_node.get("entity_type")),
        },
        "params": {
            "depth": depth,
            "direction": direction,
            "edge_types": sorted(eff_edge_types),
            "levels": sorted(level_allow) if level_allow is not None else None,
            "extraction_methods": sorted(eff_methods),
            "min_weight": min_weight,
            "max_nodes": max_nodes,
        },
        "nodes": nodes_out,
        "edges": out_edges,
        "node_count": len(nodes_out),
        "edge_count": len(out_edges),
        "truncated": truncated,
    }
