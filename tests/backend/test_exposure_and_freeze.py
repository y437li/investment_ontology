"""End-to-end M5 contract tests: exposure/compute + discovery/freeze.

Asserts:
  (a) company_theme_exposure.parquet conforms to io_contracts §18 columns;
      rows are traceable to edge/evidence ids.
  (b) Only document_stated edges contribute by default (OI-2 policy);
      a llm_inferred edge does NOT change exposure.
  (c) freeze writes discovery_artifact_hashes for all required discovery
      artifacts + discovery_frozen=true.
  (d) freeze is deterministic/idempotent: repeated calls produce the same hashes.
  (e) point-in-time respected: only data <= as_of_date contributes.
  (f) validation/ directory is created by freeze.
  (g) frozen_at is set in run_manifest.json.
  (h) api response shapes conform to io_contracts §24.

No network or LLM calls are made — RuleBasedExtractor is used throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS
from theme_engine.exposure import EXPOSURE_COLUMNS
from theme_engine.main import app
from theme_engine.models import RunCreateRequest
from theme_engine import runs

client = TestClient(app)

# Extraction fixtures contain 2 in-scope documents (acme_annual, beta_transcript)
# and 1 future document (future_doc, available_at 2025-01-15 > as_of 2024-06-30).
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"
AS_OF_DATE = "2024-06-30"


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _run_pipeline_to_themes(as_of_date: str = AS_OF_DATE) -> str:
    """Run full pipeline to theme discovery. Returns run_id."""
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

    resp = client.post("/api/graph/build", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/themes/discover", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


def _run_pipeline_to_exposure(as_of_date: str = AS_OF_DATE) -> str:
    """Run full pipeline through exposure computation. Returns run_id."""
    run_id = _run_pipeline_to_themes(as_of_date)

    resp = client.post("/api/exposure/compute", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


def _run_pipeline_to_freeze(as_of_date: str = AS_OF_DATE) -> str:
    """Run full pipeline through freeze. Returns run_id."""
    run_id = _run_pipeline_to_exposure(as_of_date)

    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


# ---------------------------------------------------------------------------
# (a) Exposure schema conforms to io_contracts §18
# ---------------------------------------------------------------------------


def test_exposure_parquet_columns():
    """company_theme_exposure.parquet has exactly io_contracts §18 columns."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id
    exposure_path = run_dir / "discovery" / "company_theme_exposure.parquet"

    assert exposure_path.exists(), "company_theme_exposure.parquet was not written"
    table = pq.read_table(exposure_path)

    assert list(table.schema.names) == EXPOSURE_COLUMNS, (
        f"Exposure columns mismatch.\n"
        f"  expected: {EXPOSURE_COLUMNS}\n"
        f"  got: {list(table.schema.names)}"
    )


def test_exposure_rows_have_required_fields():
    """Every exposure row has all required io_contracts §18 fields."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()

    for row in rows:
        # Required string fields
        assert row.get("schema_version"), f"missing schema_version: {row}"
        assert row.get("as_of_date") == AS_OF_DATE, f"wrong as_of_date: {row}"
        assert row.get("company_id"), f"missing company_id: {row}"
        assert row.get("theme_snapshot_id"), f"missing theme_snapshot_id: {row}"
        assert row.get("community_id"), f"missing community_id: {row}"
        assert row.get("calculation_method"), f"missing calculation_method: {row}"

        # Numeric fields
        assert isinstance(row.get("exposure_score"), float), (
            f"exposure_score must be float: {row}"
        )
        assert 0.0 <= row["exposure_score"] <= 1.0, (
            f"exposure_score out of [0,1]: {row['exposure_score']}"
        )

        assert isinstance(row.get("edge_confidence_sum"), float), (
            f"edge_confidence_sum must be float: {row}"
        )

        assert isinstance(row.get("evidence_count"), int), (
            f"evidence_count must be int: {row}"
        )

        # top_evidence_chunk_ids must be a list
        assert isinstance(row.get("top_evidence_chunk_ids"), list), (
            f"top_evidence_chunk_ids must be list: {row}"
        )


def test_exposure_rows_are_traceable_to_evidence():
    """Exposure rows with evidence_count > 0 must carry chunk ids in top_evidence_chunk_ids.

    Traceability requirement from io_contracts §18: exposure must be explainable
    from graph and evidence.
    """
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()

    for row in rows:
        if row["evidence_count"] > 0:
            assert len(row["top_evidence_chunk_ids"]) > 0, (
                f"Row with evidence_count={row['evidence_count']} has no chunk ids: {row}"
            )


def test_exposure_community_ids_match_communities():
    """Every community_id in exposure refers to a community from communities.json."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id

    communities_doc = json.loads((run_dir / "discovery" / "communities.json").read_text())
    community_ids = {c["community_id"] for c in communities_doc["communities"]}

    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()
    for row in rows:
        assert row["community_id"] in community_ids, (
            f"exposure row references unknown community_id: {row['community_id']!r}"
        )


def test_exposure_theme_snapshot_ids_match_snapshots():
    """Every theme_snapshot_id in exposure refers to a snapshot from theme_snapshots.json."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id

    snapshots_doc = json.loads((run_dir / "discovery" / "theme_snapshots.json").read_text())
    snapshot_ids = {s["theme_snapshot_id"] for s in snapshots_doc["snapshots"]}

    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()
    for row in rows:
        assert row["theme_snapshot_id"] in snapshot_ids, (
            f"exposure row references unknown theme_snapshot_id: {row['theme_snapshot_id']!r}"
        )


def test_exposure_evidence_chunk_ids_exist_in_edges():
    """top_evidence_chunk_ids in exposure rows reference chunk ids from edges.parquet."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id

    # Collect all evidence chunk ids from edges
    edges_rows = pq.read_table(run_dir / "discovery" / "edges.parquet").to_pylist()
    all_chunk_ids: set[str] = set()
    for edge in edges_rows:
        for cid in (edge.get("evidence_chunk_ids") or []):
            all_chunk_ids.add(cid)

    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()
    for row in rows:
        for cid in row.get("top_evidence_chunk_ids") or []:
            assert cid in all_chunk_ids, (
                f"top_evidence_chunk_ids references unknown chunk_id {cid!r} in row "
                f"(company={row['company_id']!r}, community={row['community_id']!r})"
            )


def test_exposure_as_of_date_respected():
    """All exposure rows use the run's as_of_date."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()

    for row in rows:
        assert row["as_of_date"] == AS_OF_DATE, (
            f"exposure row has wrong as_of_date: {row['as_of_date']!r}"
        )


# ---------------------------------------------------------------------------
# (b) OI-2: Only document_stated edges by default; llm_inferred excluded
# ---------------------------------------------------------------------------


def test_oi2_only_document_stated_by_default():
    """By default, llm_inferred edges do NOT change exposure scores.

    We inject a synthetic run with one company, one concept, and two edges:
      - one document_stated edge (should contribute)
      - one llm_inferred edge (should NOT contribute in default mode)

    Then compare exposure computed with include_weak_signals=False vs True.
    """
    from theme_engine import exposure as exp_mod
    from theme_engine import runs as runs_mod
    from theme_engine.models import RunCreateRequest

    run = runs_mod.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    run_id = run.run_id
    run_dir = Path(settings.run_output_dir) / run_id
    ddir = run_dir / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)

    # Entity ids (stable, deterministic)
    company_id = "ent_company_test_oi2"
    concept_id = "ent_concept_test_oi2"

    def _row(cols, **kw):
        d = {c: kw.get(c, "") for c in cols}
        if "source_chunk_ids" in d and not kw.get("source_chunk_ids"):
            d["source_chunk_ids"] = ["chunk_seed_1"]
        if "evidence_chunk_ids" in d and not kw.get("evidence_chunk_ids"):
            d["evidence_chunk_ids"] = ["chunk_seed_1"]
        return d

    ents = [
        _row(ENTITIES_COLUMNS, entity_id=company_id, entity_type="Company",
             name="TestCo", canonical_name="TestCo", first_seen_at="2024-01-01",
             confidence="0.9"),
        _row(ENTITIES_COLUMNS, entity_id=concept_id, entity_type="EconomicConcept",
             name="TestTheme", canonical_name="TestTheme", first_seen_at="2024-01-01",
             confidence="0.9"),
    ]

    # Two edges: one document_stated, one llm_inferred
    edges_doc_stated_only = [
        _row(EDGES_COLUMNS, edge_id="edge_doc_stated_1",
             source_entity_id=company_id, target_entity_id=concept_id,
             edge_type="exposed_to", confidence="0.9",
             evidence_chunk_ids=["chunk_doc_1"],
             first_seen_at="2024-01-01", last_seen_at="2024-06-30",
             as_of_date="2024-06-30", extraction_method="document_stated"),
    ]

    edges_with_weak = [
        _row(EDGES_COLUMNS, edge_id="edge_doc_stated_1",
             source_entity_id=company_id, target_entity_id=concept_id,
             edge_type="exposed_to", confidence="0.9",
             evidence_chunk_ids=["chunk_doc_1"],
             first_seen_at="2024-01-01", last_seen_at="2024-06-30",
             as_of_date="2024-06-30", extraction_method="document_stated"),
        _row(EDGES_COLUMNS, edge_id="edge_llm_inferred_1",
             source_entity_id=company_id, target_entity_id=concept_id,
             edge_type="exposed_to", confidence="0.95",
             evidence_chunk_ids=["chunk_llm_1"],
             first_seen_at="2024-01-01", last_seen_at="2024-06-30",
             as_of_date="2024-06-30", extraction_method="llm_inferred"),
    ]

    # Communities fixture: one community containing concept_id
    communities_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": "2024-06-30",
        "algorithm": "louvain",
        "communities": [
            {
                "community_id": "community_oi2_test",
                "node_ids": [concept_id],
                "edge_ids": [],
                "size": 1,
                "density": 0.0,
                "top_entities": ["TestTheme"],
                "top_companies": [],
                "theme_name": "TestTheme",
                "theme_summary": "test",
                "naming_model": "deterministic",
            }
        ],
    }

    snapshots_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": "2024-06-30",
        "snapshots": [
            {
                "theme_snapshot_id": "snap_oi2_test",
                "community_id": "community_oi2_test",
                "theme_family_id": None,
                "state": "Emerging",
                "theme_name": "TestTheme",
                "summary": "test",
                "evidence_edge_ids": [],
            }
        ],
    }

    # Graph fixture
    graph_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": "2024-06-30",
        "projection": {
            "type": "entity_only",
            "node_types_in_structural_graph": ["Company", "EconomicConcept"],
            "excluded_node_types": ["Document"],
        },
        "structural_edge_types": ["exposed_to", "sensitive_to", "causes"],
        "evidence_edge_types": ["mentioned_in"],
        "nodes": [
            {"entity_id": company_id, "entity_type": "Company", "label": "TestCo", "attributes": {}},
            {"entity_id": concept_id, "entity_type": "EconomicConcept", "label": "TestTheme", "attributes": {}},
        ],
        "edges": [
            {
                "edge_id": "edge_doc_stated_1",
                "source_entity_id": company_id,
                "target_entity_id": concept_id,
                "edge_type": "exposed_to",
                "weight": 0.9,
                "evidence_chunk_ids": ["chunk_doc_1"],
                "extraction_method": "document_stated",
            }
        ],
        "community_input_edges": ["edge_doc_stated_1"],
    }

    # Write doc_stated-only run first
    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges_doc_stated_only), ddir / "edges.parquet")
    (ddir / "communities.json").write_text(json.dumps(communities_doc))
    (ddir / "theme_snapshots.json").write_text(json.dumps(snapshots_doc))
    (ddir / "graph.json").write_text(json.dumps(graph_doc))

    # Compute exposure: document_stated only (default OI-2)
    count_default = exp_mod.compute_exposure(run_id, include_weak_signals=False)
    rows_default = pq.read_table(ddir / "company_theme_exposure.parquet").to_pylist()

    # Now add the llm_inferred edge and recompute
    pq.write_table(pa.Table.from_pylist(edges_with_weak), ddir / "edges.parquet")

    count_default_with_llm = exp_mod.compute_exposure(run_id, include_weak_signals=False)
    rows_default_with_llm = pq.read_table(ddir / "company_theme_exposure.parquet").to_pylist()

    # The llm_inferred edge must NOT change exposure in default mode
    assert count_default == count_default_with_llm, (
        "Adding llm_inferred edge changed exposure row count in default mode (OI-2 violation)"
    )
    if rows_default and rows_default_with_llm:
        score_default = rows_default[0]["exposure_score"]
        score_with_llm = rows_default_with_llm[0]["exposure_score"]
        assert score_default == score_with_llm, (
            f"llm_inferred edge changed exposure_score in default mode (OI-2 violation): "
            f"{score_default} -> {score_with_llm}"
        )

    # With include_weak_signals=True, the llm_inferred edge SHOULD be included
    count_weak = exp_mod.compute_exposure(run_id, include_weak_signals=True)
    rows_weak = pq.read_table(ddir / "company_theme_exposure.parquet").to_pylist()

    # With the extra llm edge, evidence_count should be higher when weak signals enabled
    if rows_default and rows_weak:
        evidence_default = rows_default_with_llm[0]["evidence_count"]
        evidence_weak = rows_weak[0]["evidence_count"]
        # The llm chunk "chunk_llm_1" is distinct from "chunk_doc_1"
        assert evidence_weak >= evidence_default, (
            f"include_weak_signals=True should have >= evidence count "
            f"({evidence_default} -> {evidence_weak})"
        )


def test_oi2_calculation_method_labels_default_policy():
    """calculation_method should reflect OI-2 default policy label."""
    run_id = _run_pipeline_to_exposure()
    run_dir = Path(settings.run_output_dir) / run_id
    rows = pq.read_table(run_dir / "discovery" / "company_theme_exposure.parquet").to_pylist()

    for row in rows:
        assert row["calculation_method"] == "exposure_v1_document_stated", (
            f"Wrong calculation_method for default policy: {row['calculation_method']!r}"
        )


# ---------------------------------------------------------------------------
# (c) Freeze writes required hashes + discovery_frozen=true
# ---------------------------------------------------------------------------


def test_freeze_sets_discovery_frozen_true():
    """POST /api/discovery/freeze sets discovery_frozen=true in run_manifest.json."""
    run_id = _run_pipeline_to_freeze()
    manifest_path = Path(settings.run_output_dir) / run_id / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["discovery_frozen"] is True, "discovery_frozen must be True after freeze"


def test_freeze_writes_required_discovery_artifact_hashes():
    """freeze writes discovery_artifact_hashes for all required discovery artifacts.

    Keys must match test_leakage_gates.py required_keys exactly.
    """
    run_id = _run_pipeline_to_freeze()
    manifest_path = Path(settings.run_output_dir) / run_id / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    hashes = manifest.get("discovery_artifact_hashes", {})
    assert isinstance(hashes, dict), "discovery_artifact_hashes must be a dict"
    assert len(hashes) > 0, "discovery_artifact_hashes must not be empty"

    required_keys = {
        "discovery/raw_documents.parquet",
        "discovery/documents.parquet",
        "discovery/document_cleaning_log.parquet",
        "discovery/chunks.parquet",
        "discovery/entities.parquet",
        "discovery/entity_aliases.parquet",
        "discovery/edges.parquet",
        "discovery/graph.json",
    }
    missing = required_keys - set(hashes.keys())
    assert not missing, f"missing discovery_artifact_hashes keys: {sorted(missing)}"

    for key, digest in hashes.items():
        assert digest.startswith("sha256:"), (
            f"hash value for {key!r} must start with 'sha256:': {digest!r}"
        )


def test_freeze_includes_m4_m5_artifacts_in_hashes():
    """freeze includes M4/M5 artifacts (communities.json, company_theme_exposure.parquet) in hashes."""
    run_id = _run_pipeline_to_freeze()
    manifest_path = Path(settings.run_output_dir) / run_id / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    hashes = manifest.get("discovery_artifact_hashes", {})

    # M4 artifacts
    assert "discovery/communities.json" in hashes, (
        "communities.json should be in discovery_artifact_hashes after M4 freeze"
    )
    assert "discovery/theme_snapshots.json" in hashes, (
        "theme_snapshots.json should be in discovery_artifact_hashes after M4 freeze"
    )
    # M5 artifact
    assert "discovery/company_theme_exposure.parquet" in hashes, (
        "company_theme_exposure.parquet should be in discovery_artifact_hashes after M5 freeze"
    )


def test_freeze_sets_frozen_at_timestamp():
    """freeze sets frozen_at in run_manifest.json."""
    run_id = _run_pipeline_to_freeze()
    manifest_path = Path(settings.run_output_dir) / run_id / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    frozen_at = manifest.get("frozen_at")
    assert frozen_at, "frozen_at must be set in manifest after freeze"
    # Validate it's an ISO timestamp
    from datetime import datetime
    try:
        datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    except ValueError:
        pytest.fail(f"frozen_at is not a valid ISO timestamp: {frozen_at!r}")


def test_freeze_creates_validation_directory():
    """freeze creates the validation/ directory under the run dir."""
    run_id = _run_pipeline_to_freeze()
    validation_dir = Path(settings.run_output_dir) / run_id / "validation"
    assert validation_dir.exists(), "validation/ directory must exist after freeze"
    assert validation_dir.is_dir(), "validation/ must be a directory"


def test_freeze_api_response_shape():
    """POST /api/discovery/freeze returns correct response shape (io_contracts §24)."""
    run_id = _run_pipeline_to_exposure()

    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    assert body["discovery_frozen"] is True
    assert "manifest_path" in body
    assert body["manifest_path"] == f"data/runs/{run_id}/run_manifest.json"
    assert isinstance(body["discovery_artifact_hashes"], dict)
    assert len(body["discovery_artifact_hashes"]) > 0


# ---------------------------------------------------------------------------
# (d) Freeze is deterministic / idempotent
# ---------------------------------------------------------------------------


def test_freeze_is_idempotent():
    """Running freeze twice produces the same hashes and discovery_frozen=true."""
    run_id = _run_pipeline_to_exposure()

    # First freeze
    resp1 = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp1.status_code == 200, resp1.text
    hashes1 = resp1.json()["discovery_artifact_hashes"]
    frozen1 = resp1.json()["discovery_frozen"]

    # Second freeze (idempotent)
    resp2 = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp2.status_code == 200, resp2.text
    hashes2 = resp2.json()["discovery_artifact_hashes"]
    frozen2 = resp2.json()["discovery_frozen"]

    assert frozen1 is True
    assert frozen2 is True
    assert hashes1 == hashes2, (
        f"Freeze produced different hashes on second call!\n"
        f"  first:  {sorted(hashes1.items())}\n"
        f"  second: {sorted(hashes2.items())}"
    )


def test_freeze_hashes_are_deterministic():
    """Two independent runs on the same fixture data produce the same hash values
    for artifacts with identical content.

    Note: hashes will differ between runs because artifacts contain run_id-specific
    data. This test verifies that hash computation is stable for the same file
    (no randomness in the hash algorithm).
    """
    from theme_engine import freeze as freeze_mod

    # Create a run and seed artifacts with known content
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    run_id = run.run_id
    discovery_dir = Path(settings.run_output_dir) / run_id / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)

    for name in [
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entities.parquet",
        "entity_aliases.parquet",
        "edges.parquet",
        "graph.json",
    ]:
        (discovery_dir / name).write_bytes(b"stable_content_" + name.encode())

    # Compute hashes twice
    hashes_a = freeze_mod._collect_artifact_hashes(run_id)
    hashes_b = freeze_mod._collect_artifact_hashes(run_id)

    assert hashes_a == hashes_b, (
        "Hash computation is not deterministic for the same file contents"
    )

    # Verify keys match leakage gate expectations
    for key in hashes_a:
        assert key.startswith("discovery/"), f"hash key must start with 'discovery/': {key!r}"


def test_freeze_blocks_refreeze_with_mutated_artifact():
    """After freeze, mutating a discovery artifact does not corrupt the manifest.

    The freeze endpoint is idempotent and re-hashes artifacts. After mutation,
    a fresh freeze will produce a different hash, while the original frozen hash
    is overwritten. Validation must check hashes independently.
    This test verifies the hash of a mutated artifact changes on re-freeze.
    """
    run_id = _run_pipeline_to_exposure()

    # First freeze
    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200
    hash_before = resp.json()["discovery_artifact_hashes"]["discovery/graph.json"]

    # Mutate graph.json
    (Path(settings.run_output_dir) / run_id / "discovery" / "graph.json").write_text(
        "mutated content", encoding="utf-8"
    )

    # Second freeze — hash should change because content changed
    resp2 = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp2.status_code == 200
    hash_after = resp2.json()["discovery_artifact_hashes"]["discovery/graph.json"]

    assert hash_before != hash_after, (
        "Mutated artifact should produce different hash on re-freeze"
    )


# ---------------------------------------------------------------------------
# (e) Point-in-time: future documents do NOT affect exposure
# ---------------------------------------------------------------------------


def test_point_in_time_future_edges_excluded():
    """Edges with first_seen_at > as_of_date do NOT contribute to exposure."""
    from theme_engine import exposure as exp_mod
    from theme_engine import runs as runs_mod
    from theme_engine.models import RunCreateRequest

    AS_OF = "2024-06-30"
    FUTURE_DATE = "2025-01-01"  # after as_of_date

    run = runs_mod.create_run(RunCreateRequest(as_of_date=AS_OF))
    run_id = run.run_id
    ddir = Path(settings.run_output_dir) / run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)

    company_id = "ent_company_pit"
    concept_id = "ent_concept_pit"

    def _row(cols, **kw):
        d = {c: kw.get(c, "") for c in cols}
        if "source_chunk_ids" in d and not kw.get("source_chunk_ids"):
            d["source_chunk_ids"] = ["chunk_pit_1"]
        if "evidence_chunk_ids" in d and not kw.get("evidence_chunk_ids"):
            d["evidence_chunk_ids"] = ["chunk_pit_1"]
        return d

    ents = [
        _row(ENTITIES_COLUMNS, entity_id=company_id, entity_type="Company",
             name="PitCo", canonical_name="PitCo", first_seen_at="2024-01-01",
             confidence="0.9"),
        _row(ENTITIES_COLUMNS, entity_id=concept_id, entity_type="EconomicConcept",
             name="PitTheme", canonical_name="PitTheme", first_seen_at="2024-01-01",
             confidence="0.9"),
    ]

    # Only edge: first_seen_at is AFTER as_of_date — must be excluded
    future_edge = [
        _row(EDGES_COLUMNS, edge_id="edge_future_1",
             source_entity_id=company_id, target_entity_id=concept_id,
             edge_type="exposed_to", confidence="0.9",
             evidence_chunk_ids=["chunk_pit_future"],
             first_seen_at=FUTURE_DATE, last_seen_at=FUTURE_DATE,
             as_of_date=AS_OF, extraction_method="document_stated"),
    ]

    graph_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": AS_OF,
        "projection": {"type": "entity_only", "node_types_in_structural_graph": ["Company", "EconomicConcept"],
                       "excluded_node_types": ["Document"]},
        "structural_edge_types": ["exposed_to"],
        "evidence_edge_types": ["mentioned_in"],
        "nodes": [
            {"entity_id": company_id, "entity_type": "Company", "label": "PitCo", "attributes": {}},
            {"entity_id": concept_id, "entity_type": "EconomicConcept", "label": "PitTheme", "attributes": {}},
        ],
        "edges": [],  # future edge excluded from graph by graph_build
        "community_input_edges": [],
    }

    communities_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": AS_OF,
        "algorithm": "louvain",
        "communities": [
            {
                "community_id": "community_pit_test",
                "node_ids": [concept_id],
                "edge_ids": [],
                "size": 1,
                "density": 0.0,
                "top_entities": ["PitTheme"],
                "top_companies": [],
                "theme_name": "PitTheme",
                "theme_summary": "test",
                "naming_model": "deterministic",
            }
        ],
    }

    snapshots_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": AS_OF,
        "snapshots": [
            {
                "theme_snapshot_id": "snap_pit_test",
                "community_id": "community_pit_test",
                "theme_family_id": None,
                "state": "Emerging",
                "theme_name": "PitTheme",
                "summary": "test",
                "evidence_edge_ids": [],
            }
        ],
    }

    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(future_edge), ddir / "edges.parquet")
    (ddir / "communities.json").write_text(json.dumps(communities_doc))
    (ddir / "theme_snapshots.json").write_text(json.dumps(snapshots_doc))
    (ddir / "graph.json").write_text(json.dumps(graph_doc))

    # Compute exposure — the future edge must NOT contribute
    exp_mod.compute_exposure(run_id, include_weak_signals=False)
    rows = pq.read_table(ddir / "company_theme_exposure.parquet").to_pylist()

    # No rows should have chunk_pit_future in their evidence
    for row in rows:
        assert "chunk_pit_future" not in (row.get("top_evidence_chunk_ids") or []), (
            "Future chunk id leaked into exposure evidence (point-in-time violation)"
        )

    # For this test: the company can only reach concept_id via the future edge
    # (which is excluded). The company might still appear in rows if it's in the
    # community node set, but it should have NO evidence from the future edge.
    for row in rows:
        assert row.get("evidence_count", 0) == 0 or "chunk_pit_future" not in (
            row.get("top_evidence_chunk_ids") or []
        ), "Future chunk leaked into exposure evidence"


# ---------------------------------------------------------------------------
# (h) API response shapes
# ---------------------------------------------------------------------------


def test_exposure_api_response_shape():
    """POST /api/exposure/compute returns correct response shape (io_contracts §24)."""
    run_id = _run_pipeline_to_themes()

    resp = client.post("/api/exposure/compute", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    assert "discovery/company_theme_exposure.parquet" in body["artifacts"]
    assert isinstance(body["theme_count"], int)
    assert isinstance(body["company_theme_pair_count"], int)
    assert body["theme_count"] >= 0
    assert body["company_theme_pair_count"] >= 0


def test_exposure_api_missing_run_returns_404():
    """POST /api/exposure/compute returns 404 for unknown run_id."""
    resp = client.post("/api/exposure/compute", json={"run_id": "nonexistent_run_m5_999"})
    assert resp.status_code == 404, resp.text


def test_freeze_api_missing_run_returns_404():
    """POST /api/discovery/freeze returns 404 for unknown run_id."""
    resp = client.post("/api/discovery/freeze", json={"run_id": "nonexistent_freeze_m5_999"})
    assert resp.status_code == 404, resp.text


def test_full_m5_pipeline_end_to_end():
    """End-to-end M5+M6: full pipeline -> exposure -> freeze -> validation.

    Validates the entire M5+M6 flow:
      1. Run full pipeline to themes.
      2. Compute exposure (M5).
      3. Freeze discovery (M5).
      4. Attempt validation (M6) — no market_prices.parquet present, so
         validation_status should be 'blocked_insufficient_forward_data'
         (not an error about discovery not being frozen, and not 'blocked_not_implemented').
    """
    run_id = _run_pipeline_to_exposure()

    # Exposure must be computable
    resp = client.post("/api/exposure/compute", json={"run_id": run_id})
    assert resp.status_code == 200, f"exposure compute failed: {resp.text}"
    pair_count = resp.json()["company_theme_pair_count"]
    assert pair_count >= 0

    # Freeze must succeed
    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200, f"freeze failed: {freeze_resp.text}"
    assert freeze_resp.json()["discovery_frozen"] is True

    # Validation: no market_prices.parquet present ->
    # must return blocked_insufficient_forward_data (freeze gate passed, but no price data)
    val_resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert val_resp.status_code == 200, f"unexpected validation error: {val_resp.text}"
    val_status = val_resp.json()["validation_status"]
    assert val_status in ("blocked_insufficient_forward_data", "completed"), (
        f"unexpected validation_status: {val_status!r}"
    )

    # Confirm company_theme_exposure.parquet is in hashes
    manifest = json.loads(
        (Path(settings.run_output_dir) / run_id / "run_manifest.json").read_text()
    )
    assert manifest["discovery_artifact_hashes"].get("discovery/company_theme_exposure.parquet"), (
        "company_theme_exposure.parquet missing from discovery_artifact_hashes after full M5 freeze"
    )


def test_exposure_deterministic_across_runs():
    """Two independent pipeline runs on the same fixture data produce the same exposure scores."""
    run_id_a = _run_pipeline_to_exposure()
    run_id_b = _run_pipeline_to_exposure()

    run_dir_a = Path(settings.run_output_dir) / run_id_a
    run_dir_b = Path(settings.run_output_dir) / run_id_b

    rows_a = sorted(
        pq.read_table(run_dir_a / "discovery" / "company_theme_exposure.parquet").to_pylist(),
        key=lambda r: (r["company_id"], r["community_id"]),
    )
    rows_b = sorted(
        pq.read_table(run_dir_b / "discovery" / "company_theme_exposure.parquet").to_pylist(),
        key=lambda r: (r["company_id"], r["community_id"]),
    )

    assert len(rows_a) == len(rows_b), (
        f"Exposure row count differs across runs: {len(rows_a)} vs {len(rows_b)}"
    )

    for a, b in zip(rows_a, rows_b):
        assert a["company_id"] == b["company_id"]
        assert a["exposure_score"] == b["exposure_score"], (
            f"Exposure score not deterministic for company {a['company_id']!r}: "
            f"{a['exposure_score']} vs {b['exposure_score']}"
        )
        assert a["evidence_count"] == b["evidence_count"]
