"""Subgraph extraction for a set of communities (a main theme = union of its
sub-themes). Returns the entity-only structural subgraph (nodes with factor level,
structural edges among them) so the UI can render a whole main theme as one graph.
Deterministic — read from graph.json + communities.json.
"""

from __future__ import annotations

from . import registry, run_cache, runs

_EVIDENCE_EDGE_TYPES = {"mentioned_in", "co_occurs_with"}


def community_subgraph(run_id: str, community_ids: list[str]) -> dict:
    """Union subgraph for the given communities: nodes (with level) + structural edges."""
    rd = runs.get_run_dir(run_id)
    comm_doc = run_cache.load_json(rd / "discovery" / "communities.json")
    communities = comm_doc.get("communities", comm_doc)
    wanted = set(community_ids)

    node_ids: set[str] = set()
    for c in communities:
        if c["community_id"] in wanted:
            node_ids.update(c.get("node_ids", []))

    graph = run_cache.load_json(rd / "discovery" / "graph.json")
    nodes = [{
        "id": n["entity_id"],
        "label": n.get("label") or n["entity_id"],
        "entity_type": n.get("entity_type"),
        "level": registry.entity_level(n.get("entity_type")),
    } for n in graph.get("nodes", []) if n["entity_id"] in node_ids]
    keep = {n["id"] for n in nodes}

    edges = [{
        "source": e["source_entity_id"],
        "target": e["target_entity_id"],
        "edge_type": e.get("edge_type"),
    } for e in graph.get("edges", [])
        if e["source_entity_id"] in keep and e["target_entity_id"] in keep
        and e.get("edge_type") not in _EVIDENCE_EDGE_TYPES]

    return {"community_ids": sorted(wanted), "nodes": nodes, "edges": edges,
            "node_count": len(nodes), "edge_count": len(edges)}
