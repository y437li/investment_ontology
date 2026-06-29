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

Structural edge types (derived from configs/ontology.yml, structural: true):
  causes, benefits, hurts, exposed_to, sensitive_to

Evidence edge types (excluded from community discovery):
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

# ---------------------------------------------------------------------------
# FI-A: Signed/weighted edge model (GitHub #104)
#
# POLARITY — derived from configs/ontology.yml `base_polarity` per edge type:
#   +1  positive / same-direction signal (benefits, causes, exposed_to, …)
#   -1  negative / opposite-direction signal (hurts)
#    0  undirected, evidence-only, or excluded from signed propagation
#
# For `causes`, `exposed_to`, `sensitive_to` the config base_polarity is +1
# (source→target convention is positive).  When edges.parquet gains a future
# `direction` field (e.g. "positive" / "negative"), graph_build will multiply
# base_polarity by that direction multiplier.  Until then, the config value is
# the sole polarity source.  polarity is NOT hardcoded here — it is read from
# registry.edge_base_polarity() which loads configs/ontology.yml.
#
# PROPAGATION WEIGHT — derived from edge confidence in (0, 1]:
#   propagation_weight = max(confidence, 0.01)
#
# confidence ∈ [0.0, 1.0]; clamped to 0.01 minimum so weight ∈ (0, 1].
# Evidence count and recency are optional future enhancements; confidence alone
# is used here.
#
# Both fields land on every edge dict in the `edges` list of graph.json so
# FI-B (the propagation engine) can read them directly.  They do NOT affect
# community_input_edges (community detection uses only structural edge ids).
# ---------------------------------------------------------------------------

_MIN_PROPAGATION_WEIGHT: float = 0.01  # clamp floor so weight is always in (0, 1]

# FI-#110: edge types where effective polarity = the extracted per-instance direction field.
# For these types, base_polarity from ontology is NO LONGER the effective polarity.
# Locked design decision: unknown direction -> 0 (excluded from signed propagation), NOT +1.
_DIRECTION_TYPED_EDGES: frozenset = frozenset({"causes", "exposed_to", "sensitive_to"})


def _propagation_weight(confidence: float) -> float:
    """Return propagation weight clamped to (0, 1]."""
    return max(float(confidence), _MIN_PROPAGATION_WEIGHT)


def _effective_polarity(edge_type: str, raw_direction) -> int:
    """Return effective graph polarity for an edge.

    FI-A + #110 rule:
    - causes / exposed_to / sensitive_to: use the extracted per-instance direction
      field from edges.parquet.  0 if absent/unknown/invalid — these edges are
      EXCLUDED from signed propagation when direction is unknown (locked design
      decision: unknown -> 0, NOT +1).
    - benefits / hurts and other types: use base_polarity from ontology.yml
      (unchanged behaviour).

    Args:
        edge_type: The edge's type string.
        raw_direction: The raw value from edge.get("direction") — may be None,
            "", int, or string-encoded int.

    Returns:
        int in {-1, 0, +1}
    """
    if edge_type in _DIRECTION_TYPED_EDGES:
        # Backward compatible: old edges without the column have raw_direction = None / ""
        if raw_direction is None or raw_direction == "":
            return 0
        try:
            val = int(raw_direction)
            return val if val in (-1, 0, 1) else 0
        except (TypeError, ValueError):
            return 0
    # benefits / hurts / co_occurs_with / mentioned_in / located_in / …
    return registry.edge_base_polarity(edge_type)

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

# ---------------------------------------------------------------------------
# OI-5: Bipartite company<->concept projection for community detection
#
# Community detection runs on a BIPARTITE graph where:
#   - One side = Company entities
#   - Other side = "binding" concept nodes (EconomicConcept, Commodity,
#     MacroIndicator, Event)
#
# Only edges that cross the bipartite boundary (Company<->concept) are
# included in community_input_edges. Edges between two Companies, or
# between two non-Company concept nodes, are EXCLUDED from community
# detection — they remain in graph.json for evidence/provenance.
#
# This guarantees that companies cluster together ONLY when they share
# a common binding concept, not merely because they co-appear in the
# same sector or geography.
#
# Sector and Geography are excluded from the concept/binding side;
# they remain in graph.json for provenance but do NOT drive themes.
# ---------------------------------------------------------------------------

COMPANY_NODE_TYPE: str = "Company"

# "Binding" concept node types — the right side of the bipartite projection.
# Sector and Geography are intentionally excluded: they do not define a theme's
# concept spine; their edges remain in graph.json for evidence only.
CONCEPT_NODE_TYPES: list[str] = [
    "EconomicConcept",
    "Commodity",
    "MacroIndicator",
    "Event",
]


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


def _read_entities(run_id: str, as_of: str | None = None) -> list[dict]:
    artifact = runs.discovery_point_dir(run_id, as_of) / "entities.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"entities.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def _read_edges(run_id: str, as_of: str | None = None) -> list[dict]:
    artifact = runs.discovery_point_dir(run_id, as_of) / "edges.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"edges.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def build_graph(run_id: str, as_of: str | None = None) -> tuple[int, int]:
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
    as_of_date = as_of if as_of is not None else manifest.as_of_date

    raw_entities = _read_entities(run_id, as_of)
    raw_edges = _read_edges(run_id, as_of)

    # --- Point-in-time filter + OI-5: include only non-Document entities ---
    structural_entity_ids: set[str] = set()
    # OI-5 bipartite: track entity types for the bipartite projection filter
    entity_type_by_id: dict[str, str] = {}
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
        entity_type_by_id[entity_id] = entity_type
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
    # community_input_edges contains only bipartite (Company<->concept) structural edges.
    all_edges: list[dict] = []
    community_input_edge_ids: list[str] = []

    # OI-5: frozenset of concept node types for O(1) bipartite check inside the loop
    _concept_type_set: frozenset = frozenset(CONCEPT_NODE_TYPES)

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
        #
        # FI-A + #110: effective polarity via _effective_polarity():
        #   causes/exposed_to/sensitive_to -> extracted direction (0 if unknown)
        #   benefits/hurts -> base_polarity from ontology.yml (unchanged)
        # Neither field touches community_input_edges.
        raw_direction = edge.get("direction")
        polarity: int = _effective_polarity(edge_type, raw_direction)
        prop_weight: float = _propagation_weight(confidence)

        all_edges.append(
            {
                "edge_id": edge_id,
                "source_entity_id": source_id,
                "target_entity_id": target_id,
                "edge_type": edge_type,
                "weight": confidence,
                "evidence_chunk_ids": list(evidence_chunk_ids) if evidence_chunk_ids else [],
                "extraction_method": extraction_method,
                # FI-A fields — substrate for forward-inference propagation engine
                "polarity": polarity,
                "propagation_weight": prop_weight,
            }
        )

        # OI-5: community_input_edges must have:
        # 1. Both endpoints are structural (non-Document) entities
        # 2. Edge type is structural
        # 3. Extraction method is admitted (document_stated or approved metadata_inferred)
        # 4. BIPARTITE: one endpoint is Company, other is a binding concept node
        #    (EconomicConcept, Commodity, MacroIndicator, Event).
        #    Edges between two Companies, or two concepts, are excluded from
        #    community detection (they stay in graph.json for provenance).
        src_type = entity_type_by_id.get(source_id, "")
        tgt_type = entity_type_by_id.get(target_id, "")
        is_bipartite_edge = (
            (src_type == COMPANY_NODE_TYPE and tgt_type in _concept_type_set)
            or (tgt_type == COMPANY_NODE_TYPE and src_type in _concept_type_set)
        )
        if (
            edge_type in STRUCTURAL_EDGE_TYPES
            and source_id in structural_entity_ids
            and target_id in structural_entity_ids
            and extraction_method in COMMUNITY_INPUT_METHODS
            and is_bipartite_edge
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
            # OI-5: bipartite projection metadata
            "community_detection": "bipartite",
            "bipartite_company_side": COMPANY_NODE_TYPE,
            "bipartite_concept_side": CONCEPT_NODE_TYPES,
            "bipartite_note": (
                "community_input_edges contains only edges crossing the Company<->concept "
                "boundary. Edges between two Companies or two concepts are in graph.json "
                "for provenance but excluded from community detection."
            ),
        },
        "structural_edge_types": STRUCTURAL_EDGE_TYPES,
        "evidence_edge_types": EVIDENCE_EDGE_TYPES,
        "nodes": node_list,
        "edges": all_edges,
        "community_input_edges": community_input_edge_ids,
    }

    # Write graph.json
    discovery_dir = runs.discovery_point_dir(run_id, as_of, for_write=True)
    graph_path = discovery_dir / "graph.json"
    graph_path.write_text(json.dumps(graph_doc, indent=2), encoding="utf-8")

    return len(node_list), len(all_edges)
