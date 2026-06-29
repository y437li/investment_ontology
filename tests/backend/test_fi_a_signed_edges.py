"""FI-A: Signed/weighted edge model — hermetic unit tests (GitHub #104).

Acceptance criteria (per spec):
  (1) Every structural edge in graph.json carries polarity in {+1, -1, 0}
      and propagation_weight in (0, 1].
  (2) Fixture: hurts -> -1, benefits -> +1, undirected/unknown -> 0.
  (3) community_input_edges set is IDENTICAL to before FI-A (unchanged).
  (4) Hermetic — no network, no LLM calls.
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
from theme_engine.models import RunCreateRequest
from theme_engine.registry import edge_base_polarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run() -> str:
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    return run.run_id


def _write_fixture(run_id: str, ents: list[dict], edges: list[dict]) -> Path:
    ddir = Path(settings.run_output_dir) / run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
    return ddir


def _ent(eid: str, etype: str = "Company") -> dict:
    return {c: "" for c in ENTITIES_COLUMNS} | {
        "entity_id": eid,
        "entity_type": etype,
        "name": eid,
        "canonical_name": eid,
        "first_seen_at": "2024-01-01",
    }


def _edge(eid: str, src: str, tgt: str, etype: str, confidence: float = 0.8) -> dict:
    return {c: "" for c in EDGES_COLUMNS} | {
        "edge_id": eid,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "edge_type": etype,
        "confidence": str(confidence),
        "extraction_method": "document_stated",
        "first_seen_at": "2024-01-01",
        "evidence_chunk_ids": ["chunk_1"],
    }


# ---------------------------------------------------------------------------
# (2a) registry.edge_base_polarity unit tests — no graph needed
# ---------------------------------------------------------------------------

class TestEdgeBasePolarity:
    """registry.edge_base_polarity reads from ontology.yml — no hardcoding."""

    def test_benefits_is_plus_one(self):
        assert edge_base_polarity("benefits") == 1

    def test_hurts_is_minus_one(self):
        assert edge_base_polarity("hurts") == -1

    def test_causes_is_plus_one(self):
        assert edge_base_polarity("causes") == 1

    def test_exposed_to_is_plus_one(self):
        assert edge_base_polarity("exposed_to") == 1

    def test_sensitive_to_is_plus_one(self):
        assert edge_base_polarity("sensitive_to") == 1

    def test_located_in_is_zero(self):
        assert edge_base_polarity("located_in") == 0

    def test_co_occurs_with_is_zero(self):
        assert edge_base_polarity("co_occurs_with") == 0

    def test_mentioned_in_is_zero(self):
        assert edge_base_polarity("mentioned_in") == 0

    def test_unknown_edge_type_is_zero(self):
        assert edge_base_polarity("unknown_future_type") == 0

    def test_reports_is_plus_one(self):
        assert edge_base_polarity("reports") == 1

    def test_guides_to_is_plus_one(self):
        assert edge_base_polarity("guides_to") == 1


# ---------------------------------------------------------------------------
# (1) + (2b) graph.json edges carry polarity + weight; fixture assertions
# ---------------------------------------------------------------------------

class TestGraphEdgeSignedFields:
    """Build a minimal graph and assert polarity + propagation_weight on edges."""

    @pytest.fixture(scope="class")
    def graph_doc(self):
        """Build a graph with one hurts, one benefits, one co_occurs_with edge."""
        run_id = _make_run()
        ents = [
            _ent("e_a", "Company"),
            _ent("e_b", "EconomicConcept"),
            _ent("e_c", "MacroIndicator"),
        ]
        edges = [
            _edge("ed_hurts",    "e_a", "e_b", "hurts",         confidence=0.9),
            _edge("ed_benefits", "e_b", "e_c", "benefits",      confidence=0.7),
            _edge("ed_co",       "e_a", "e_c", "co_occurs_with", confidence=0.5),
        ]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        return json.loads((ddir / "graph.json").read_text())

    def test_all_edges_have_polarity(self, graph_doc):
        for e in graph_doc["edges"]:
            assert "polarity" in e, f"missing polarity on edge {e['edge_id']!r}"

    def test_all_edges_have_propagation_weight(self, graph_doc):
        for e in graph_doc["edges"]:
            assert "propagation_weight" in e, (
                f"missing propagation_weight on edge {e['edge_id']!r}"
            )

    def test_polarity_values_are_in_valid_set(self, graph_doc):
        valid = {-1, 0, 1}
        for e in graph_doc["edges"]:
            assert e["polarity"] in valid, (
                f"polarity {e['polarity']!r} not in {valid} for edge {e['edge_id']!r}"
            )

    def test_propagation_weight_in_range(self, graph_doc):
        for e in graph_doc["edges"]:
            w = e["propagation_weight"]
            assert 0 < w <= 1.0, (
                f"propagation_weight {w!r} not in (0, 1] for edge {e['edge_id']!r}"
            )

    def test_hurts_polarity_is_minus_one(self, graph_doc):
        edge_by_id = {e["edge_id"]: e for e in graph_doc["edges"]}
        assert "ed_hurts" in edge_by_id, "ed_hurts not found in graph"
        assert edge_by_id["ed_hurts"]["polarity"] == -1

    def test_benefits_polarity_is_plus_one(self, graph_doc):
        edge_by_id = {e["edge_id"]: e for e in graph_doc["edges"]}
        assert "ed_benefits" in edge_by_id, "ed_benefits not found in graph"
        assert edge_by_id["ed_benefits"]["polarity"] == 1

    def test_co_occurs_with_polarity_is_zero(self, graph_doc):
        """Undirected/non-structural edge -> polarity 0 (excluded from signed prop)."""
        edge_by_id = {e["edge_id"]: e for e in graph_doc["edges"]}
        assert "ed_co" in edge_by_id, "ed_co not found in graph"
        assert edge_by_id["ed_co"]["polarity"] == 0

    def test_propagation_weight_reflects_confidence(self, graph_doc):
        edge_by_id = {e["edge_id"]: e for e in graph_doc["edges"]}
        # hurts confidence=0.9 -> weight=0.9
        assert abs(edge_by_id["ed_hurts"]["propagation_weight"] - 0.9) < 1e-9
        # benefits confidence=0.7 -> weight=0.7
        assert abs(edge_by_id["ed_benefits"]["propagation_weight"] - 0.7) < 1e-9


# ---------------------------------------------------------------------------
# (2c) Zero-confidence clamping: weight must stay in (0, 1]
# ---------------------------------------------------------------------------

class TestPropagationWeightClamping:
    def test_zero_confidence_clamped_to_min(self):
        """confidence=0 must produce propagation_weight > 0."""
        run_id = _make_run()
        ents = [_ent("e_x", "Company"), _ent("e_y", "EconomicConcept")]
        edges = [_edge("ed_zero_conf", "e_x", "e_y", "causes", confidence=0.0)]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        edge_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert "ed_zero_conf" in edge_by_id
        w = edge_by_id["ed_zero_conf"]["propagation_weight"]
        assert w > 0, f"propagation_weight must be > 0, got {w}"
        assert w <= 1.0


# ---------------------------------------------------------------------------
# (3) community_input_edges UNCHANGED by FI-A
# ---------------------------------------------------------------------------

class TestCommunityInputEdgesUnchanged:
    """community_input_edges must not be affected by polarity/weight annotations."""

    def test_community_input_edges_identical_to_pre_fia(self):
        """FI-A fields do NOT change which edges feed community detection.

        Build a graph with one structural (document_stated, structural type,
        non-Document endpoints) and one evidence edge.  Assert:
          - structural edge IS in community_input_edges
          - evidence edge is NOT in community_input_edges
          - FI-A fields (polarity, propagation_weight) are present on both
            without changing the community_input_edges membership.
        """
        run_id = _make_run()
        ents = [
            _ent("e_co",   "Company"),
            _ent("e_ec",   "EconomicConcept"),
        ]
        edges = [
            _edge("ed_struct",   "e_co", "e_ec", "exposed_to",   confidence=0.8),
            _edge("ed_evidence", "e_co", "e_ec", "co_occurs_with", confidence=0.6),
        ]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())

        cie = set(g["community_input_edges"])

        # structural edge IS in community inputs
        assert "ed_struct" in cie, (
            "structural document_stated edge missing from community_input_edges"
        )
        # evidence edge is NOT
        assert "ed_evidence" not in cie, (
            "co_occurs_with edge must NOT be in community_input_edges"
        )
        # Both have polarity + propagation_weight
        edge_by_id = {e["edge_id"]: e for e in g["edges"]}
        for eid in ("ed_struct", "ed_evidence"):
            assert "polarity" in edge_by_id[eid], f"missing polarity on {eid}"
            assert "propagation_weight" in edge_by_id[eid], (
                f"missing propagation_weight on {eid}"
            )

    def test_community_input_edges_unchanged_with_multiple_edge_types(self):
        """Community input: structural, document_stated, non-Document, AND bipartite (OI-5).

        OI-5 (bipartite projection): community_input_edges contains only structural edges
        that cross the Company<->concept boundary.

        - e1: Company->EconomicConcept (benefits)   -> bipartite, IN community_input_edges
        - e2: EconomicConcept->MacroIndicator (hurts) -> concept-concept, NOT bipartite,
          EXCLUDED from community_input_edges (remains in graph.json for provenance)
        - e3: Company->MacroIndicator (co_occurs_with) -> non-structural, NOT included

        Legitimate fixture update: e2 is now excluded because OI-5 requires the detection
        input to be bipartite (Company<->concept only), not heterogeneous. e2 stays in
        graph.json edges for evidence traceability.
        """
        run_id = _make_run()
        ents = [
            _ent("ea", "Company"),
            _ent("eb", "EconomicConcept"),
            _ent("ec", "MacroIndicator"),
        ]
        edges = [
            _edge("e1", "ea", "eb", "benefits",       confidence=0.9),  # bipartite structural
            _edge("e2", "eb", "ec", "hurts",          confidence=0.8),  # structural but concept-concept
            _edge("e3", "ea", "ec", "co_occurs_with", confidence=0.5),  # non-structural
        ]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())

        cie = set(g["community_input_edges"])
        # e1: Company<->EconomicConcept bipartite edge — in community_input_edges
        assert "e1" in cie
        # e2: EconomicConcept->MacroIndicator (concept-concept) — OI-5: NOT in community_input_edges
        # (remains in graph.json edges for provenance, but excluded from bipartite detection)
        assert "e2" not in cie, (
            "OI-5 bipartite: concept-concept edge must NOT be in community_input_edges"
        )
        # e3: non-structural — never in community_input_edges
        assert "e3" not in cie

        # e2 is still in graph.json edges list for evidence traceability
        all_edge_ids = {e["edge_id"] for e in g["edges"]}
        assert "e2" in all_edge_ids, "e2 must remain in graph.json edges for provenance"

        # FI-A polarity on the structural edges (unchanged by OI-5)
        edge_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert edge_by_id["e1"]["polarity"] == 1   # benefits
        assert edge_by_id["e2"]["polarity"] == -1  # hurts
        assert edge_by_id["e3"]["polarity"] == 0   # co_occurs_with
