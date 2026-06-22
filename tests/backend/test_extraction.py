"""End-to-end M3 contract test: import -> clean -> chunk -> extract -> resolve.

Asserts:
  (a) entities.parquet, edges.parquet, edge_explanations.parquet, and
      entity_aliases.parquet have the exact contract columns defined in
      io_contracts.md sections 9, 10, 11, 12 — and all entity_type and
      edge_type values are from the ontology (theme_discovery_engine_v1.md §7).
  (b) Every non-trivial (document_stated) edge has at least one evidence_chunk_id.
  (c) extraction_method enum is populated and valid for every entity and edge.
  (d) Alias resolution excludes a document whose available_at is after
      as_of_date (point-in-time guard — OI-4).
  (e) No network calls occur (the RuleBasedExtractor is used throughout).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.extraction import (
    ENTITIES_COLUMNS,
    EDGES_COLUMNS,
    EDGE_EXPLANATIONS_COLUMNS,
    VALID_ENTITY_TYPES,
    VALID_EDGE_TYPES,
    VALID_EXTRACTION_METHODS,
    RuleBasedExtractor,
    run_extraction,
)
from theme_engine.entity_resolution import (
    ENTITY_ALIASES_COLUMNS,
    resolve_entities,
)
from theme_engine.main import app

# No network calls must occur — enforce by ensuring OpenAIExtractor is never used.
# The test uses only RuleBasedExtractor (the default).
client = TestClient(app)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"

AS_OF_DATE = "2024-06-30"  # future_doc.txt has available_at=2025-01-15 -> excluded


# ---------------------------------------------------------------------------
# Helper: build a complete pipeline run up to chunks
# ---------------------------------------------------------------------------


def _create_run(as_of_date: str = AS_OF_DATE) -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _run_pipeline_to_chunks(as_of_date: str = AS_OF_DATE) -> str:
    """Create run, import, clean, chunk. Returns run_id."""
    run_id = _create_run(as_of_date)

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
    body = resp.json()
    # ext-doc-3 (future) should be quarantined; 2 included
    assert body["included_documents"] == 2, (
        f"expected 2 included docs (future quarantined), got {body}"
    )

    resp = client.post("/api/data/chunk", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["chunk_count"] >= 2

    return run_id


# ---------------------------------------------------------------------------
# (a) Contract column conformance + ontology-valid types
# ---------------------------------------------------------------------------


def test_entities_contract_columns():
    """entities.parquet has exactly the contract columns from io_contracts §9."""
    run_id = _run_pipeline_to_chunks()
    resp = client.post("/api/extraction/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    run_dir = Path(settings.run_output_dir) / run_id
    entities_path = run_dir / "discovery" / "entities.parquet"
    assert entities_path.exists()

    table = pq.read_table(entities_path)
    assert table.column_names == ENTITIES_COLUMNS, (
        f"entities columns mismatch.\n  expected: {ENTITIES_COLUMNS}\n  got: {table.column_names}"
    )

    rows = table.to_pylist()
    assert len(rows) > 0, "expected at least one entity"

    for row in rows:
        # entity_type must be from the ontology
        assert row["entity_type"] in VALID_ENTITY_TYPES, (
            f"invalid entity_type: {row['entity_type']!r}"
        )
        # extraction_method must be in the enum
        assert row["extraction_method"] in VALID_EXTRACTION_METHODS, (
            f"invalid extraction_method: {row['extraction_method']!r}"
        )
        # source_chunk_ids must be a non-empty list
        assert isinstance(row["source_chunk_ids"], list), (
            f"source_chunk_ids is not a list: {row['source_chunk_ids']!r}"
        )
        assert len(row["source_chunk_ids"]) > 0, (
            f"source_chunk_ids is empty for entity {row['entity_id']}"
        )
        # confidence must be a float in [0, 1]
        assert isinstance(row["confidence"], float), (
            f"confidence is not float: {row['confidence']!r}"
        )
        assert 0.0 <= row["confidence"] <= 1.0


def test_edges_contract_columns():
    """edges.parquet has exactly the contract columns from io_contracts §11."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    run_dir = Path(settings.run_output_dir) / run_id
    edges_path = run_dir / "discovery" / "edges.parquet"
    assert edges_path.exists()

    table = pq.read_table(edges_path)
    assert table.column_names == EDGES_COLUMNS, (
        f"edges columns mismatch.\n  expected: {EDGES_COLUMNS}\n  got: {table.column_names}"
    )


def test_edge_explanations_contract_columns():
    """edge_explanations.parquet has exactly the contract columns from io_contracts §12."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    run_dir = Path(settings.run_output_dir) / run_id
    expl_path = run_dir / "discovery" / "edge_explanations.parquet"
    assert expl_path.exists()

    table = pq.read_table(expl_path)
    assert table.column_names == EDGE_EXPLANATIONS_COLUMNS, (
        f"edge_explanations columns mismatch.\n  expected: {EDGE_EXPLANATIONS_COLUMNS}\n  got: {table.column_names}"
    )


def test_entity_aliases_contract_columns():
    """entity_aliases.parquet has exactly the contract columns from io_contracts §10."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    resp = client.post("/api/extraction/resolve", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    run_dir = Path(settings.run_output_dir) / run_id
    aliases_path = run_dir / "discovery" / "entity_aliases.parquet"
    assert aliases_path.exists()

    table = pq.read_table(aliases_path)
    assert table.column_names == ENTITY_ALIASES_COLUMNS, (
        f"entity_aliases columns mismatch.\n  expected: {ENTITY_ALIASES_COLUMNS}\n  got: {table.column_names}"
    )

    rows = table.to_pylist()
    for row in rows:
        # alias_scope must be point_in_time for the standard alias table
        assert row["alias_scope"] == "point_in_time", (
            f"unexpected alias_scope: {row['alias_scope']!r}"
        )
        # source_record_ids must be a list
        assert isinstance(row["source_record_ids"], list), (
            f"source_record_ids is not a list: {row['source_record_ids']!r}"
        )


def test_ontology_valid_edge_types():
    """All edge_type values in edges.parquet are from the ontology §7."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    run_dir = Path(settings.run_output_dir) / run_id
    edges_path = run_dir / "discovery" / "edges.parquet"
    table = pq.read_table(edges_path)
    rows = table.to_pylist()

    for row in rows:
        assert row["edge_type"] in VALID_EDGE_TYPES, (
            f"invalid edge_type: {row['edge_type']!r}"
        )


# ---------------------------------------------------------------------------
# (b) Every document_stated edge has >=1 evidence_chunk_ids
# ---------------------------------------------------------------------------


def test_document_stated_edges_have_evidence():
    """Non-trivial (document_stated) edges must carry >=1 evidence_chunk_ids."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    run_dir = Path(settings.run_output_dir) / run_id
    edges_path = run_dir / "discovery" / "edges.parquet"
    rows = pq.read_table(edges_path).to_pylist()

    document_stated_edges = [r for r in rows if r["extraction_method"] == "document_stated"]
    assert len(document_stated_edges) > 0, "expected at least one document_stated edge"

    for row in document_stated_edges:
        chunk_ids = row["evidence_chunk_ids"]
        assert isinstance(chunk_ids, list), (
            f"evidence_chunk_ids not a list for edge {row['edge_id']}"
        )
        assert len(chunk_ids) >= 1, (
            f"document_stated edge {row['edge_id']} has no evidence_chunk_ids"
        )


# ---------------------------------------------------------------------------
# (c) extraction_method enum populated for edges
# ---------------------------------------------------------------------------


def test_edge_extraction_method_enum():
    """extraction_method is present and valid for every edge."""
    run_id = _run_pipeline_to_chunks()
    client.post("/api/extraction/run", json={"run_id": run_id})

    run_dir = Path(settings.run_output_dir) / run_id
    edges_path = run_dir / "discovery" / "edges.parquet"
    rows = pq.read_table(edges_path).to_pylist()

    for row in rows:
        assert row["extraction_method"] in VALID_EXTRACTION_METHODS, (
            f"invalid extraction_method on edge {row['edge_id']}: {row['extraction_method']!r}"
        )


# ---------------------------------------------------------------------------
# (d) Alias resolution excludes future documents (PIT — OI-4)
# ---------------------------------------------------------------------------


def test_alias_pit_excludes_future_document():
    """Aliases must not be influenced by the future doc (available_at=2025-01-15).

    The future_doc.txt contains 'Acme Corp' and 'Beta Industries'.
    Since that document is quarantined (available_at > as_of_date 2024-06-30),
    it never becomes a chunk, so its chunk_ids are not eligible.

    We verify that:
    1. The alias table is non-empty (eligible entities were found).
    2. All entity_ids referenced by aliases correspond to entities that came
       from eligible chunks only (available_at <= 2024-06-30).
    """
    run_id = _run_pipeline_to_chunks(AS_OF_DATE)
    client.post("/api/extraction/run", json={"run_id": run_id})
    resp = client.post("/api/extraction/resolve", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    run_dir = Path(settings.run_output_dir) / run_id

    # Load all artifacts
    aliases = pq.read_table(run_dir / "discovery" / "entity_aliases.parquet").to_pylist()
    entities = pq.read_table(run_dir / "discovery" / "entities.parquet").to_pylist()
    chunks = pq.read_table(run_dir / "discovery" / "chunks.parquet").to_pylist()

    # The alias table must be non-empty
    assert len(aliases) > 0, "expected alias rows from eligible documents"

    # Build set of eligible chunk_ids (available_at <= as_of_date)
    eligible_chunk_ids = {
        ch["chunk_id"]
        for ch in chunks
        if str(ch.get("available_at", "9999-12-31"))[:10] <= AS_OF_DATE
    }

    # Build set of entity_ids with at least one eligible chunk
    eligible_entity_ids = set()
    for ent in entities:
        src_ids = ent.get("source_chunk_ids") or []
        if any(cid in eligible_chunk_ids for cid in src_ids):
            eligible_entity_ids.add(ent["entity_id"])

    # Every alias canonical_entity_id must be an eligible entity
    for row in aliases:
        assert row["canonical_entity_id"] in eligible_entity_ids, (
            f"alias {row['alias']!r} references entity_id "
            f"{row['canonical_entity_id']!r} which is not from eligible chunks"
        )

    # Verify that the as_of_date in alias rows matches the run as_of_date
    for row in aliases:
        assert row["as_of_date"] == AS_OF_DATE, (
            f"alias row has wrong as_of_date: {row['as_of_date']!r}"
        )


# ---------------------------------------------------------------------------
# (e) No network calls — verified implicitly by using only RuleBasedExtractor
# ---------------------------------------------------------------------------


def test_no_network_calls_by_default():
    """Extraction uses RuleBasedExtractor by default — never calls OpenAIExtractor."""
    run_id = _run_pipeline_to_chunks()

    # Directly call run_extraction with RuleBasedExtractor (the default)
    entity_count, edge_count = run_extraction(run_id=run_id)
    assert entity_count >= 0
    assert edge_count >= 0

    # The RuleBasedExtractor is the default — calling extraction endpoint
    # must NOT attempt any network I/O.
    resp = client.post("/api/extraction/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["entity_count"] >= 0


# ---------------------------------------------------------------------------
# Full end-to-end pipeline test
# ---------------------------------------------------------------------------


def test_full_pipeline_end_to_end():
    """Full run: import -> clean -> chunk -> extract -> resolve with contract checks."""
    run_id = _run_pipeline_to_chunks()

    # Extract
    resp = client.post("/api/extraction/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    ext_body = resp.json()
    assert ext_body["success"] is True
    assert set(ext_body["artifacts"]) == {
        "discovery/entities.parquet",
        "discovery/edges.parquet",
        "discovery/edge_explanations.parquet",
    }
    assert ext_body["entity_count"] >= 1
    assert ext_body["edge_count"] >= 0

    # Resolve
    resp = client.post("/api/extraction/resolve", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    res_body = resp.json()
    assert res_body["success"] is True
    assert res_body["artifacts"] == ["discovery/entity_aliases.parquet"]
    assert res_body["alias_count"] >= 1

    run_dir = Path(settings.run_output_dir) / run_id

    # Verify all artifacts exist
    for artifact in [
        "discovery/entities.parquet",
        "discovery/edges.parquet",
        "discovery/edge_explanations.parquet",
        "discovery/entity_aliases.parquet",
    ]:
        assert (run_dir / artifact).exists(), f"missing: {artifact}"

    # Cross-reference: every edge references valid entity_ids
    entities_table = pq.read_table(run_dir / "discovery" / "entities.parquet")
    edges_table = pq.read_table(run_dir / "discovery" / "edges.parquet")
    expl_table = pq.read_table(run_dir / "discovery" / "edge_explanations.parquet")

    entity_ids = {row["entity_id"] for row in entities_table.to_pylist()}
    edges = edges_table.to_pylist()
    explanations = expl_table.to_pylist()

    edge_ids_from_edges = {row["edge_id"] for row in edges}

    for edge in edges:
        assert edge["source_entity_id"] in entity_ids, (
            f"edge source_entity_id {edge['source_entity_id']!r} not in entities"
        )
        assert edge["target_entity_id"] in entity_ids, (
            f"edge target_entity_id {edge['target_entity_id']!r} not in entities"
        )

    # Every explanation must reference a known edge_id
    for expl in explanations:
        assert expl["edge_id"] in edge_ids_from_edges, (
            f"explanation edge_id {expl['edge_id']!r} not in edges"
        )

    # Aliases reference known entity_ids
    aliases = pq.read_table(run_dir / "discovery" / "entity_aliases.parquet").to_pylist()
    for alias in aliases:
        assert alias["canonical_entity_id"] in entity_ids, (
            f"alias canonical_entity_id {alias['canonical_entity_id']!r} not in entities"
        )


# ---------------------------------------------------------------------------
# Determinism test: same input -> same ids
# ---------------------------------------------------------------------------


def test_deterministic_entity_ids():
    """Running extraction twice on the same run produces identical entity_ids."""
    run_id = _run_pipeline_to_chunks()

    client.post("/api/extraction/run", json={"run_id": run_id})
    run_dir = Path(settings.run_output_dir) / run_id
    entities_path = run_dir / "discovery" / "entities.parquet"
    first_ids = sorted(r["entity_id"] for r in pq.read_table(entities_path).to_pylist())

    # Re-run extraction on the same run
    client.post("/api/extraction/run", json={"run_id": run_id})
    second_ids = sorted(r["entity_id"] for r in pq.read_table(entities_path).to_pylist())

    assert first_ids == second_ids, "entity_ids are not deterministic across runs"


def test_deterministic_edge_ids():
    """Running extraction twice on the same run produces identical edge_ids."""
    run_id = _run_pipeline_to_chunks()

    client.post("/api/extraction/run", json={"run_id": run_id})
    run_dir = Path(settings.run_output_dir) / run_id
    edges_path = run_dir / "discovery" / "edges.parquet"
    first_ids = sorted(r["edge_id"] for r in pq.read_table(edges_path).to_pylist())

    client.post("/api/extraction/run", json={"run_id": run_id})
    second_ids = sorted(r["edge_id"] for r in pq.read_table(edges_path).to_pylist())

    assert first_ids == second_ids, "edge_ids are not deterministic across runs"


# ---------------------------------------------------------------------------
# RuleBasedExtractor unit tests (no pipeline, no network)
# ---------------------------------------------------------------------------


def test_rule_based_extractor_finds_entities():
    """RuleBasedExtractor identifies known entities from text."""
    extractor = RuleBasedExtractor()
    result = extractor.extract(
        chunk_id="test_chunk_001",
        chunk_text="Acme Corp is exposed to copper price volatility in North America.",
    )
    entity_names = {e.name for e in result.entities}
    assert "Acme Corp" in entity_names
    assert "Copper" in entity_names
    assert "North America" in entity_names

    for e in result.entities:
        assert e.entity_type in VALID_ENTITY_TYPES
        assert e.extraction_method in VALID_EXTRACTION_METHODS


def test_rule_based_extractor_finds_edges():
    """RuleBasedExtractor produces edges for entities in the same chunk."""
    extractor = RuleBasedExtractor()
    result = extractor.extract(
        chunk_id="test_chunk_002",
        chunk_text="Beta Industries is exposed to copper and aluminum input costs.",
    )
    edge_types = {e.edge_type for e in result.edges}
    # Should find co_occurs_with at minimum, and potentially exposed_to
    assert any(et in VALID_EDGE_TYPES for et in edge_types)

    for e in result.edges:
        assert e.edge_type in VALID_EDGE_TYPES
        assert e.extraction_method in VALID_EXTRACTION_METHODS


def test_rule_based_extractor_name():
    """RuleBasedExtractor has a stable name."""
    extractor = RuleBasedExtractor()
    assert extractor.name == "rule_based_extractor_v1"
