"""FI-F: Projection UI endpoint tests (GitHub #109).

Tests for:
  GET /api/themes/{run_id}/projections/triggers
  GET /api/themes/{run_id}/projections?trigger=<trigger_id>

All tests are hermetic: hand-built in-memory projected_impacts.parquet +
graph.json, written to a temp run directory.  No network calls, no LLM.

Acceptance criteria verified:
  (1) Trigger list: returns all unique Event triggers with label + company_count.
  (2) Projections: ranked (strongest first) with direction, strength, path_graph,
      evidence_chunk_ids.
  (3) Empty trigger: impact_count==0 AND empty_reason is set (never null/blank).
  (4) Unknown trigger: empty_reason set (not 404).
  (5) PIT-clean: endpoint reads from the already-PIT-clean artifact; no leakage.
  (6) Hypothetical: endpoints carry no opinion on hypothetical labelling
      (that is the UI's job), but the backend test verifies shape integrity.
  (7) sign_blind flag: True when all path edges are causes/exposed_to/sensitive_to.
  (8) Triggers endpoint raises 404 when projected_impacts.parquet is absent.
  (9) path_graph contains correct nodes and edges for the given path.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Bootstrap path and imports
# ---------------------------------------------------------------------------

import sys
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine.main import app  # noqa: E402
from theme_engine import runs as _runs  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_run(
    impacts: list[dict],
    graph: dict | None = None,
    as_of_date: str = "2024-06-30",
) -> str:
    """Write a minimal run directory with projected_impacts.parquet + optional graph.json."""
    run_id = f"fi_f_test_{uuid.uuid4().hex[:8]}"
    run_dir = _runs.settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)

    # Write run_manifest.json
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
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Write projected_impacts.parquet
    if impacts:
        # Build column arrays matching the FI-C schema
        columns = {
            "schema_version": pa.array([r.get("schema_version", "1.0") for r in impacts], type=pa.string()),
            "run_id":          pa.array([r.get("run_id", run_id) for r in impacts], type=pa.string()),
            "as_of_date":      pa.array([r.get("as_of_date", as_of_date) for r in impacts], type=pa.string()),
            "trigger_id":      pa.array([r["trigger_id"] for r in impacts], type=pa.string()),
            "trigger_kind":    pa.array([r.get("trigger_kind", "Event") for r in impacts], type=pa.string()),
            "company_id":      pa.array([r["company_id"] for r in impacts], type=pa.string()),
            "direction":       pa.array([r.get("direction", 1) for r in impacts], type=pa.int32()),
            "strength":        pa.array([r.get("strength", 0.5) for r in impacts], type=pa.float64()),
            "path":            pa.array([r.get("path", []) for r in impacts], type=pa.list_(pa.string())),
            "contributing_edge_ids": pa.array([r.get("contributing_edge_ids", []) for r in impacts], type=pa.list_(pa.string())),
            "evidence_chunk_ids": pa.array([r.get("evidence_chunk_ids", []) for r in impacts], type=pa.list_(pa.string())),
            "confidence":      pa.array([r.get("confidence", 0.5) for r in impacts], type=pa.float64()),
            "method":          pa.array([r.get("method", "propagation_v1_event_trigger") for r in impacts], type=pa.string()),
        }
        table = pa.table(columns)
    else:
        # Schema-valid empty table
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("run_id", pa.string()),
            ("as_of_date", pa.string()),
            ("trigger_id", pa.string()),
            ("trigger_kind", pa.string()),
            ("company_id", pa.string()),
            ("direction", pa.int32()),
            ("strength", pa.float64()),
            ("path", pa.list_(pa.string())),
            ("contributing_edge_ids", pa.list_(pa.string())),
            ("evidence_chunk_ids", pa.list_(pa.string())),
            ("confidence", pa.float64()),
            ("method", pa.string()),
        ])
        table = pa.table({f.name: pa.array([], type=f.type) for f in schema}, schema=schema)

    pq.write_table(table, discovery / "projected_impacts.parquet")

    # Write graph.json
    if graph is not None:
        (discovery / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    return run_id


def _graph(nodes: list[dict], edges: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "test",
        "as_of_date": "2024-06-30",
        "nodes": nodes,
        "edges": edges,
        "community_input_edges": [],
    }


def _node(entity_id: str, entity_type: str, label: str = "") -> dict:
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "label": label or entity_id,
        "level": "macro" if entity_type == "Event" else "company",
    }


def _edge(edge_id: str, src: str, tgt: str, edge_type: str = "benefits") -> dict:
    return {
        "edge_id": edge_id,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "edge_type": edge_type,
        "polarity": 1,
        "propagation_weight": 0.8,
        "evidence_chunk_ids": [f"chunk_{edge_id}"],
    }


# ---------------------------------------------------------------------------
# (8) Triggers endpoint 404 when artifact absent
# ---------------------------------------------------------------------------


class TestTriggersMissingArtifact:
    """GET /projections/triggers returns 404 when parquet is absent."""

    def test_404_when_projected_impacts_absent(self):
        run_id = f"fi_f_noart_{uuid.uuid4().hex[:8]}"
        run_dir = _runs.settings.run_output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0", "run_id": run_id, "as_of_date": "2024-06-30",
            "created_at": "2024-06-30T00:00:00Z", "code_version": "test",
            "universe_config": "x", "pipeline_config": "x", "validation_config": "x",
            "input_hash": "x", "model_config_hash": None,
            "sweep_id": None, "sweep_parent_id": None,
            "validation_mode": "single_snapshot", "sweep_position": None,
            "discovery_artifact_hashes": None, "discovery_frozen": False,
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest))
        resp = client.get(f"/api/themes/{run_id}/projections/triggers")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# (1) Trigger list shape and content
# ---------------------------------------------------------------------------


class TestTriggersList:
    """GET /projections/triggers returns correct triggers, labels, company counts."""

    def test_basic_trigger_list(self):
        g = _graph(
            nodes=[_node("EV1", "Event", "Trade war 2025"), _node("CO1", "Company", "Acme")],
            edges=[_edge("e1", "EV1", "CO1")],
        )
        impacts = [
            {"trigger_id": "EV1", "trigger_kind": "Event", "company_id": "CO1",
             "direction": 1, "strength": 0.8, "path": ["e1"],
             "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["chunk_e1"],
             "confidence": 0.8},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert "triggers" in data
        assert "as_of_date" in data
        assert data["trigger_count"] == 1
        trig = data["triggers"][0]
        assert trig["trigger_id"] == "EV1"
        assert trig["label"] == "Trade war 2025"
        assert trig["trigger_kind"] == "Event"
        assert trig["company_count"] == 1

    def test_multiple_triggers_sorted_alphabetically(self):
        g = _graph(
            nodes=[
                _node("EV1", "Event", "Zeta event"),
                _node("EV2", "Event", "Alpha event"),
                _node("CO1", "Company", "Acme"),
            ],
            edges=[_edge("e1", "EV1", "CO1"), _edge("e2", "EV2", "CO1")],
        )
        impacts = [
            {"trigger_id": "EV1", "trigger_kind": "Event", "company_id": "CO1",
             "direction": 1, "strength": 0.8, "path": ["e1"],
             "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"], "confidence": 0.8},
            {"trigger_id": "EV2", "trigger_kind": "Event", "company_id": "CO1",
             "direction": -1, "strength": 0.5, "path": ["e2"],
             "contributing_edge_ids": ["e2"], "evidence_chunk_ids": ["c2"], "confidence": 0.7},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections/triggers")
        assert resp.status_code == 200
        labels = [t["label"] for t in resp.json()["triggers"]]
        assert labels == sorted(labels, key=str.lower)

    def test_company_count_counts_distinct_companies(self):
        """trigger reaching 3 companies -> company_count == 3."""
        g = _graph(
            nodes=[
                _node("EV1", "Event", "Rate hike"),
                _node("CO1", "Company"),
                _node("CO2", "Company"),
                _node("CO3", "Company"),
            ],
            edges=[
                _edge("e1", "EV1", "CO1"),
                _edge("e2", "EV1", "CO2"),
                _edge("e3", "EV1", "CO3"),
            ],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "path": ["e1"],
             "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"]},
            {"trigger_id": "EV1", "company_id": "CO2", "path": ["e2"],
             "contributing_edge_ids": ["e2"], "evidence_chunk_ids": ["c2"]},
            {"trigger_id": "EV1", "company_id": "CO3", "path": ["e3"],
             "contributing_edge_ids": ["e3"], "evidence_chunk_ids": ["c3"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections/triggers")
        assert resp.status_code == 200
        assert resp.json()["triggers"][0]["company_count"] == 3


# ---------------------------------------------------------------------------
# (2) Projections: ranked impacts with direction, strength, path_graph, evidence
# ---------------------------------------------------------------------------


class TestProjectionsContent:
    """GET /projections?trigger=... returns ranked company impacts."""

    def test_basic_impact_shape(self):
        g = _graph(
            nodes=[_node("EV1", "Event", "Trade war"), _node("CO1", "Company", "Acme Corp")],
            edges=[_edge("e1", "EV1", "CO1", "benefits")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.9,
             "path": ["e1"], "contributing_edge_ids": ["e1"],
             "evidence_chunk_ids": ["chunk_001"], "confidence": 0.85},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trigger_id"] == "EV1"
        assert data["trigger_label"] == "Trade war"
        assert data["impact_count"] == 1
        assert data["empty_reason"] is None

        imp = data["impacts"][0]
        assert imp["company_id"] == "CO1"
        assert imp["company_name"] == "Acme Corp"
        assert imp["direction"] == 1
        assert imp["strength"] == pytest.approx(0.9)
        assert imp["evidence_chunk_ids"] == ["chunk_001"]

    def test_impacts_sorted_by_strength_descending(self):
        """Stronger impact (higher abs strength) comes first."""
        g = _graph(
            nodes=[
                _node("EV1", "Event"),
                _node("CO1", "Company", "Strong"),
                _node("CO2", "Company", "Weak"),
            ],
            edges=[_edge("e1", "EV1", "CO1"), _edge("e2", "EV1", "CO2")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "strength": 0.3,
             "path": ["e1"], "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"]},
            {"trigger_id": "EV1", "company_id": "CO2", "strength": 0.9,
             "path": ["e2"], "contributing_edge_ids": ["e2"], "evidence_chunk_ids": ["c2"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        result_impacts = resp.json()["impacts"]
        strengths = [i["strength"] for i in result_impacts]
        assert strengths == sorted(strengths, reverse=True)

    def test_direction_both_positive_and_negative(self):
        g = _graph(
            nodes=[
                _node("EV1", "Event"),
                _node("CO1", "Company"),
                _node("CO2", "Company"),
            ],
            edges=[_edge("e1", "EV1", "CO1"), _edge("e2", "EV1", "CO2")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.8,
             "path": ["e1"], "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"]},
            {"trigger_id": "EV1", "company_id": "CO2", "direction": -1, "strength": 0.7,
             "path": ["e2"], "contributing_edge_ids": ["e2"], "evidence_chunk_ids": ["c2"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        directions = {i["company_id"]: i["direction"] for i in resp.json()["impacts"]}
        assert directions["CO1"] == 1
        assert directions["CO2"] == -1


# ---------------------------------------------------------------------------
# (9) path_graph contains correct nodes and edges
# ---------------------------------------------------------------------------


class TestPathGraph:
    """path_graph carries the correct nodes and edges for the impact path."""

    def test_single_hop_path_graph(self):
        g = _graph(
            nodes=[
                _node("EV1", "Event", "Interest Rate Hike"),
                _node("CO1", "Company", "BankCo"),
            ],
            edges=[_edge("e1", "EV1", "CO1", "benefits")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.8,
             "path": ["e1"], "contributing_edge_ids": ["e1"],
             "evidence_chunk_ids": ["chunk_001"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        pg = resp.json()["impacts"][0]["path_graph"]

        node_ids = {n["id"] for n in pg["nodes"]}
        assert "EV1" in node_ids
        assert "CO1" in node_ids

        # Exactly one edge
        assert len(pg["edges"]) == 1
        e = pg["edges"][0]
        assert e["source"] == "EV1"
        assert e["target"] == "CO1"
        assert e["edge_type"] == "benefits"

    def test_two_hop_path_graph(self):
        g = _graph(
            nodes=[
                _node("EV1", "Event"),
                _node("M1", "MacroIndicator"),
                _node("CO1", "Company"),
            ],
            edges=[
                _edge("e1", "EV1", "M1", "causes"),
                _edge("e2", "M1", "CO1", "exposed_to"),
            ],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.6,
             "path": ["e1", "e2"], "contributing_edge_ids": ["e1", "e2"],
             "evidence_chunk_ids": ["c1", "c2"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        pg = resp.json()["impacts"][0]["path_graph"]
        node_ids = {n["id"] for n in pg["nodes"]}
        assert {"EV1", "M1", "CO1"} == node_ids
        assert len(pg["edges"]) == 2

    def test_path_graph_empty_when_edge_ids_unknown(self):
        """When path contains edge_ids not in graph.json, path_graph is empty."""
        g = _graph(nodes=[_node("EV1", "Event")], edges=[])
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO_ghost", "direction": 1,
             "strength": 0.5, "path": ["NONEXISTENT_EDGE"],
             "contributing_edge_ids": ["NONEXISTENT_EDGE"], "evidence_chunk_ids": []},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        pg = resp.json()["impacts"][0]["path_graph"]
        assert pg["nodes"] == []
        assert pg["edges"] == []


# ---------------------------------------------------------------------------
# (3) Empty trigger: impact_count==0 and empty_reason set
# ---------------------------------------------------------------------------


class TestEmptyTrigger:
    """When a trigger reaches no companies, empty_reason is always set."""

    def test_known_trigger_with_zero_impacts_has_empty_reason(self):
        """A trigger that exists but reaches no companies (e.g. isolated Event)."""
        # Build a run where the trigger IS in the graph but has 0 impact rows
        g = _graph(nodes=[_node("EV1", "Event", "Rate hike")], edges=[])
        # No impact rows for EV1
        impacts: list[dict] = []
        run_id = _make_run(impacts, graph=g)

        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["impact_count"] == 0
        assert data["empty_reason"] is not None
        assert len(data["empty_reason"]) > 0
        assert data["impacts"] == []

    def test_empty_impacts_list_never_missing(self):
        """'impacts' key always present even when count is zero."""
        g = _graph(nodes=[], edges=[])
        run_id = _make_run([], graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=SOME_EVENT")
        assert resp.status_code == 200
        data = resp.json()
        assert "impacts" in data
        assert isinstance(data["impacts"], list)


# ---------------------------------------------------------------------------
# (4) Unknown trigger: empty impacts + empty_reason, NOT 404
# ---------------------------------------------------------------------------


class TestUnknownTrigger:
    """Unknown trigger_id returns empty impacts with descriptive empty_reason."""

    def test_unknown_trigger_not_404(self):
        g = _graph(nodes=[_node("EV1", "Event")], edges=[])
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.5,
             "path": [], "contributing_edge_ids": [], "evidence_chunk_ids": []},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV_DOES_NOT_EXIST")
        assert resp.status_code == 200
        data = resp.json()
        assert data["impact_count"] == 0
        assert data["empty_reason"] is not None

    def test_missing_trigger_param_returns_400(self):
        g = _graph(nodes=[], edges=[])
        run_id = _make_run([], graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# (7) sign_blind flag
# ---------------------------------------------------------------------------


class TestSignBlind:
    """sign_blind is True iff ALL path edges are causes/exposed_to/sensitive_to."""

    def test_sign_blind_true_for_causes_path(self):
        g = _graph(
            nodes=[_node("EV1", "Event"), _node("CO1", "Company")],
            edges=[_edge("e1", "EV1", "CO1", "causes")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.8,
             "path": ["e1"], "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        assert resp.json()["impacts"][0]["sign_blind"] is True

    def test_sign_blind_false_for_benefits_path(self):
        g = _graph(
            nodes=[_node("EV1", "Event"), _node("CO1", "Company")],
            edges=[_edge("e1", "EV1", "CO1", "benefits")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.8,
             "path": ["e1"], "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"]},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        assert resp.json()["impacts"][0]["sign_blind"] is False

    def test_sign_blind_false_when_mixed_path(self):
        """One directional edge in path -> sign_blind False (direction reliable)."""
        g = _graph(
            nodes=[_node("EV1", "Event"), _node("M1", "MacroIndicator"), _node("CO1", "Company")],
            edges=[
                _edge("e1", "EV1", "M1", "causes"),      # sign-blind
                _edge("e2", "M1", "CO1", "benefits"),     # directional
            ],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.7,
             "path": ["e1", "e2"], "contributing_edge_ids": ["e1", "e2"], "evidence_chunk_ids": []},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        assert resp.json()["impacts"][0]["sign_blind"] is False

    def test_sign_blind_false_for_empty_path(self):
        """Empty path -> sign_blind is False (no edges to be sign-blind)."""
        g = _graph(nodes=[_node("EV1", "Event"), _node("CO1", "Company")], edges=[])
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.5,
             "path": [], "contributing_edge_ids": [], "evidence_chunk_ids": []},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        assert resp.json()["impacts"][0]["sign_blind"] is False


# ---------------------------------------------------------------------------
# Required fields present on all responses
# ---------------------------------------------------------------------------


class TestResponseShape:
    """Both endpoints always return required fields."""

    def test_triggers_response_fields(self):
        run_id = _make_run([], graph=_graph([], []))
        resp = client.get(f"/api/themes/{run_id}/projections/triggers")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("as_of_date", "trigger_count", "triggers"):
            assert key in data, f"Missing key: {key}"

    def test_projections_response_fields(self):
        g = _graph(nodes=[_node("EV1", "Event")], edges=[])
        run_id = _make_run([], graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("trigger_id", "trigger_kind", "trigger_label", "as_of_date",
                    "impact_count", "empty_reason", "impacts"):
            assert key in data, f"Missing key: {key}"

    def test_impact_row_fields(self):
        g = _graph(
            nodes=[_node("EV1", "Event"), _node("CO1", "Company")],
            edges=[_edge("e1", "EV1", "CO1")],
        )
        impacts = [
            {"trigger_id": "EV1", "company_id": "CO1", "direction": 1, "strength": 0.7,
             "path": ["e1"], "contributing_edge_ids": ["e1"], "evidence_chunk_ids": ["c1"],
             "confidence": 0.6},
        ]
        run_id = _make_run(impacts, graph=g)
        resp = client.get(f"/api/themes/{run_id}/projections?trigger=EV1")
        assert resp.status_code == 200
        imp = resp.json()["impacts"][0]
        for key in ("company_id", "company_name", "direction", "strength",
                    "confidence", "sign_blind", "path", "path_graph", "evidence_chunk_ids"):
            assert key in imp, f"Missing impact key: {key}"
        pg = imp["path_graph"]
        assert "nodes" in pg
        assert "edges" in pg
