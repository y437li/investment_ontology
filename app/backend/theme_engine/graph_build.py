"""Graph(t) construction service (M4): build an entity-only structural graph.

Reads:
  - ``discovery/entities.parquet``  (io_contracts.md section 9)
  - ``discovery/edges.parquet``     (io_contracts.md section 11)

Writes:
  - ``discovery/graph.json``        (io_contracts.md section 13)

OI-5 Entity-only projection:
  - Community detection must run on entity-only structural edges.
  - Document nodes are excluded from the structural graph.
  - ``mentioned_in`` and ``co_occurs_with`` edges are evidence-only.
  - ``community_input_edges`` contains only edges with structural edge_types
    and non-Document endpoints.

Structural edge types (spec §7, io_contracts §13):
  causes, benefits, hurts, exposed_to, sensitive_to, co_occurs_with

Evidence edge types:
  mentioned_in, co_occurs_with

Point-in-time:
  Only entities/edges with first_seen_at <= as_of_date are included.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

SCHEMA_VERSION = "1.0"

# OI-5: structural edges used for community discovery. Derived from the ontology
# (configs/ontology.yml structural:true) so the config is the single source of
# truth; located_in is structural:false there, so geography no longer drives themes.
from . import registry  # noqa: E402

_FALLBACK_STRUCTURAL = ["exposed_to", "sensitive_to", "causes", "benefits", "hurts"]
STRUCTURAL_EDGE_TYPES: list[str] = registry.structural_edge_types() or _FALLBACK_STRUCTURAL

# Evidence edges (not used for structural clustering)
EVIDENCE_EDGE_TYPES: list[str] = ["mentioned_in", "co_occurs_with"]

# Extraction methods admitted into community discovery (spec §11): document_stated
# plus config-approved metadata_inferred (structured adapters, e.g. the macro
# adapter — trusted factor edges, not weak LLM inference). llm_inferred stays out.
COMMUNITY_INPUT_METHODS: frozenset = frozenset({"document_stated", "metadata_inferred"})

# Node types included in the entity-only structural graph (excludes Document)
STRUCTURAL_NODE_TYPES: list[str] = [
    "Company",
    "Sector",          # industry-level node type (ontology); was missing from the whitelist
    "MacroIndicator",
    "EconomicConcept",
    "Commodity",
    "Event",
    "Geography",
]

# Node types excluded from the structural graph
EXCLUDED_NODE_TYPES: list[str] = ["Document"]


def _to_date_str(val: Any) -> str:
    """Coerce a value to YYYY-MM-DD string for comparison."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


def _read_entities(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "entities.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"entities.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def _read_edges(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "edges.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"edges.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def build_graph(run_id: str) -> tuple[int, int]:
    """Build ``graph.json`` from entities and edges.

    Returns (node_count, edge_count) where edge_count counts all edges
    (both structural and evidence) written to graph.json.

    OI-5 contract:
    - Structural graph nodes: all non-Document entities with first_seen_at <= as_of_date.
    - Structural edges: edge_type in STRUCTURAL_EDGE_TYPES, both endpoints are
      non-Document entities.
    - Evidence edges: edge_type in EVIDENCE_EDGE_TYPES (stored in graph.json
      for traceability but excluded from community_input_edges).
    - community_input_edges: only structural edge ids (non-Document endpoints,
      structural edge type).
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date = manifest.as_of_date

    raw_entities = _read_entities(run_id)
    raw_edges = _read_edges(run_id)

    # --- Point-in-time filter + OI-5: include only non-Document entities ---
    structural_entity_ids: set[str] = set()
    node_list: list[dict] = []

    for ent in raw_entities:
        # Point-in-time gate
        first_seen = _to_date_str(ent.get("first_seen_at", ""))
        # PIT fail-CLOSED: exclude undated items too (cannot prove availability at as_of)
        if (not first_seen) or first_seen > as_of_date:
            continue
        entity_type = ent.get("entity_type", "")
        # OI-5: exclude Document nodes from structural graph
        if entity_type in EXCLUDED_NODE_TYPES:
            continue
        entity_id: str = ent.get("entity_id", "")
        if not entity_id:
            continue
        structural_entity_ids.add(entity_id)
        node_list.append(
            {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "label": ent.get("canonical_name") or ent.get("name") or entity_id,
                "attributes": {},
            }
        )

    # --- Build edge lists ---
    # All edges go into graph.json for traceability.
    # community_input_edges contains only structural edges.
    all_edges: list[dict] = []
    community_input_edge_ids: list[str] = []

    for edge in raw_edges:
        # Point-in-time gate
        first_seen = _to_date_str(edge.get("first_seen_at", ""))
        # PIT fail-CLOSED: exclude undated items too (cannot prove availability at as_of)
        if (not first_seen) or first_seen > as_of_date:
            continue
        edge_id: str = edge.get("edge_id", "")
        if not edge_id:
            continue
        source_id: str = edge.get("source_entity_id", "")
        target_id: str = edge.get("target_entity_id", "")
        edge_type: str = edge.get("edge_type", "")
        confidence = float(edge.get("confidence") or 0.0)
        evidence_chunk_ids = edge.get("evidence_chunk_ids") or []
        extraction_method: str = edge.get("extraction_method", "")

        # Both endpoints must exist (we need them for evidence edges too)
        # For evidence edges (mentioned_in, co_occurs_with) where one endpoint
        # might be a Document, we still store in graph.json but NOT in community_input.
        all_edges.append(
            {
                "edge_id": edge_id,
                "source_entity_id": source_id,
                "target_entity_id": target_id,
                "edge_type": edge_type,
                "weight": confidence,
                "evidence_chunk_ids": list(evidence_chunk_ids) if evidence_chunk_ids else [],
                "extraction_method": extraction_method,
            }
        )

        # OI-5: community_input_edges must have:
        # 1. Both endpoints are structural (non-Document) entities
        # 2. Edge type is structural
        # 3. Extraction method is admitted (document_stated or approved metadata_inferred)
        if (
            edge_type in STRUCTURAL_EDGE_TYPES
            and source_id in structural_entity_ids
            and target_id in structural_entity_ids
            and extraction_method in COMMUNITY_INPUT_METHODS
        ):
            community_input_edge_ids.append(edge_id)

    # Construct graph.json
    graph_doc: dict = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_date": as_of_date,
        "projection": {
            "type": "entity_only",
            "node_types_in_structural_graph": STRUCTURAL_NODE_TYPES,
            "excluded_node_types": EXCLUDED_NODE_TYPES,
        },
        "structural_edge_types": STRUCTURAL_EDGE_TYPES,
        "evidence_edge_types": EVIDENCE_EDGE_TYPES,
        "nodes": node_list,
        "edges": all_edges,
        "community_input_edges": community_input_edge_ids,
    }

    # Write graph.json
    discovery_dir = runs.get_run_dir(run_id) / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    graph_path = discovery_dir / "graph.json"
    graph_path.write_text(json.dumps(graph_doc, indent=2), encoding="utf-8")

    return len(node_list), len(all_edges)
