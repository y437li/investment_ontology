"""FI-C: projected_impacts artifact — hermetic unit tests (GitHub #106).

All tests use hand-built in-memory graph dicts and a temporary run directory.
No network calls, no LLM calls, no external filesystem I/O.

Acceptance criteria:
  (1) Reaches-companies: fixture Event trigger -> >=1 projected_impact row with
      a non-empty path AND non-empty evidence_chunk_ids.
  (2) Empty-but-schema-valid: trigger that reaches no companies produces an empty
      table whose column schema is still fully intact (all columns present, correct
      types).
  (3) PIT-clean: future-dated edges do not contribute (available_at > as_of_date
      excluded by propagation layer).
  (4) Path + evidence: path is the edge chain; evidence_chunk_ids resolve via the
      same edge dict that graph_build.py writes (edge["evidence_chunk_ids"]).
  (5) direction + ordinal strength present; strength is NOT a calibrated %.
  (6) select_triggers returns only Event nodes (no Company / MacroIndicator leak).
  (7) Empty-but-schema-valid when graph has no Event nodes at all.

Known limitation (#110)
-----------------------
causes/exposed_to/sensitive_to edges have base_polarity = +1 unconditionally.
FI-C inherits FI-B's direction and does not add any direction logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from theme_engine.projected_impacts import (
    PROJECTED_IMPACTS_COLUMNS,
    SCHEMA_VERSION,
    _METHOD,
    _build_edge_index,
    _resolve_evidence_chunk_ids,
    compute_projected_impacts,
    select_triggers,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _node(entity_id: str, entity_type: str, label: str = "") -> dict:
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "label": label or entity_id,
        "attributes": {},
    }


def _edge(
    edge_id: str,
    src: str,
    tgt: str,
    polarity: int,
    propagation_weight: float = 0.8,
    evidence_chunk_ids: list[str] | None = None,
    available_at: str | None = None,
) -> dict:
    e: dict = {
        "edge_id": edge_id,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "polarity": polarity,
        "propagation_weight": propagation_weight,
        "evidence_chunk_ids": evidence_chunk_ids or [],
    }
    if available_at is not None:
        e["available_at"] = available_at
    return e


def _graph(
    nodes: list[dict],
    edges: list[dict],
    as_of_date: str = "2024-06-30",
    run_id: str = "run_test",
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "nodes": nodes,
        "edges": edges,
        "community_input_edges": [],
    }


class _FakeManifest:
    """Minimal manifest mock — only as_of_date is used by compute_projected_impacts."""
    def __init__(self, as_of_date: str = "2024-06-30") -> None:
        self.as_of_date = as_of_date


def _make_run(graph: dict, as_of_date: str = "2024-06-30") -> str:
    """Write a minimal on-disk run directory for integration tests.

    Creates the run directory inside ``settings.run_output_dir`` (which is
    already pointed at a temp dir by conftest.py via the RUN_OUTPUT_DIR env
    var).  Returns the run_id (== run directory name).
    """
    import theme_engine.runs as _runs
    # Create a unique run sub-directory inside the configured output dir
    import uuid
    run_id = f"fi_c_test_{uuid.uuid4().hex[:8]}"
    run_dir = _runs.settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)

    # Write graph.json
    (discovery / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    # Write run_manifest.json (minimal)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "created_at": "2024-06-30T00:00:00Z",
        "code_version": "test",
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "input_hash": "abc123",
        "model_config_hash": None,
        "sweep_id": None,
        "sweep_parent_id": None,
        "validation_mode": "single_snapshot",
        "sweep_position": None,
        "discovery_artifact_hashes": None,
        "discovery_frozen": False,
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    return run_id


# ---------------------------------------------------------------------------
# (6) select_triggers: only Event nodes
# ---------------------------------------------------------------------------


class TestSelectTriggers:
    """select_triggers returns only Event nodes from the graph."""

    def test_returns_event_nodes_only(self):
        g = _graph(nodes=[
            _node("E1", "Event"),
            _node("C1", "Company"),
            _node("M1", "MacroIndicator"),
            _node("E2", "Event"),
        ], edges=[])
        triggers = select_triggers(g)
        assert len(triggers) == 2
        assert all(t["entity_type"] == "Event" for t in triggers)
        ids = {t["entity_id"] for t in triggers}
        assert ids == {"E1", "E2"}

    def test_empty_graph_returns_empty(self):
        g = _graph(nodes=[], edges=[])
        assert select_triggers(g) == []

    def test_no_event_nodes_returns_empty(self):
        g = _graph(nodes=[
            _node("C1", "Company"),
            _node("M1", "MacroIndicator"),
        ], edges=[])
        assert select_triggers(g) == []

    def test_nodes_without_entity_id_excluded(self):
        g = _graph(nodes=[
            {"entity_type": "Event", "label": "no-id"},   # missing entity_id
            _node("E1", "Event"),
        ], edges=[])
        triggers = select_triggers(g)
        assert len(triggers) == 1
        assert triggers[0]["entity_id"] == "E1"


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    """_build_edge_index and _resolve_evidence_chunk_ids."""

    def test_edge_index_keys_by_edge_id(self):
        g = _graph(nodes=[], edges=[
            _edge("e1", "A", "B", polarity=+1, evidence_chunk_ids=["chunk_1"]),
            _edge("e2", "B", "C", polarity=-1, evidence_chunk_ids=["chunk_2"]),
        ])
        idx = _build_edge_index(g)
        assert "e1" in idx
        assert "e2" in idx
        assert idx["e1"]["source_entity_id"] == "A"

    def test_edge_index_skips_edges_without_edge_id(self):
        g = _graph(nodes=[], edges=[
            {"source_entity_id": "A", "target_entity_id": "B"},  # no edge_id
            _edge("e1", "A", "B", polarity=+1),
        ])
        idx = _build_edge_index(g)
        assert list(idx.keys()) == ["e1"]

    def test_resolve_evidence_chunk_ids_deduplicates(self):
        g = _graph(nodes=[], edges=[
            _edge("e1", "A", "B", polarity=+1, evidence_chunk_ids=["c1", "c2"]),
            _edge("e2", "B", "C", polarity=+1, evidence_chunk_ids=["c2", "c3"]),
        ])
        idx = _build_edge_index(g)
        result = _resolve_evidence_chunk_ids(["e1", "e2"], idx)
        assert result == ["c1", "c2", "c3"]  # c2 appears only once

    def test_resolve_evidence_chunk_ids_unknown_edge_skipped(self):
        g = _graph(nodes=[], edges=[
            _edge("e1", "A", "B", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        idx = _build_edge_index(g)
        result = _resolve_evidence_chunk_ids(["e1", "NONEXISTENT"], idx)
        assert result == ["c1"]

    def test_resolve_evidence_chunk_ids_empty_input(self):
        idx: dict = {}
        assert _resolve_evidence_chunk_ids([], idx) == []


# ---------------------------------------------------------------------------
# (1) Reaches-companies: non-empty path + evidence_chunk_ids
# ---------------------------------------------------------------------------


class TestReachesCompanies:
    """compute_projected_impacts produces rows with non-empty path + chunk ids."""

    def test_event_reaches_company_via_single_hop(self):
        """Fixture: Event -benefits(+1)-> Company with evidence."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, propagation_weight=0.9,
                  evidence_chunk_ids=["chunk_001"]),
        ])
        run_id = _make_run(g)
        count = compute_projected_impacts(run_id)
        assert count >= 1

        # Read and inspect the written artifact
        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        assert out.exists()
        rows = pq.read_table(out).to_pylist()
        assert len(rows) >= 1

        row = rows[0]
        assert row["trigger_id"] == "EV1"
        assert row["trigger_kind"] == "Event"
        assert row["company_id"] == "CO1"
        assert row["direction"] == +1
        # Non-empty path
        assert isinstance(row["path"], list)
        assert len(row["path"]) >= 1
        # evidence_chunk_ids non-empty and resolvable
        assert isinstance(row["evidence_chunk_ids"], list)
        assert len(row["evidence_chunk_ids"]) >= 1
        assert "chunk_001" in row["evidence_chunk_ids"]
        # contributing_edge_ids present
        assert "e1" in row["contributing_edge_ids"]

    def test_event_reaches_company_via_two_hops(self):
        """Event -> MacroIndicator -> Company (two-hop path)."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "M1", polarity=+1, propagation_weight=0.8,
                  evidence_chunk_ids=["chunk_001"]),
            _edge("e2", "M1", "CO1", polarity=-1, propagation_weight=0.7,
                  evidence_chunk_ids=["chunk_002"]),
        ])
        run_id = _make_run(g)
        count = compute_projected_impacts(run_id)
        assert count == 1

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 1
        row = rows[0]

        assert row["trigger_id"] == "EV1"
        assert row["company_id"] == "CO1"
        assert row["direction"] == -1   # +1 * -1 = net negative

        # Path must be 2 edges long
        assert len(row["path"]) == 2
        assert set(row["path"]) == {"e1", "e2"}

        # Evidence from both edges
        assert "chunk_001" in row["evidence_chunk_ids"]
        assert "chunk_002" in row["evidence_chunk_ids"]

    def test_multiple_events_each_produce_rows(self):
        """Two Event nodes each reach a different Company."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("EV2", "Event"),
            _node("CO1", "Company"),
            _node("CO2", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
            _edge("e2", "EV2", "CO2", polarity=-1, evidence_chunk_ids=["c2"]),
        ])
        run_id = _make_run(g)
        count = compute_projected_impacts(run_id)
        assert count == 2

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 2
        triggers = {r["trigger_id"] for r in rows}
        assert triggers == {"EV1", "EV2"}


# ---------------------------------------------------------------------------
# (2) Empty-but-schema-valid artifact
# ---------------------------------------------------------------------------


class TestEmptyButSchemaValid:
    """When a trigger reaches no companies, the artifact is empty but schema-valid."""

    def test_no_event_nodes_produces_empty_valid_artifact(self):
        """No Event nodes -> no triggers -> empty table, all columns present."""
        g = _graph(nodes=[
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "M1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        count = compute_projected_impacts(run_id)
        assert count == 0

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        assert out.exists()

        table = pq.read_table(out)
        assert table.num_rows == 0
        # All required columns must be present
        for col in PROJECTED_IMPACTS_COLUMNS:
            assert col in table.schema.names, f"Missing column: {col}"

    def test_event_with_no_outgoing_causal_edges_produces_empty(self):
        """Event node exists but has no edges to any Company -> empty table."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            # Edge goes the wrong direction or uses polarity=0 (no propagation)
            _edge("e1", "CO1", "EV1", polarity=0, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        count = compute_projected_impacts(run_id)
        assert count == 0

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        table = pq.read_table(out)
        assert table.num_rows == 0
        for col in PROJECTED_IMPACTS_COLUMNS:
            assert col in table.schema.names

    def test_schema_column_types_correct_when_empty(self):
        """Column types in the empty table match the contract."""
        g = _graph(nodes=[], edges=[])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        import pyarrow as pa
        table = pq.read_table(out)
        schema = table.schema
        assert schema.field("direction").type == pa.int32()
        assert schema.field("strength").type == pa.float64()
        assert schema.field("confidence").type == pa.float64()
        assert schema.field("path").type == pa.list_(pa.string())
        assert schema.field("contributing_edge_ids").type == pa.list_(pa.string())
        assert schema.field("evidence_chunk_ids").type == pa.list_(pa.string())


# ---------------------------------------------------------------------------
# (3) PIT-clean
# ---------------------------------------------------------------------------


class TestPITClean:
    """Future-dated edges must not contribute to projected impacts."""

    def test_future_dated_edge_excluded(self):
        """Edge with available_at > as_of_date does not reach the company."""
        as_of = "2024-06-30"
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e_future", "EV1", "CO1", polarity=+1,
                  evidence_chunk_ids=["c1"],
                  available_at="2024-12-31"),  # future: excluded
        ], as_of_date=as_of)
        run_id = _make_run(g, as_of_date=as_of)
        count = compute_projected_impacts(run_id)
        assert count == 0

    def test_past_dated_edge_included(self):
        """Edge with available_at <= as_of_date is included."""
        as_of = "2024-06-30"
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e_past", "EV1", "CO1", polarity=+1,
                  evidence_chunk_ids=["c1"],
                  available_at="2024-01-01"),  # past: included
        ], as_of_date=as_of)
        run_id = _make_run(g, as_of_date=as_of)
        count = compute_projected_impacts(run_id)
        assert count == 1

    def test_mixed_pit_only_past_edge_contributes(self):
        """When one edge is future and one is past, only the past edge contributes."""
        as_of = "2024-06-30"
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e_ok", "EV1", "CO1", polarity=+1, propagation_weight=0.6,
                  evidence_chunk_ids=["c_ok"],
                  available_at="2024-01-01"),
            _edge("e_future", "EV1", "CO1", polarity=+1, propagation_weight=0.8,
                  evidence_chunk_ids=["c_future"],
                  available_at="2025-01-01"),  # excluded
        ], as_of_date=as_of)
        run_id = _make_run(g, as_of_date=as_of)
        count = compute_projected_impacts(run_id)
        assert count == 1

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 1
        # Only c_ok should appear (c_future excluded by PIT gate)
        assert "c_ok" in rows[0]["evidence_chunk_ids"]
        assert "c_future" not in rows[0]["evidence_chunk_ids"]

    def test_as_of_date_stamped_on_every_row(self):
        """Every projected_impact row carries the run's as_of_date."""
        as_of = "2023-12-31"
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
            _node("CO2", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
            _edge("e2", "EV1", "CO2", polarity=-1, evidence_chunk_ids=["c2"]),
        ], as_of_date=as_of)
        run_id = _make_run(g, as_of_date=as_of)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        for row in rows:
            assert row["as_of_date"] == as_of, (
                f"Expected as_of_date={as_of!r}, got {row['as_of_date']!r}"
            )


# ---------------------------------------------------------------------------
# (4) Path + evidence resolvability
# ---------------------------------------------------------------------------


class TestPathAndEvidence:
    """path is non-empty edge chain; evidence_chunk_ids resolve to real chunk IDs."""

    def test_path_contains_edge_ids_not_node_ids(self):
        """path entries are edge_ids (strings like 'e1'), not node ids."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("edge_alpha", "EV1", "CO1", polarity=+1,
                  evidence_chunk_ids=["chunk_a"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert rows[0]["path"] == ["edge_alpha"]

    def test_evidence_chunk_ids_union_of_all_paths(self):
        """evidence_chunk_ids is the union across all contributing edge paths."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
        ], edges=[
            # Direct path: EV1 -e1-> CO1
            _edge("e1", "EV1", "CO1", polarity=+1, propagation_weight=0.6,
                  evidence_chunk_ids=["chunk_001"]),
            # Two-hop path: EV1 -e2-> M1 -e3-> CO1
            _edge("e2", "EV1", "M1", polarity=+1, propagation_weight=0.8,
                  evidence_chunk_ids=["chunk_002"]),
            _edge("e3", "M1", "CO1", polarity=+1, propagation_weight=0.7,
                  evidence_chunk_ids=["chunk_003"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 1
        row = rows[0]

        # All three chunk IDs must appear in evidence_chunk_ids
        ev = set(row["evidence_chunk_ids"])
        assert "chunk_001" in ev
        assert "chunk_002" in ev
        assert "chunk_003" in ev

    def test_contributing_edge_ids_covers_all_paths(self):
        """contributing_edge_ids is the flat union across all paths."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
            _edge("e2", "EV1", "M1", polarity=+1, evidence_chunk_ids=["c2"]),
            _edge("e3", "M1", "CO1", polarity=+1, evidence_chunk_ids=["c3"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 1
        row = rows[0]
        contrib = set(row["contributing_edge_ids"])
        assert contrib == {"e1", "e2", "e3"}

    def test_evidence_chunk_ids_deduped_across_shared_edges(self):
        """If two paths share an edge, its chunk IDs appear only once."""
        # Two companies share e1 (EV1 -> M1) in their paths
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "M1", polarity=+1, evidence_chunk_ids=["shared_chunk"]),
            _edge("e2", "M1", "CO1", polarity=+1, evidence_chunk_ids=["shared_chunk"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        assert len(rows) == 1
        # chunk appears only once despite shared path
        chunk_list = rows[0]["evidence_chunk_ids"]
        assert chunk_list.count("shared_chunk") == 1


# ---------------------------------------------------------------------------
# (5) direction + ordinal strength; NOT a calibrated %
# ---------------------------------------------------------------------------


class TestDirectionAndStrength:
    """direction is +1/-1; strength is ordinal float, not calibrated %."""

    def test_direction_positive_when_net_positive(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        row = pq.read_table(out).to_pylist()[0]
        assert row["direction"] == +1

    def test_direction_negative_when_net_negative(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=-1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        row = pq.read_table(out).to_pylist()[0]
        assert row["direction"] == -1

    def test_strength_is_positive_float(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=-1, propagation_weight=0.75,
                  evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        row = pq.read_table(out).to_pylist()[0]
        assert row["strength"] > 0.0

    def test_strength_not_a_probability(self):
        """strength may exceed 1.0 when aggregated contributions sum > 1."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            # Multiple positive paths; strength can accumulate > 1.0
            _edge("e1", "EV1", "CO1", polarity=+1, propagation_weight=1.0,
                  evidence_chunk_ids=["c1"]),
            _edge("e2", "EV1", "CO1", polarity=+1, propagation_weight=1.0,
                  evidence_chunk_ids=["c2"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        row = pq.read_table(out).to_pylist()[0]
        # strength is ordinal; may exceed 1.0
        assert isinstance(row["strength"], float)
        assert row["strength"] > 0.0

    def test_stronger_impact_has_higher_strength(self):
        """Direct (1-hop) path has higher strength than equivalent 2-hop path."""
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),   # 1-hop
            _node("CO2", "Company"),   # 2-hop
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, propagation_weight=1.0,
                  evidence_chunk_ids=["c1"]),
            _edge("e2", "EV1", "M1", polarity=+1, propagation_weight=1.0,
                  evidence_chunk_ids=["c2"]),
            _edge("e3", "M1", "CO2", polarity=+1, propagation_weight=1.0,
                  evidence_chunk_ids=["c3"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        by_company = {r["company_id"]: r for r in rows}
        # 1-hop CO1 must be stronger than 2-hop CO2
        assert by_company["CO1"]["strength"] > by_company["CO2"]["strength"]


# ---------------------------------------------------------------------------
# Artifact contract: all columns present, correct metadata
# ---------------------------------------------------------------------------


class TestArtifactContract:
    """Verify schema_version, run_id, method, and all required columns."""

    def test_all_columns_present_on_non_empty_table(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        table = pq.read_table(out)
        for col in PROJECTED_IMPACTS_COLUMNS:
            assert col in table.schema.names, f"Missing column: {col}"

    def test_schema_version_is_constant(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        for row in rows:
            assert row["schema_version"] == SCHEMA_VERSION

    def test_method_field_is_correct(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        for row in rows:
            assert row["method"] == _METHOD

    def test_run_id_matches_manifest(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("CO1", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows = pq.read_table(out).to_pylist()
        for row in rows:
            assert row["run_id"] == run_id


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same inputs produce identical artifact on repeated calls."""

    def test_repeated_calls_produce_identical_rows(self):
        g = _graph(nodes=[
            _node("EV1", "Event"),
            _node("M1", "MacroIndicator"),
            _node("CO1", "Company"),
            _node("CO2", "Company"),
        ], edges=[
            _edge("e1", "EV1", "CO1", polarity=+1, evidence_chunk_ids=["c1"]),
            _edge("e2", "EV1", "M1", polarity=-1, evidence_chunk_ids=["c2"]),
            _edge("e3", "M1", "CO2", polarity=-1, evidence_chunk_ids=["c3"]),
        ])
        run_id = _make_run(g)
        compute_projected_impacts(run_id)

        from theme_engine import runs as _runs
        out = _runs.get_run_dir(run_id) / "discovery" / "projected_impacts.parquet"
        rows_1 = pq.read_table(out).to_pylist()

        # Rerun: overwrites the artifact
        compute_projected_impacts(run_id)
        rows_2 = pq.read_table(out).to_pylist()

        assert rows_1 == rows_2
