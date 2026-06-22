"""End-to-end M4 contract test: extract -> graph/build -> themes/discover.

Asserts:
  (a) graph.json, communities.json, theme_snapshots.json, theme_lineage.json,
      and theme_metrics.parquet conform to io_contracts column definitions.
  (b) Document nodes / mentioned_in edges are NOT in the structural graph
      (community_input_edges) -- OI-5.
  (c) Community detection is deterministic: same input -> same communities.
  (d) Single-snapshot run emits only single-snapshot metrics (momentum,
      birth_score, novelty are null) and theme_lineage is empty with
      lineage_mode='single_snapshot'.
  (e) Communities are produced by the algorithm (community_ids follow the
      deterministic format, not manually-assigned labels).

No network or LLM calls are made — RuleBasedExtractor is used throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.graph_build import (
    STRUCTURAL_EDGE_TYPES,
    EVIDENCE_EDGE_TYPES,
    EXCLUDED_NODE_TYPES,
    STRUCTURAL_NODE_TYPES,
)
from theme_engine.themes import THEME_METRICS_COLUMNS
from theme_engine.main import app

client = TestClient(app)

# Re-use the extraction fixtures (they already have 2 eligible + 1 future doc).
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"
AS_OF_DATE = "2024-06-30"


# ---------------------------------------------------------------------------
# Helper: run the full pipeline through to extraction + resolve
# ---------------------------------------------------------------------------


def _run_pipeline_to_extract(as_of_date: str = AS_OF_DATE) -> str:
    """Create run, import, clean, chunk, extract, resolve. Returns run_id."""
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    resp = client.post(
        "/api/data/import",
        json={
            "run_id": run_id,
            "documents_dir": str(FIXTURES),
            "source_manifest_path": str(FIXTURES / "source_manifest.csv"),
        },
    )
    assert resp.status_code == 200, resp.text

    resp = client.post(
        "/api/data/clean",
        json={"run_id": run_id, "documents_dir": str(FIXTURES)},
    )
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/extraction/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/extraction/resolve", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


def _run_pipeline_to_graph(as_of_date: str = AS_OF_DATE) -> str:
    """Create run, run full pipeline, build graph. Returns run_id."""
    run_id = _run_pipeline_to_extract(as_of_date)

    resp = client.post("/api/graph/build", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


def _run_pipeline_to_themes(as_of_date: str = AS_OF_DATE) -> str:
    """Create run, run full pipeline, build graph, discover themes. Returns run_id."""
    run_id = _run_pipeline_to_graph(as_of_date)

    resp = client.post("/api/themes/discover", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


# ---------------------------------------------------------------------------
# (a) graph.json contract conformance
# ---------------------------------------------------------------------------


def test_graph_json_schema_fields():
    """graph.json contains all required top-level fields from io_contracts §13."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    graph_path = run_dir / "discovery" / "graph.json"
    assert graph_path.exists(), "graph.json was not written"

    doc = json.loads(graph_path.read_text())

    required_top_level = {
        "schema_version",
        "run_id",
        "as_of_date",
        "projection",
        "structural_edge_types",
        "evidence_edge_types",
        "nodes",
        "edges",
        "community_input_edges",
    }
    for field in required_top_level:
        assert field in doc, f"graph.json missing required field: {field!r}"

    # Projection sub-fields
    proj = doc["projection"]
    assert proj["type"] == "entity_only"
    assert "node_types_in_structural_graph" in proj
    assert "excluded_node_types" in proj
    assert "Document" in proj["excluded_node_types"]

    # as_of_date matches the run
    assert doc["as_of_date"] == AS_OF_DATE
    assert doc["run_id"] == run_id

    # structural vs evidence edge type lists
    assert set(doc["structural_edge_types"]) == set(STRUCTURAL_EDGE_TYPES)
    assert set(doc["evidence_edge_types"]) == set(EVIDENCE_EDGE_TYPES)


def test_graph_json_node_fields():
    """Every node in graph.json has entity_id, entity_type, label, attributes."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    for node in doc["nodes"]:
        for field in ("entity_id", "entity_type", "label", "attributes"):
            assert field in node, f"node missing field {field!r}: {node}"


def test_graph_json_edge_fields():
    """Every edge in graph.json has required fields."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    for edge in doc["edges"]:
        for field in (
            "edge_id",
            "source_entity_id",
            "target_entity_id",
            "edge_type",
            "weight",
            "evidence_chunk_ids",
            "extraction_method",
        ):
            assert field in edge, f"edge missing field {field!r}: {edge}"


def test_graph_api_response():
    """POST /api/graph/build returns correct response shape."""
    run_id = _run_pipeline_to_extract()
    resp = client.post("/api/graph/build", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "discovery/graph.json" in body["artifacts"]
    assert isinstance(body["node_count"], int)
    assert isinstance(body["edge_count"], int)
    assert body["node_count"] >= 0
    assert body["edge_count"] >= 0


# ---------------------------------------------------------------------------
# (b) OI-5: Document nodes and mentioned_in edges NOT in structural graph
# ---------------------------------------------------------------------------


def test_oi5_no_document_nodes_in_structural_graph():
    """Document entity nodes must not appear in graph.json nodes list (OI-5)."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    for node in doc["nodes"]:
        assert node["entity_type"] not in EXCLUDED_NODE_TYPES, (
            f"Document node found in structural graph: {node}"
        )


def test_oi5_no_mentioned_in_in_community_input_edges():
    """mentioned_in edges must NOT appear in community_input_edges (OI-5)."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    # Build lookup of edge_id -> edge_type
    edge_type_by_id: dict[str, str] = {
        e["edge_id"]: e["edge_type"] for e in doc["edges"]
    }

    community_input_ids = set(doc["community_input_edges"])
    for eid in community_input_ids:
        if eid in edge_type_by_id:
            assert edge_type_by_id[eid] not in EVIDENCE_EDGE_TYPES, (
                f"Evidence edge {eid!r} (type={edge_type_by_id[eid]!r}) "
                f"found in community_input_edges — OI-5 violation"
            )


def test_oi5_community_input_edges_only_structural_types():
    """All community_input_edges must be of structural edge types only."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    edge_type_by_id: dict[str, str] = {
        e["edge_id"]: e["edge_type"] for e in doc["edges"]
    }
    structural_set = set(STRUCTURAL_EDGE_TYPES)

    for eid in doc["community_input_edges"]:
        if eid in edge_type_by_id:
            assert edge_type_by_id[eid] in structural_set, (
                f"Non-structural edge {eid!r} (type={edge_type_by_id[eid]!r}) "
                f"found in community_input_edges"
            )


def test_oi5_community_input_edges_no_document_endpoints():
    """community_input_edges must not reference Document-typed entity nodes."""
    run_id = _run_pipeline_to_graph()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "graph.json").read_text())

    structural_node_ids = {n["entity_id"] for n in doc["nodes"]}
    # By OI-5, nodes in graph.json are already non-Document; verify consistency.
    edge_by_id: dict[str, dict] = {e["edge_id"]: e for e in doc["edges"]}

    for eid in doc["community_input_edges"]:
        if eid not in edge_by_id:
            continue
        e = edge_by_id[eid]
        assert e["source_entity_id"] in structural_node_ids, (
            f"community_input_edge {eid!r} source not in structural nodes"
        )
        assert e["target_entity_id"] in structural_node_ids, (
            f"community_input_edge {eid!r} target not in structural nodes"
        )


# ---------------------------------------------------------------------------
# (a) communities.json + theme_snapshots.json + theme_lineage.json contract
# ---------------------------------------------------------------------------


def test_communities_json_schema():
    """communities.json has all required fields from io_contracts §14."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "communities.json").read_text())

    for field in ("schema_version", "run_id", "as_of_date", "algorithm", "communities"):
        assert field in doc, f"communities.json missing field: {field!r}"

    assert doc["run_id"] == run_id
    assert doc["as_of_date"] == AS_OF_DATE

    for comm in doc["communities"]:
        for field in (
            "community_id",
            "node_ids",
            "edge_ids",
            "size",
            "density",
            "top_entities",
            "top_companies",
            "theme_name",
            "theme_summary",
            "naming_model",
        ):
            assert field in comm, f"community record missing field {field!r}: {comm}"
        assert isinstance(comm["node_ids"], list)
        assert isinstance(comm["edge_ids"], list)
        assert isinstance(comm["size"], int)
        assert isinstance(comm["density"], float)
        assert isinstance(comm["top_entities"], list)
        assert isinstance(comm["top_companies"], list)


def test_theme_snapshots_json_schema():
    """theme_snapshots.json has all required fields from io_contracts §15."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "theme_snapshots.json").read_text())

    for field in ("schema_version", "run_id", "as_of_date", "snapshots"):
        assert field in doc, f"theme_snapshots.json missing field: {field!r}"

    allowed_states = {"Emerging", "Expanding", "Mature", "Crowded", "Declining", "Dormant", "Revived"}
    for snap in doc["snapshots"]:
        for field in (
            "theme_snapshot_id",
            "community_id",
            "theme_family_id",
            "state",
            "theme_name",
            "summary",
            "evidence_edge_ids",
        ):
            assert field in snap, f"snapshot missing field {field!r}: {snap}"
        assert snap["state"] in allowed_states, (
            f"invalid state: {snap['state']!r}"
        )
        assert isinstance(snap["evidence_edge_ids"], list)


def test_theme_lineage_json_schema():
    """theme_lineage.json has all required fields from io_contracts §16."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "theme_lineage.json").read_text())

    for field in ("schema_version", "run_id", "as_of_date", "lineage_mode", "lineages"):
        assert field in doc, f"theme_lineage.json missing field: {field!r}"


def test_theme_metrics_parquet_columns():
    """theme_metrics.parquet has exactly the contract columns from io_contracts §17."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    metrics_path = run_dir / "discovery" / "theme_metrics.parquet"
    assert metrics_path.exists(), "theme_metrics.parquet was not written"

    table = pq.read_table(metrics_path)
    assert list(table.schema.names) == THEME_METRICS_COLUMNS, (
        f"theme_metrics columns mismatch.\n"
        f"  expected: {THEME_METRICS_COLUMNS}\n"
        f"  got: {list(table.schema.names)}"
    )


# ---------------------------------------------------------------------------
# (d) Single-snapshot: temporal metrics are null, lineage is empty
# ---------------------------------------------------------------------------


def test_single_snapshot_temporal_metrics_are_null():
    """momentum, birth_score, novelty must be null in single-snapshot mode (spec §20)."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "theme_metrics.parquet").to_pylist()

    # If there are communities, check temporal metrics are null
    for row in rows:
        assert row["momentum"] is None, (
            f"momentum must be null in single-snapshot mode, got {row['momentum']}"
        )
        assert row["birth_score"] is None, (
            f"birth_score must be null in single-snapshot mode, got {row['birth_score']}"
        )
        assert row["novelty"] is None, (
            f"novelty must be null in single-snapshot mode, got {row['novelty']}"
        )


def test_single_snapshot_lineage_is_empty():
    """theme_lineage.json must have empty lineages list in single-snapshot mode."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "theme_lineage.json").read_text())

    assert doc["lineage_mode"] == "single_snapshot", (
        f"expected lineage_mode='single_snapshot', got {doc['lineage_mode']!r}"
    )
    assert doc["lineages"] == [], (
        f"expected empty lineages list in single-snapshot mode, got {doc['lineages']}"
    )


def test_single_snapshot_metrics_present():
    """strength, cohesion, saturation must be non-null in single-snapshot mode."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "theme_metrics.parquet").to_pylist()

    for row in rows:
        assert row["strength"] is not None, "strength must be present"
        assert row["cohesion"] is not None, "cohesion must be present"
        assert row["saturation"] is not None, "saturation must be present"
        assert isinstance(row["strength"], float), "strength must be float"
        assert isinstance(row["cohesion"], float), "cohesion must be float"
        assert isinstance(row["saturation"], float), "saturation must be float"


# ---------------------------------------------------------------------------
# (c) Community detection is deterministic: same input -> same communities
# ---------------------------------------------------------------------------


def test_community_detection_is_deterministic():
    """Running themes/discover twice on the same run produces identical community_ids."""
    run_id = _run_pipeline_to_graph()

    # First discovery run
    resp = client.post("/api/themes/discover", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    run_dir = Path(settings.run_output_dir) / run_id
    first_doc = json.loads((run_dir / "discovery" / "communities.json").read_text())
    first_ids = sorted(c["community_id"] for c in first_doc["communities"])

    # Second discovery run on the same graph (same run)
    resp = client.post("/api/themes/discover", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    second_doc = json.loads((run_dir / "discovery" / "communities.json").read_text())
    second_ids = sorted(c["community_id"] for c in second_doc["communities"])

    assert first_ids == second_ids, (
        f"Community detection is not deterministic!\n"
        f"  first run:  {first_ids}\n"
        f"  second run: {second_ids}"
    )


def test_community_detection_deterministic_across_fresh_runs():
    """Two independent pipeline runs on the same fixture data produce the same community_ids."""
    run_id_a = _run_pipeline_to_themes()
    run_id_b = _run_pipeline_to_themes()

    run_dir_a = Path(settings.run_output_dir) / run_id_a
    run_dir_b = Path(settings.run_output_dir) / run_id_b

    doc_a = json.loads((run_dir_a / "discovery" / "communities.json").read_text())
    doc_b = json.loads((run_dir_b / "discovery" / "communities.json").read_text())

    # Same number of communities
    assert len(doc_a["communities"]) == len(doc_b["communities"]), (
        f"Different community count across runs: {len(doc_a['communities'])} vs {len(doc_b['communities'])}"
    )

    # Sort by community_id to compare structure (ids differ between runs by design
    # since they encode run_id — compare by sorted node_ids instead)
    nodes_a = sorted(
        sorted(c["node_ids"]) for c in doc_a["communities"]
    )
    nodes_b = sorted(
        sorted(c["node_ids"]) for c in doc_b["communities"]
    )
    assert nodes_a == nodes_b, (
        f"Community node sets differ across runs!\n  A: {nodes_a}\n  B: {nodes_b}"
    )


# ---------------------------------------------------------------------------
# (e) Communities come from algorithm, not manual labels
# ---------------------------------------------------------------------------


def test_community_ids_follow_deterministic_format():
    """community_ids must follow the algorithmic format, not manual strings."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "communities.json").read_text())

    for comm in doc["communities"]:
        cid = comm["community_id"]
        # Must start with 'community_' followed by the algorithm-assigned index
        assert cid.startswith("community_"), (
            f"community_id does not follow algorithmic format: {cid!r}"
        )
        # Must not be a hard-coded manual label like 'Datacenter Power Demand'
        assert not any(cid.startswith(label) for label in ["Datacenter", "Energy", "Grid"]), (
            f"community_id looks like a manual label, not algorithmic: {cid!r}"
        )


def test_naming_model_is_deterministic():
    """naming_model must be 'deterministic' (no LLM calls in this stage)."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id
    doc = json.loads((run_dir / "discovery" / "communities.json").read_text())

    for comm in doc["communities"]:
        assert comm["naming_model"] == "deterministic", (
            f"naming_model must be 'deterministic', got {comm['naming_model']!r}"
        )


# ---------------------------------------------------------------------------
# API response shape tests
# ---------------------------------------------------------------------------


def test_themes_api_response():
    """POST /api/themes/discover returns correct response shape."""
    run_id = _run_pipeline_to_graph()
    resp = client.post("/api/themes/discover", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert set(body["artifacts"]) == {
        "discovery/communities.json",
        "discovery/theme_snapshots.json",
        "discovery/theme_lineage.json",
        "discovery/theme_metrics.parquet",
    }
    assert isinstance(body["community_count"], int)
    assert body["community_count"] >= 0


def test_graph_build_missing_run():
    """POST /api/graph/build returns 404 when run does not exist."""
    resp = client.post("/api/graph/build", json={"run_id": "nonexistent_run_999"})
    assert resp.status_code == 404, resp.text


def test_themes_discover_missing_run():
    """POST /api/themes/discover returns 404 when run does not exist."""
    resp = client.post("/api/themes/discover", json={"run_id": "nonexistent_run_999"})
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Cross-artifact consistency
# ---------------------------------------------------------------------------


def test_communities_snapshots_cross_reference():
    """Every snapshot references a community_id that exists in communities.json."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id

    comm_doc = json.loads((run_dir / "discovery" / "communities.json").read_text())
    snap_doc = json.loads((run_dir / "discovery" / "theme_snapshots.json").read_text())

    community_ids = {c["community_id"] for c in comm_doc["communities"]}
    for snap in snap_doc["snapshots"]:
        assert snap["community_id"] in community_ids, (
            f"snapshot references unknown community_id: {snap['community_id']!r}"
        )


def test_metrics_snapshot_ids_match_snapshots():
    """Every theme_snapshot_id in theme_metrics exists in theme_snapshots.json."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id

    snap_doc = json.loads((run_dir / "discovery" / "theme_snapshots.json").read_text())
    snapshot_ids = {s["theme_snapshot_id"] for s in snap_doc["snapshots"]}

    metrics_rows = pq.read_table(
        run_dir / "discovery" / "theme_metrics.parquet"
    ).to_pylist()

    for row in metrics_rows:
        assert row["theme_snapshot_id"] in snapshot_ids, (
            f"metrics row references unknown theme_snapshot_id: {row['theme_snapshot_id']!r}"
        )


def test_community_edge_ids_reference_graph():
    """Edge ids in communities.json should reference edges from graph.json."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id

    graph_doc = json.loads((run_dir / "discovery" / "graph.json").read_text())
    comm_doc = json.loads((run_dir / "discovery" / "communities.json").read_text())

    graph_edge_ids = {e["edge_id"] for e in graph_doc["edges"]}

    for comm in comm_doc["communities"]:
        for eid in comm["edge_ids"]:
            assert eid in graph_edge_ids, (
                f"community {comm['community_id']!r} references unknown edge_id: {eid!r}"
            )


def test_all_m4_artifacts_written():
    """All four M4 artifacts are written after themes/discover."""
    run_id = _run_pipeline_to_themes()
    run_dir = Path(settings.run_output_dir) / run_id

    for artifact in [
        "discovery/graph.json",
        "discovery/communities.json",
        "discovery/theme_snapshots.json",
        "discovery/theme_lineage.json",
        "discovery/theme_metrics.parquet",
    ]:
        assert (run_dir / artifact).exists(), f"missing M4 artifact: {artifact}"


def test_oi5_injected_document_and_mentioned_in_excluded():
    """Non-vacuous OI-5: a Document entity + a mentioned_in edge are written into
    a run, and build_graph must exclude the Document node from the structural
    graph and the mentioned_in edge from community_input_edges (while keeping it
    in graph.json edges for evidence traceability)."""
    import json as _json
    import pyarrow as _pa
    import pyarrow.parquet as _pq
    from pathlib import Path as _Path
    from theme_engine import graph_build, runs
    from theme_engine.config import settings as _settings
    from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS
    from theme_engine.models import RunCreateRequest

    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    ddir = _Path(_settings.run_output_dir) / run.run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)

    def _row(cols, **kw):
        return {c: kw.get(c, "") for c in cols}

    ents = [
        _row(ENTITIES_COLUMNS, entity_id="e_concept", entity_type="EconomicConcept",
             name="Datacenter", canonical_name="Datacenter", first_seen_at="2024-01-01"),
        _row(ENTITIES_COLUMNS, entity_id="e_company", entity_type="Company",
             name="ACME", canonical_name="ACME", first_seen_at="2024-01-01"),
        _row(ENTITIES_COLUMNS, entity_id="e_doc", entity_type="Document",
             name="Filing", canonical_name="Filing", first_seen_at="2024-01-01"),
    ]
    edges = [
        _row(EDGES_COLUMNS, edge_id="ed_struct", source_entity_id="e_concept",
             target_entity_id="e_company", edge_type="benefits",
             extraction_method="document_stated", first_seen_at="2024-01-01", confidence="0.9"),
        _row(EDGES_COLUMNS, edge_id="ed_mention", source_entity_id="e_company",
             target_entity_id="e_doc", edge_type="mentioned_in",
             extraction_method="document_stated", first_seen_at="2024-01-01", confidence="0.9"),
    ]
    _pq.write_table(_pa.Table.from_pylist(ents), ddir / "entities.parquet")
    _pq.write_table(_pa.Table.from_pylist(edges), ddir / "edges.parquet")

    graph_build.build_graph(run.run_id)
    g = _json.loads((ddir / "graph.json").read_text())

    node_ids = {n["entity_id"] for n in g["nodes"]}
    assert "e_doc" not in node_ids, "Document node leaked into structural graph (OI-5)"
    assert {"e_concept", "e_company"} <= node_ids

    cie = set(g["community_input_edges"])
    assert "ed_struct" in cie, "structural document_stated edge missing from clustering input"
    assert "ed_mention" not in cie, "mentioned_in edge leaked into community_input_edges (OI-5)"

    all_edge_ids = {e["edge_id"] for e in g["edges"]}
    assert "ed_mention" in all_edge_ids, "mentioned_in edge must remain in graph.json for evidence"
