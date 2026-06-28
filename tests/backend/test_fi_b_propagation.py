"""FI-B: Deterministic propagation engine — hermetic unit tests (GitHub #105).

All tests use hand-built in-memory graph dicts.  No network calls, no LLM
calls, no filesystem I/O.

Acceptance criteria (per spec / GitHub #105):

  (1) Sign:
        F -benefits-> C  =>  positive direction
        F -hurts-> C     =>  negative direction
  (2) Decay:
        2-hop path has lower ordinal strength than an equal 1-hop path.
  (3) Multi-path:
        Two paths to the same company aggregate sign-aware (partial cancel).
  (4) PIT:
        Edge with available_at > as_of_date does not contribute.
  (5) Deterministic:
        Same input produces identical output across repeated calls.
  (6) Hermetic — no network, no disk I/O, fixture graphs only.

Known limitation (#110)
-----------------------
``causes``, ``exposed_to``, and ``sensitive_to`` currently have base_polarity
= +1 unconditionally (no per-instance direction field exists yet).  FI-B uses
whatever polarity is on the edge and will auto-improve when #110 lands.
Until then, causal/exposure edge signs are provisional and consumers should
treat impacts derived solely from those edge types as directionally uncertain.
See propagation.py module docstring for the full caveat.
"""

from __future__ import annotations

import copy

import pytest

from theme_engine.propagation import propagate


# ---------------------------------------------------------------------------
# Minimal fixture-graph helpers
# ---------------------------------------------------------------------------

def _node(entity_id: str, entity_type: str) -> dict:
    return {"entity_id": entity_id, "entity_type": entity_type}


def _edge(
    edge_id: str,
    src: str,
    tgt: str,
    polarity: int,
    propagation_weight: float = 0.8,
    available_at: str | None = None,
) -> dict:
    """Build a minimal edge dict matching graph.json's FI-A fields."""
    e: dict = {
        "edge_id": edge_id,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "polarity": polarity,
        "propagation_weight": propagation_weight,
    }
    if available_at is not None:
        e["available_at"] = available_at
    return e


def _graph(nodes: list[dict], edges: list[dict], as_of_date: str = "2024-06-30") -> dict:
    return {"as_of_date": as_of_date, "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# (1) Sign tests: benefits (+1) and hurts (-1)
# ---------------------------------------------------------------------------

class TestSignDirection:
    """A +shock on trigger F propagates sign correctly through each edge type."""

    def test_benefits_edge_yields_positive_direction(self):
        """F -benefits(+1)-> C: positive shock => positive impact on C."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=+1)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        assert results[0]["company_id"] == "C"
        assert results[0]["direction"] == +1

    def test_hurts_edge_yields_negative_direction(self):
        """F -hurts(-1)-> C: positive shock => negative impact on C."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=-1)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        assert results[0]["company_id"] == "C"
        assert results[0]["direction"] == -1

    def test_negative_shock_on_benefits_edge_yields_negative(self):
        """F -benefits(+1)-> C: negative shock => negative impact on C."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=+1)],
        )
        results = propagate(g, trigger_id="F", shock=-1.0)
        assert results[0]["direction"] == -1

    def test_negative_shock_on_hurts_edge_yields_positive(self):
        """F -hurts(-1)-> C: negative shock => positive impact on C (double negative)."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=-1)],
        )
        results = propagate(g, trigger_id="F", shock=-1.0)
        assert results[0]["direction"] == +1

    def test_two_hop_sign_product_benefits_then_hurts(self):
        """F -benefits(+1)-> I -hurts(-1)-> C: path sign = (+1)*(-1) = -1."""
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C", "Company"),
            ],
            edges=[
                _edge("e1", "F", "I", polarity=+1),
                _edge("e2", "I", "C", polarity=-1),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        assert results[0]["company_id"] == "C"
        assert results[0]["direction"] == -1

    def test_two_hop_sign_product_hurts_then_hurts(self):
        """F -hurts(-1)-> I -hurts(-1)-> C: path sign = (-1)*(-1) = +1."""
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C", "Company"),
            ],
            edges=[
                _edge("e1", "F", "I", polarity=-1),
                _edge("e2", "I", "C", polarity=-1),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results[0]["direction"] == +1

    def test_zero_polarity_edge_does_not_propagate(self):
        """Edge with polarity=0 (e.g. co_occurs_with) must not carry a signal."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=0)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results == []


# ---------------------------------------------------------------------------
# (2) Decay tests
# ---------------------------------------------------------------------------

class TestDecay:
    """2-hop path decays below an otherwise-equal 1-hop path."""

    def test_two_hop_weaker_than_one_hop_same_weight(self):
        """With equal edge weights, 1-hop path is stronger than 2-hop path.

        Setup:
            C1 reachable via 1-hop (F -+1-> C1, weight=w)
            C2 reachable via 2-hop (F -+1-> I -+1-> C2, weight=w per hop)

        At the same edge weight w, and using default decay d:
            strength(C1) = w * d^1
            strength(C2) = w * w * d^2

        For w=1.0 and d=0.8:
            strength(C1) = 0.8
            strength(C2) = 0.64
        => C1 is stronger.
        """
        w = 1.0
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C1", "Company"),
                _node("C2", "Company"),
            ],
            edges=[
                _edge("e1", "F", "C1", polarity=+1, propagation_weight=w),
                _edge("e2", "F", "I",  polarity=+1, propagation_weight=w),
                _edge("e3", "I", "C2", polarity=+1, propagation_weight=w),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, decay=0.8)
        by_id = {r["company_id"]: r for r in results}
        assert by_id["C1"]["strength"] > by_id["C2"]["strength"], (
            f"1-hop C1 strength {by_id['C1']['strength']} should exceed "
            f"2-hop C2 strength {by_id['C2']['strength']}"
        )

    def test_strength_matches_formula_one_hop(self):
        """strength = abs(shock) * propagation_weight * decay^1."""
        shock, w, decay = 1.0, 0.7, 0.9
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=+1, propagation_weight=w)],
        )
        results = propagate(g, trigger_id="F", shock=shock, decay=decay)
        expected = abs(shock) * w * (decay ** 1)
        assert abs(results[0]["strength"] - expected) < 1e-10

    def test_strength_matches_formula_two_hops(self):
        """strength = abs(shock) * w1 * w2 * decay^2."""
        shock, w1, w2, decay = 1.0, 0.6, 0.5, 0.8
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C", "Company"),
            ],
            edges=[
                _edge("e1", "F", "I", polarity=+1, propagation_weight=w1),
                _edge("e2", "I", "C", polarity=+1, propagation_weight=w2),
            ],
        )
        results = propagate(g, trigger_id="F", shock=shock, decay=decay)
        expected = abs(shock) * w1 * w2 * (decay ** 2)
        assert abs(results[0]["strength"] - expected) < 1e-10

    def test_custom_decay_zero_means_only_first_hop_reachable(self):
        """decay=0.0 means contributions at hop>=1 are 0; nothing appears in output."""
        # With decay=0, shock * w * 0^1 = 0 => no impact recorded
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=+1, propagation_weight=0.8)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, decay=0.0)
        # Zero-contribution entries are dropped
        assert results == []

    def test_results_sorted_by_descending_strength(self):
        """Strongest company appears first in the output list."""
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("I",  "EconomicConcept"),
                _node("C1", "Company"),   # 1-hop
                _node("C2", "Company"),   # 2-hop (weaker)
            ],
            edges=[
                _edge("e1", "F",  "C1", polarity=+1, propagation_weight=1.0),
                _edge("e2", "F",  "I",  polarity=+1, propagation_weight=1.0),
                _edge("e3", "I",  "C2", polarity=+1, propagation_weight=1.0),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results[0]["company_id"] == "C1"
        assert results[1]["company_id"] == "C2"


# ---------------------------------------------------------------------------
# (3) Multi-path sign-aware aggregation
# ---------------------------------------------------------------------------

class TestMultiPathAggregation:
    """Two paths to the same company aggregate sign-aware (partial cancel)."""

    def test_positive_and_negative_paths_partially_cancel(self):
        """Two paths to C with opposite signs result in partial cancellation.

        Setup (positive shock +1):
            Path A: F -benefits(+1)-> C  weight=0.8  => contribution = +0.8 * d
            Path B: F -hurts(-1)->    C  weight=0.5  => contribution = -0.5 * d

        aggregate = d*(0.8 - 0.5) = d*0.3  => direction +1, strength < strength_of_path_A
        """
        decay = 0.8
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_pos", "F", "C", polarity=+1, propagation_weight=0.8),
                _edge("e_neg", "F", "C", polarity=-1, propagation_weight=0.5),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, decay=decay)
        assert len(results) == 1
        r = results[0]
        assert r["company_id"] == "C"
        assert r["direction"] == +1  # net positive
        expected_strength = (0.8 - 0.5) * decay
        assert abs(r["strength"] - expected_strength) < 1e-10

    def test_equal_opposite_paths_fully_cancel(self):
        """Equal positive and negative paths perfectly cancel => no entry in output."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_pos", "F", "C", polarity=+1, propagation_weight=0.7),
                _edge("e_neg", "F", "C", polarity=-1, propagation_weight=0.7),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        # aggregate = 0, dropped from output
        assert results == []

    def test_two_positive_paths_reinforce(self):
        """Two positive paths to the same company accumulate (reinforce)."""
        # Path 1: F -> C directly (benefits, w=0.6)
        # Path 2: F -> I -> C (both benefits, w=0.8 each)
        decay = 0.8
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C", "Company"),
            ],
            edges=[
                _edge("e1", "F", "C", polarity=+1, propagation_weight=0.6),
                _edge("e2", "F", "I", polarity=+1, propagation_weight=0.8),
                _edge("e3", "I", "C", polarity=+1, propagation_weight=0.8),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, decay=decay)
        assert len(results) == 1
        r = results[0]
        assert r["direction"] == +1
        # strength > single 1-hop path (0.6 * decay = 0.48)
        one_hop = 0.6 * decay
        assert r["strength"] > one_hop

    def test_paths_list_records_all_contributing_paths(self):
        """Multiple paths to the same company are all recorded in ``paths``."""
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
                _node("C", "Company"),
            ],
            edges=[
                _edge("e1", "F", "C", polarity=+1, propagation_weight=0.8),
                _edge("e2", "F", "I", polarity=+1, propagation_weight=0.8),
                _edge("e3", "I", "C", polarity=+1, propagation_weight=0.8),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        paths = results[0]["paths"]
        assert len(paths) == 2
        path_lengths = sorted(len(p) for p in paths)
        assert path_lengths == [1, 2]  # 1-hop and 2-hop

    def test_negative_dominant_path_yields_negative_direction(self):
        """When the negative path outweighs the positive, direction is -1."""
        decay = 0.8
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_pos", "F", "C", polarity=+1, propagation_weight=0.3),
                _edge("e_neg", "F", "C", polarity=-1, propagation_weight=0.9),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, decay=decay)
        assert len(results) == 1
        assert results[0]["direction"] == -1


# ---------------------------------------------------------------------------
# (4) Point-in-time tests
# ---------------------------------------------------------------------------

class TestPointInTime:
    """Future-dated edges (available_at > as_of_date) must not contribute."""

    def test_future_dated_edge_excluded(self):
        """An edge with available_at > as_of_date does not contribute."""
        as_of = "2024-06-30"
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_future", "F", "C", polarity=+1, propagation_weight=0.8,
                      available_at="2024-12-31"),  # future
            ],
            as_of_date=as_of,
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results == []

    def test_past_dated_edge_included(self):
        """An edge with available_at <= as_of_date contributes normally."""
        as_of = "2024-06-30"
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_past", "F", "C", polarity=+1, propagation_weight=0.8,
                      available_at="2024-01-01"),  # past
            ],
            as_of_date=as_of,
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1

    def test_exact_as_of_date_edge_included(self):
        """Edge available exactly on as_of_date is included (<=, not <)."""
        as_of = "2024-06-30"
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_exact", "F", "C", polarity=+1, propagation_weight=0.8,
                      available_at="2024-06-30"),  # exact
            ],
            as_of_date=as_of,
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1

    def test_as_of_date_kwarg_overrides_graph_field(self):
        """The ``as_of_date`` kwarg is used when provided, overriding graph dict."""
        # Graph says as_of=2024-06-30, but we pass as_of_date=2023-12-31 via kwarg.
        # The edge has available_at=2024-01-01, which is future for the kwarg date.
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e1", "F", "C", polarity=+1, propagation_weight=0.8,
                      available_at="2024-01-01"),
            ],
            as_of_date="2024-06-30",
        )
        # With kwarg as_of_date=2023-12-31, the edge is in the future -> excluded
        results = propagate(g, trigger_id="F", shock=+1.0, as_of_date="2023-12-31")
        assert results == []

    def test_edges_without_available_at_always_included(self):
        """Edges lacking available_at are trusted (graph.json is PIT-built upstream)."""
        as_of = "2020-01-01"  # very old as_of
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                # no available_at field at all
                _edge("e_no_pit", "F", "C", polarity=+1, propagation_weight=0.8),
            ],
            as_of_date=as_of,
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        # Should propagate; PIT trust is delegated to graph_build.py
        assert len(results) == 1

    def test_mixed_pit_only_valid_edges_contribute(self):
        """Only edges with available_at <= as_of contribute; future ones are skipped."""
        as_of = "2024-06-30"
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                _edge("e_ok",     "F", "C", polarity=+1, propagation_weight=0.6,
                      available_at="2024-01-01"),   # valid
                _edge("e_future", "F", "C", polarity=+1, propagation_weight=0.8,
                      available_at="2025-01-01"),   # future: excluded
            ],
            as_of_date=as_of,
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        # Only e_ok contributes: shock=1, w=0.6, decay^1=0.8 => strength=0.48
        expected = 0.6 * 0.8  # default decay=0.8
        assert abs(results[0]["strength"] - expected) < 1e-10


# ---------------------------------------------------------------------------
# (5) Determinism tests
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same input always produces identical output (seedless, no randomness)."""

    def test_repeated_calls_identical(self):
        """propagate() with same args returns exactly the same list twice."""
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("I",  "EconomicConcept"),
                _node("C1", "Company"),
                _node("C2", "Company"),
            ],
            edges=[
                _edge("e1", "F",  "C1", polarity=+1, propagation_weight=0.9),
                _edge("e2", "F",  "I",  polarity=-1, propagation_weight=0.7),
                _edge("e3", "I",  "C2", polarity=-1, propagation_weight=0.8),
                _edge("e4", "F",  "C2", polarity=+1, propagation_weight=0.6),
            ],
        )
        result_a = propagate(g, trigger_id="F", shock=+1.0)
        result_b = propagate(g, trigger_id="F", shock=+1.0)
        assert result_a == result_b

    def test_output_order_is_stable(self):
        """Sort order (descending strength, then company_id) is stable."""
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("CA", "Company"),
                _node("CB", "Company"),
                _node("CC", "Company"),
            ],
            edges=[
                _edge("e1", "F", "CA", polarity=+1, propagation_weight=0.9),
                _edge("e2", "F", "CB", polarity=+1, propagation_weight=0.6),
                _edge("e3", "F", "CC", polarity=+1, propagation_weight=0.7),
            ],
        )
        for _ in range(5):
            results = propagate(g, trigger_id="F", shock=+1.0)
            ids = [r["company_id"] for r in results]
            assert ids == ["CA", "CC", "CB"]  # sorted by strength desc

    def test_independent_of_edge_dict_ordering(self):
        """Swapping the edge list order should not change the output."""
        nodes = [_node("F", "MacroIndicator"), _node("C", "Company")]
        edge_a = _edge("ea", "F", "C", polarity=+1, propagation_weight=0.8)
        edge_b = _edge("eb", "F", "C", polarity=-1, propagation_weight=0.3)

        g1 = _graph(nodes, [edge_a, edge_b])
        g2 = _graph(nodes, [edge_b, edge_a])

        r1 = propagate(g1, trigger_id="F", shock=+1.0)
        r2 = propagate(g2, trigger_id="F", shock=+1.0)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Edge / boundary cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary cases: empty graph, unknown trigger, cycle prevention, hop cap."""

    def test_empty_graph_returns_empty(self):
        g = _graph(nodes=[], edges=[])
        assert propagate(g, trigger_id="F", shock=+1.0) == []

    def test_unknown_trigger_returns_empty(self):
        g = _graph(
            nodes=[_node("C", "Company")],
            edges=[],
        )
        assert propagate(g, trigger_id="NONEXISTENT", shock=+1.0) == []

    def test_zero_shock_returns_empty(self):
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e1", "F", "C", polarity=+1)],
        )
        assert propagate(g, trigger_id="F", shock=0.0) == []

    def test_trigger_company_node_not_self_impacted(self):
        """Trigger node itself (at hop=0) is never recorded as an impact target."""
        g = _graph(
            nodes=[_node("C", "Company")],
            edges=[],  # no outgoing edges; nothing to reach
        )
        results = propagate(g, trigger_id="C", shock=+1.0)
        assert results == []

    def test_max_hops_cap_limits_depth(self):
        """Paths longer than max_hops do not contribute."""
        # Chain: F -> I1 -> I2 -> I3 -> C  (depth 4)
        # With max_hops=2, only paths up to depth 2 reach nodes; C is at depth 4.
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("I1", "EconomicConcept"),
                _node("I2", "EconomicConcept"),
                _node("I3", "EconomicConcept"),
                _node("C",  "Company"),
            ],
            edges=[
                _edge("e1", "F",  "I1", polarity=+1, propagation_weight=0.9),
                _edge("e2", "I1", "I2", polarity=+1, propagation_weight=0.9),
                _edge("e3", "I2", "I3", polarity=+1, propagation_weight=0.9),
                _edge("e4", "I3", "C",  polarity=+1, propagation_weight=0.9),
            ],
        )
        # Company only reachable at hop 4; max_hops=2 => no result
        results = propagate(g, trigger_id="F", shock=+1.0, max_hops=2)
        assert results == []

    def test_max_hops_cap_at_three_includes_three_hop_paths(self):
        """max_hops=3 allows paths of exactly length 3."""
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("I1", "EconomicConcept"),
                _node("I2", "EconomicConcept"),
                _node("C",  "Company"),
            ],
            edges=[
                _edge("e1", "F",  "I1", polarity=+1, propagation_weight=0.9),
                _edge("e2", "I1", "I2", polarity=+1, propagation_weight=0.9),
                _edge("e3", "I2", "C",  polarity=+1, propagation_weight=0.9),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0, max_hops=3)
        assert len(results) == 1
        assert results[0]["company_id"] == "C"

    def test_cycle_does_not_cause_infinite_loop(self):
        """A cycle in the graph terminates cleanly (per-path visited set)."""
        g = _graph(
            nodes=[
                _node("F",  "MacroIndicator"),
                _node("I",  "EconomicConcept"),
                _node("C",  "Company"),
            ],
            edges=[
                _edge("e1", "F", "I", polarity=+1, propagation_weight=0.8),
                _edge("e2", "I", "F", polarity=+1, propagation_weight=0.8),  # cycle
                _edge("e3", "I", "C", polarity=+1, propagation_weight=0.8),
            ],
        )
        # Should complete without infinite recursion
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert len(results) == 1
        assert results[0]["company_id"] == "C"

    def test_no_company_nodes_returns_empty(self):
        """Graph with no Company nodes produces no impacts."""
        g = _graph(
            nodes=[
                _node("F", "MacroIndicator"),
                _node("I", "EconomicConcept"),
            ],
            edges=[_edge("e1", "F", "I", polarity=+1)],
        )
        assert propagate(g, trigger_id="F", shock=+1.0) == []


# ---------------------------------------------------------------------------
# #110 sign-blind documentation test
# ---------------------------------------------------------------------------

class TestIssue110SignBlindCaveat:
    """Document the #110 limitation: causes/exposed_to/sensitive_to are sign-blind.

    These edge types have base_polarity = +1 unconditionally (no per-instance
    direction field exists yet in edges.parquet / ontology.yml).  FI-B uses
    whatever polarity is on the edge, so the sign is correct for hurts (-1)
    and benefits (+1), but provisional for causal/exposure edges.

    This test class serves as living documentation; it asserts observable
    behaviour today and will need updating when #110 lands.
    """

    def test_causes_edge_treated_as_positive_until_110_lands(self):
        """causes edge with polarity=+1 propagates as positive (FI-A substrate).

        NOTE (#110): this polarity is unconditional today.  The economic
        relationship could be negative (e.g. 'rising rates causes bond pain'),
        but until per-instance direction is added to the edge, FI-B cannot
        distinguish.  Callers should document this uncertainty.
        """
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[
                # polarity=+1 is what graph_build.py will assign for causes today
                _edge("e_causes", "F", "C", polarity=+1),
            ],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        # Today: direction is +1 (provisional — depends on #110)
        assert results[0]["direction"] == +1, (
            "Expected +1 for causes until #110 adds per-instance direction. "
            "If this assertion fails, #110 may have landed — update this test."
        )

    def test_exposed_to_edge_treated_as_positive_until_110_lands(self):
        """exposed_to with polarity=+1 propagates as positive (provisional)."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e_exposed", "F", "C", polarity=+1)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results[0]["direction"] == +1  # provisional until #110

    def test_sensitive_to_edge_treated_as_positive_until_110_lands(self):
        """sensitive_to with polarity=+1 propagates as positive (provisional)."""
        g = _graph(
            nodes=[_node("F", "MacroIndicator"), _node("C", "Company")],
            edges=[_edge("e_sensitive", "F", "C", polarity=+1)],
        )
        results = propagate(g, trigger_id="F", shock=+1.0)
        assert results[0]["direction"] == +1  # provisional until #110
