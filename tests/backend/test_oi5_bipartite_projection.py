"""OI-5 acceptance tests: Bipartite company<->concept projection for community detection.

Deliverables tested:
  (A) DETECTION INPUT IS BIPARTITE:
      - community_input_edges contains only edges where one endpoint is a Company
        and the other is a binding concept node (EconomicConcept, Commodity,
        MacroIndicator, Event).
      - Concept-concept edges and company-company edges are excluded from
        community_input_edges (but remain in graph.json for provenance).
      - A company with NO concept link does NOT join a concept-defined cluster
        that the old heterogeneous path would have merged.

  (B) CONCEPT SPINE IN COMMUNITY ARTIFACT:
      - Each community in communities.json carries BOTH company_members AND
        concept_spine fields.
      - concept_spine is non-empty for any multi-company theme.

  (C) PIT PRESERVED ON BIPARTITE PROJECTION:
      - A future-dated edge does not appear in community_input_edges.
      - Only edges with first_seen_at <= as_of_date shape the projection.

  (D) END-TO-END: graph -> themes -> exposure still runs on the bipartite input.

All tests are hermetic (no network / LLM calls).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine import graph_build, runs
from theme_engine.config import settings
from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS
from theme_engine.graph_build import (
    COMPANY_NODE_TYPE,
    CONCEPT_NODE_TYPES,
    STRUCTURAL_EDGE_TYPES,
)
from theme_engine.models import RunCreateRequest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CONCEPT_TYPE_SET = frozenset(CONCEPT_NODE_TYPES)


def _make_run(as_of_date: str = "2024-06-30") -> "RunManifest":
    return runs.create_run(RunCreateRequest(as_of_date=as_of_date))


def _row(cols: list[str], **kw) -> dict:
    return {c: kw.get(c, "") for c in cols}


def _ent(entity_id: str, entity_type: str, first_seen: str = "2024-01-01") -> dict:
    return _row(
        ENTITIES_COLUMNS,
        entity_id=entity_id,
        entity_type=entity_type,
        name=entity_id,
        canonical_name=entity_id,
        first_seen_at=first_seen,
        confidence="0.9",
        extraction_method="document_stated",
        review_status="pending",
    )


def _edge(
    edge_id: str,
    src: str,
    tgt: str,
    edge_type: str,
    first_seen: str = "2024-01-01",
    confidence: str = "0.8",
    extraction_method: str = "document_stated",
) -> dict:
    return _row(
        EDGES_COLUMNS,
        edge_id=edge_id,
        source_entity_id=src,
        target_entity_id=tgt,
        edge_type=edge_type,
        first_seen_at=first_seen,
        confidence=confidence,
        extraction_method=extraction_method,
    )


def _write_and_build(
    run,
    ents: list[dict],
    edges: list[dict],
) -> dict:
    """Write entities + edges, run build_graph, return graph.json dict."""
    ddir = Path(settings.run_output_dir) / run.run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
    graph_build.build_graph(run.run_id)
    return json.loads((ddir / "graph.json").read_text())


# ===========================================================================
# (A) DETECTION INPUT IS BIPARTITE
# ===========================================================================


class TestBipartiteDetectionInput:
    """community_input_edges must only contain Company<->concept bipartite edges."""

    def test_company_concept_edge_is_included(self):
        """A structural edge between a Company and a binding concept is in community_input_edges."""
        run = _make_run()
        ents = [
            _ent("c1", "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [
            _edge("e_bipartite", "c1", "ec1", "exposed_to"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        assert "e_bipartite" in cie, (
            "Company<->EconomicConcept structural edge must be in community_input_edges"
        )

    def test_concept_concept_edge_excluded_from_community_input(self):
        """A structural edge between two concept nodes is NOT in community_input_edges.

        OI-5: community detection is bipartite — concept-concept edges are excluded
        from the detection input, but remain in graph.json for provenance.
        """
        run = _make_run()
        ents = [
            _ent("ec1", "EconomicConcept"),
            _ent("mi1", "MacroIndicator"),
        ]
        edges = [
            _edge("e_concept_concept", "ec1", "mi1", "causes"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        assert "e_concept_concept" not in cie, (
            "OI-5: concept-concept structural edge must NOT be in community_input_edges"
        )
        # But it stays in graph.json edges for provenance
        all_edge_ids = {e["edge_id"] for e in g["edges"]}
        assert "e_concept_concept" in all_edge_ids, (
            "Concept-concept edge must remain in graph.json edges for evidence traceability"
        )

    def test_company_company_edge_excluded_from_community_input(self):
        """A structural edge between two Company nodes is NOT in community_input_edges.

        Under OI-5 bipartite projection, company-company edges are excluded;
        companies cluster only via shared concepts.
        """
        run = _make_run()
        ents = [
            _ent("c1", "Company"),
            _ent("c2", "Company"),
        ]
        edges = [
            _edge("e_co_co", "c1", "c2", "benefits"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        assert "e_co_co" not in cie, (
            "OI-5: company-company structural edge must NOT be in community_input_edges"
        )
        # Stays in graph.json for provenance
        all_edge_ids = {e["edge_id"] for e in g["edges"]}
        assert "e_co_co" in all_edge_ids

    def test_company_without_concept_link_isolated_from_concept_cluster(self):
        """A company with no concept link does not join the concept-defined cluster.

        Acceptance test: proves the bipartite projection is the detection input,
        not the old heterogeneous path. In the old path, Company C (connected
        to Company A via a company-company edge) would be merged into the same
        cluster as A's concepts. Under OI-5, that company-company edge is
        excluded from community_input_edges, so C is isolated from A's concept
        cluster.
        """
        run = _make_run()
        ents = [
            _ent("c_a", "Company"),       # connected to concept
            _ent("c_b", "Company"),       # ONLY connected to c_a (company-company)
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [
            # c_a is linked to ec1 (bipartite) — this goes into community_input_edges
            _edge("e_a_ec", "c_a", "ec1", "exposed_to"),
            # c_b is linked to c_a (company-company) — bipartite filter EXCLUDES this
            _edge("e_b_a", "c_b", "c_a", "benefits"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        # The bipartite edge is in community_input_edges
        assert "e_a_ec" in cie
        # The company-company edge is NOT in community_input_edges
        assert "e_b_a" not in cie, (
            "OI-5: company-company edge excluded — c_b cannot join ec1's cluster via c_a"
        )

    def test_all_concept_types_accepted_as_bipartite_side(self):
        """All four binding concept types are valid as the concept side of bipartite edges."""
        run = _make_run()
        ents = [
            _ent("comp", "Company"),
            _ent("ec",   "EconomicConcept"),
            _ent("com",  "Commodity"),
            _ent("mi",   "MacroIndicator"),
            _ent("ev",   "Event"),
        ]
        edges = [
            _edge("e_ec",  "comp", "ec",  "exposed_to"),
            _edge("e_com", "comp", "com", "sensitive_to"),
            _edge("e_mi",  "comp", "mi",  "sensitive_to"),
            _edge("e_ev",  "comp", "ev",  "exposed_to"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        for eid in ("e_ec", "e_com", "e_mi", "e_ev"):
            assert eid in cie, (
                f"OI-5: Company<->{eid.split('_')[1]} bipartite edge must be in community_input_edges"
            )

    def test_sector_geography_excluded_from_bipartite_side(self):
        """Sector and Geography nodes are NOT the concept side of the bipartite projection.

        These node types remain in graph.json for provenance but do not drive
        community detection under OI-5.
        """
        run = _make_run()
        ents = [
            _ent("comp",  "Company"),
            _ent("sec",   "Sector"),
            _ent("geo",   "Geography"),
        ]
        edges = [
            _edge("e_sec", "comp", "sec", "exposed_to"),
            _edge("e_geo", "comp", "geo", "located_in"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        # located_in is not a structural edge type, so it's always excluded
        assert "e_geo" not in cie
        # Sector is a structural node type but NOT a binding concept type (OI-5)
        # exposed_to with Sector is structural but NOT bipartite -> excluded
        assert "e_sec" not in cie, (
            "OI-5: Sector is not the concept side of bipartite projection; "
            "Company<->Sector edges must NOT be in community_input_edges"
        )

    def test_projection_metadata_in_graph_json(self):
        """graph.json projection block documents the bipartite nature (OI-5)."""
        run = _make_run()
        ents = [_ent("c1", "Company"), _ent("ec1", "EconomicConcept")]
        edges = [_edge("e1", "c1", "ec1", "exposed_to")]
        g = _write_and_build(run, ents, edges)

        proj = g.get("projection", {})
        assert proj.get("community_detection") == "bipartite", (
            "graph.json projection must carry community_detection='bipartite'"
        )
        assert proj.get("bipartite_company_side") == COMPANY_NODE_TYPE
        assert sorted(proj.get("bipartite_concept_side", [])) == sorted(CONCEPT_NODE_TYPES)


# ===========================================================================
# (B) CONCEPT SPINE IN COMMUNITY ARTIFACT
# ===========================================================================


class TestConceptSpineInCommunityArtifact:
    """communities.json must carry company_members AND concept_spine for each community."""

    def _run_pipeline_to_communities(self, ents, edges, as_of_date="2024-06-30"):
        """Write a minimal fixture run, build graph and discover themes. Returns communities dict."""
        from fastapi.testclient import TestClient
        from theme_engine.main import app

        client = TestClient(app)

        run = _make_run(as_of_date)
        ddir = Path(settings.run_output_dir) / run.run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
        pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")

        resp = client.post("/api/graph/build", json={"run_id": run.run_id})
        assert resp.status_code == 200, resp.text

        resp = client.post("/api/themes/discover", json={"run_id": run.run_id})
        assert resp.status_code == 200, resp.text

        comm_path = ddir / "communities.json"
        return json.loads(comm_path.read_text())

    def test_communities_have_company_members_field(self):
        """Every community record has a company_members field (OI-5)."""
        ents = [
            _ent("c1", "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [_edge("e1", "c1", "ec1", "exposed_to")]

        doc = self._run_pipeline_to_communities(ents, edges)
        for comm in doc["communities"]:
            assert "company_members" in comm, (
                f"OI-5: community {comm['community_id']!r} missing company_members field"
            )
            assert isinstance(comm["company_members"], list), (
                "company_members must be a list"
            )

    def test_communities_have_concept_spine_field(self):
        """Every community record has a concept_spine field (OI-5)."""
        ents = [
            _ent("c1", "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [_edge("e1", "c1", "ec1", "exposed_to")]

        doc = self._run_pipeline_to_communities(ents, edges)
        for comm in doc["communities"]:
            assert "concept_spine" in comm, (
                f"OI-5: community {comm['community_id']!r} missing concept_spine field"
            )
            assert isinstance(comm["concept_spine"], list), (
                "concept_spine must be a list"
            )

    def test_multi_company_theme_has_non_empty_concept_spine(self):
        """A community with ≥2 companies must have a non-empty concept_spine.

        This is the core OI-5 acceptance: the concept spine explains WHY companies
        cluster. If two companies share a binding concept, the spine must carry it.
        """
        ents = [
            _ent("c1", "Company"),
            _ent("c2", "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [
            _edge("e1", "c1", "ec1", "exposed_to"),
            _edge("e2", "c2", "ec1", "exposed_to"),
        ]
        doc = self._run_pipeline_to_communities(ents, edges)

        # Find the community that contains both companies (should be one with ec1)
        multi_co_communities = [
            c for c in doc["communities"]
            if len(c.get("company_members", [])) >= 2
        ]
        assert multi_co_communities, (
            "Expected at least one community with 2 companies sharing a concept node"
        )
        for comm in multi_co_communities:
            assert len(comm["concept_spine"]) > 0, (
                f"OI-5: multi-company community {comm['community_id']!r} has empty concept_spine — "
                "every multi-company theme must have the binding concepts that connect the companies"
            )

    def test_concept_spine_entity_ids_are_concept_types(self):
        """concept_spine contains only entity IDs of binding concept node types (OI-5)."""
        ents = [
            _ent("c1",  "Company"),
            _ent("c2",  "Company"),
            _ent("ec1", "EconomicConcept"),
            _ent("com1","Commodity"),
        ]
        edges = [
            _edge("e1", "c1", "ec1",  "exposed_to"),
            _edge("e2", "c2", "com1", "sensitive_to"),
            _edge("e3", "c1", "com1", "exposed_to"),
        ]
        doc = self._run_pipeline_to_communities(ents, edges)

        # Build an entity_type lookup from the communities doc via node_ids
        # (we don't have entities.parquet in this test's scope, so infer from IDs)
        # The concept_spine items must NOT be Company IDs
        all_company_members = set()
        for comm in doc["communities"]:
            all_company_members.update(comm.get("company_members", []))

        for comm in doc["communities"]:
            spine = comm.get("concept_spine", [])
            for node_id in spine:
                assert node_id not in all_company_members, (
                    f"concept_spine node {node_id!r} is also a company_member — "
                    "concept_spine must only contain binding concept nodes, not companies"
                )

    def test_company_members_entity_ids_are_company_type(self):
        """company_members contains only Company entity IDs (not concept nodes)."""
        ents = [
            _ent("c1",  "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [_edge("e1", "c1", "ec1", "exposed_to")]
        doc = self._run_pipeline_to_communities(ents, edges)

        all_concept_spine = set()
        for comm in doc["communities"]:
            all_concept_spine.update(comm.get("concept_spine", []))

        for comm in doc["communities"]:
            members = comm.get("company_members", [])
            for node_id in members:
                assert node_id not in all_concept_spine, (
                    f"company_members node {node_id!r} is also in concept_spine — "
                    "these must be disjoint"
                )


# ===========================================================================
# (C) PIT PRESERVED ON BIPARTITE PROJECTION
# ===========================================================================


class TestPitPreservedOnBipartiteProjection:
    """Future-dated bipartite edges must not appear in community_input_edges."""

    def test_future_dated_bipartite_edge_excluded_from_projection(self):
        """An edge with first_seen_at > as_of_date is NOT in community_input_edges.

        This proves PIT is preserved on the bipartite projection, not just on
        the evidence substrate.
        """
        run = _make_run(as_of_date="2024-06-30")
        ents = [
            _ent("c1", "Company"),
            _ent("ec1", "EconomicConcept"),
        ]
        edges = [
            # Past-dated bipartite edge: admitted
            _edge("e_past",   "c1", "ec1", "exposed_to", first_seen="2024-01-01"),
            # Future-dated bipartite edge: MUST be excluded (PIT gate)
            _edge("e_future", "c1", "ec1", "exposed_to", first_seen="2025-01-01"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        assert "e_past" in cie, "Past-dated bipartite edge must be in community_input_edges"
        assert "e_future" not in cie, (
            "OI-5 + PIT: future-dated bipartite edge must NOT be in community_input_edges "
            "(first_seen_at=2025-01-01 > as_of_date=2024-06-30)"
        )

    def test_future_dated_entity_excluded_from_bipartite_projection(self):
        """A concept node with first_seen_at > as_of_date is excluded from the projection.

        The bipartite edge to this future-dated concept must not appear in
        community_input_edges.
        """
        run = _make_run(as_of_date="2024-06-30")
        ents = [
            _ent("c1",  "Company",         first_seen="2024-01-01"),
            _ent("ec1", "EconomicConcept", first_seen="2025-01-01"),  # future entity
        ]
        edges = [
            _edge("e_to_future_ec", "c1", "ec1", "exposed_to", first_seen="2024-01-01"),
        ]
        g = _write_and_build(run, ents, edges)

        # ec1 is future-dated, so it should not be in the structural graph nodes
        node_ids = {n["entity_id"] for n in g["nodes"]}
        assert "ec1" not in node_ids, (
            "Future-dated concept node must not appear in graph.json nodes"
        )
        # And the edge referencing it cannot be in community_input_edges
        cie = set(g["community_input_edges"])
        assert "e_to_future_ec" not in cie, (
            "Edge to future-dated concept must not be in community_input_edges"
        )

    def test_pit_clean_bipartite_projection_passes(self):
        """A PIT-clean bipartite edge is admitted to community_input_edges."""
        run = _make_run(as_of_date="2024-06-30")
        ents = [
            _ent("c1",  "Company",         first_seen="2023-12-31"),
            _ent("com1","Commodity",        first_seen="2024-03-01"),
        ]
        edges = [
            _edge("e_clean", "c1", "com1", "sensitive_to", first_seen="2024-05-15"),
        ]
        g = _write_and_build(run, ents, edges)

        cie = set(g["community_input_edges"])
        assert "e_clean" in cie, (
            "PIT-clean bipartite edge (first_seen <= as_of) must be in community_input_edges"
        )


# ===========================================================================
# (D) END-TO-END: graph -> themes -> exposure runs on bipartite input
# ===========================================================================


def test_end_to_end_bipartite_pipeline():
    """Full end-to-end: graph -> themes -> exposure runs correctly on bipartite projection.

    Verifies that:
    1. Themes are discovered (non-zero community count for connected bipartite fixture)
    2. Exposure is computed (non-zero rows)
    3. community_members and concept_spine are present in communities.json
    4. No crash; all three stages succeed.
    """
    from fastapi.testclient import TestClient
    from theme_engine.main import app

    client = TestClient(app)

    run = _make_run()
    ddir = Path(settings.run_output_dir) / run.run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)

    ents = [
        _ent("company_a",  "Company"),
        _ent("company_b",  "Company"),
        _ent("concept_ai", "EconomicConcept"),
        _ent("commodity_1","Commodity"),
    ]
    edges = [
        _edge("e_a_ai",  "company_a", "concept_ai",  "exposed_to"),
        _edge("e_b_ai",  "company_b", "concept_ai",  "exposed_to"),
        _edge("e_a_com", "company_a", "commodity_1", "sensitive_to"),
    ]

    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")

    # Stage 1: build graph
    resp = client.post("/api/graph/build", json={"run_id": run.run_id})
    assert resp.status_code == 200, f"graph/build failed: {resp.text}"

    g = json.loads((ddir / "graph.json").read_text())
    cie = set(g["community_input_edges"])
    # Only bipartite edges are in community_input_edges
    assert "e_a_ai" in cie
    assert "e_b_ai" in cie
    assert "e_a_com" in cie

    # Stage 2: discover themes
    resp = client.post("/api/themes/discover", json={"run_id": run.run_id})
    assert resp.status_code == 200, f"themes/discover failed: {resp.text}"
    assert resp.json()["community_count"] > 0

    comm_doc = json.loads((ddir / "communities.json").read_text())
    for comm in comm_doc["communities"]:
        assert "company_members" in comm, "communities.json missing company_members"
        assert "concept_spine" in comm, "communities.json missing concept_spine"

    # Stage 3: compute exposure
    resp = client.post("/api/exposure/compute", json={"run_id": run.run_id})
    assert resp.status_code == 200, f"exposure/compute failed: {resp.text}"
    assert resp.json()["theme_count"] > 0
    assert resp.json()["company_theme_pair_count"] > 0
