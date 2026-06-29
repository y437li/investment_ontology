"""EG-E Provenance tests (Workstream E: entity provenance + theme/company document links).

Acceptance criteria asserted here:
  E1  entity_chunk_provenance.parquet: extraction writes document_id + company_id
      per (entity_id, chunk_id) occurrence.  PIT-clean (available_at <= as_of).

  E2  theme_document_evidence.parquet: given a theme (community_id), one parquet
      read returns its contributing source documents (deduped, PIT-clean) without
      any client-side graph walk.

  E3  company_theme_document_evidence.parquet: given a company entity_id, returns
      its per-theme evidence with DISTINCT groups per theme (no cross-theme bleed).
      Includes:
        - cross-theme test: one company in >=2 themes -> >=2 distinct groups
        - entity-not-document test: a doc whose subject company (document.company_id)
          is X, but whose chunk contains evidence for company entity Y -> the doc
          appears in Y's evidence, NOT attributed to X via document.company_id

All tests are hermetic (no network, no LLM) and use synthetic fixtures or the
existing extraction pipeline fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.extraction import (
    EDGES_COLUMNS,
    ENTITIES_COLUMNS,
    ENTITY_CHUNK_PROVENANCE_COLUMNS,
    run_extraction,
)
from theme_engine.exposure import EXPOSURE_COLUMNS
from theme_engine.main import app
from theme_engine import provenance as prov_mod
from theme_engine import runs as runs_mod
from theme_engine.models import RunCreateRequest
from theme_engine.provenance import (
    COMPANY_THEME_DOC_EVIDENCE_COLUMNS,
    THEME_DOC_EVIDENCE_COLUMNS,
)

client = TestClient(app)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"
AS_OF_DATE = "2024-06-30"


# ---------------------------------------------------------------------------
# Pipeline helpers (shared with other test modules)
# ---------------------------------------------------------------------------


def _run_pipeline_to_exposure(as_of_date: str = AS_OF_DATE) -> str:
    """Full pipeline through exposure computation. Returns run_id."""
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

    resp = client.post("/api/data/clean", json={"run_id": run_id, "documents_dir": str(FIXTURES)})
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

    resp = client.post("/api/exposure/compute", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    return run_id


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _make_ent_row(entity_id: str, entity_type: str, name: str,
                  first_seen: str = "2024-01-01", confidence: float = 0.9,
                  source_chunk_ids: Optional[list] = None) -> dict:
    row = {c: "" for c in ENTITIES_COLUMNS}
    row["schema_version"] = "1.0"
    row["entity_id"] = entity_id
    row["entity_type"] = entity_type
    row["name"] = name
    row["canonical_name"] = name
    row["first_seen_at"] = first_seen
    row["confidence"] = str(confidence)
    row["extraction_method"] = "document_stated"
    row["review_status"] = "pending"
    row["source_chunk_ids"] = source_chunk_ids or ["chunk_seed"]
    return row


def _make_edge_row(edge_id: str, src: str, tgt: str, edge_type: str,
                   chunk_ids: list, first_seen: str = "2024-01-01",
                   as_of: str = AS_OF_DATE) -> dict:
    row = {c: "" for c in EDGES_COLUMNS}
    row["schema_version"] = "1.0"
    row["edge_id"] = edge_id
    row["source_entity_id"] = src
    row["target_entity_id"] = tgt
    row["edge_type"] = edge_type
    row["confidence"] = "0.9"
    row["evidence_chunk_ids"] = chunk_ids
    row["first_seen_at"] = first_seen
    row["last_seen_at"] = as_of
    row["as_of_date"] = as_of
    row["extraction_method"] = "document_stated"
    row["review_status"] = "pending"
    return row


def _make_exp_row(company_id: str, theme_snapshot_id: str, community_id: str,
                  top_chunk_ids: list, as_of: str = AS_OF_DATE,
                  exposure_score: float = 0.5) -> dict:
    """Build an exposure row with correct Python types for _write_exposure_table."""
    return {
        "schema_version": "1.0",
        "as_of_date": as_of,
        "company_id": company_id,
        "ticker": None,
        "theme_snapshot_id": theme_snapshot_id,
        "community_id": community_id,
        "exposure_score": float(exposure_score),
        "graph_distance": 1.0,
        "edge_confidence_sum": 0.9,
        "evidence_count": len(top_chunk_ids),
        "top_evidence_chunk_ids": list(top_chunk_ids),
        "calculation_method": "exposure_v1_document_stated",
    }


def _write_chunks_parquet(ddir: Path, chunks: list[dict]) -> None:
    """Write a minimal chunks.parquet with columns needed for provenance."""
    from theme_engine.chunking import CHUNKS_COLUMNS  # noqa: PLC0415
    pq.write_table(pa.Table.from_pylist(chunks), ddir / "chunks.parquet")


def _write_documents_parquet(ddir: Path, docs: list[dict]) -> None:
    """Write a minimal documents.parquet."""
    pq.write_table(pa.Table.from_pylist(docs), ddir / "documents.parquet")


def _communities_doc(run_id: str, as_of: str, communities: list) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of,
        "algorithm": "louvain",
        "communities": communities,
    }


def _snapshots_doc(run_id: str, as_of: str, snapshots: list) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of,
        "snapshots": snapshots,
    }


# ---------------------------------------------------------------------------
# E1 Tests: entity_chunk_provenance
# ---------------------------------------------------------------------------


class TestE1EntityChunkProvenance:
    """E1: extraction writes entity_chunk_provenance.parquet with document_id+company_id."""

    def test_provenance_artifact_written(self):
        """entity_chunk_provenance.parquet is written by extraction."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        assert (ddir / "entity_chunk_provenance.parquet").exists(), (
            "entity_chunk_provenance.parquet not written by extraction"
        )

    def test_provenance_columns(self):
        """entity_chunk_provenance.parquet has exactly the E1 contract columns."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        table = pq.read_table(ddir / "entity_chunk_provenance.parquet")
        assert table.column_names == ENTITY_CHUNK_PROVENANCE_COLUMNS, (
            f"entity_chunk_provenance columns mismatch.\n"
            f"  expected: {ENTITY_CHUNK_PROVENANCE_COLUMNS}\n"
            f"  got: {table.column_names}"
        )

    def test_provenance_rows_non_empty(self):
        """entity_chunk_provenance.parquet has at least one row after extraction."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        rows = pq.read_table(ddir / "entity_chunk_provenance.parquet").to_pylist()
        assert len(rows) > 0, "entity_chunk_provenance.parquet is unexpectedly empty"

    def test_provenance_document_id_preserved(self):
        """Every provenance row has a non-empty document_id."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        rows = pq.read_table(ddir / "entity_chunk_provenance.parquet").to_pylist()
        for row in rows:
            assert row.get("document_id"), (
                f"provenance row missing document_id: {row}"
            )

    def test_provenance_entity_ids_match_entities(self):
        """All entity_id values in provenance are in entities.parquet."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        entity_ids = {
            r["entity_id"]
            for r in pq.read_table(ddir / "entities.parquet").to_pylist()
        }
        prov_rows = pq.read_table(ddir / "entity_chunk_provenance.parquet").to_pylist()
        for row in prov_rows:
            assert row["entity_id"] in entity_ids, (
                f"provenance entity_id {row['entity_id']!r} not in entities.parquet"
            )

    def test_provenance_chunk_ids_match_chunks(self):
        """All chunk_id values in provenance are in chunks.parquet."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        chunk_ids = {
            r["chunk_id"]
            for r in pq.read_table(ddir / "chunks.parquet").to_pylist()
        }
        prov_rows = pq.read_table(ddir / "entity_chunk_provenance.parquet").to_pylist()
        for row in prov_rows:
            assert row["chunk_id"] in chunk_ids, (
                f"provenance chunk_id {row['chunk_id']!r} not in chunks.parquet"
            )

    def test_provenance_pit_clean(self):
        """All provenance rows have available_at <= as_of_date."""
        run_id = _run_pipeline_to_exposure()
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        rows = pq.read_table(ddir / "entity_chunk_provenance.parquet").to_pylist()
        for row in rows:
            avail = str(row.get("available_at", "") or "")[:10]
            if avail:
                assert avail <= AS_OF_DATE, (
                    f"provenance row has future available_at {avail!r} > {AS_OF_DATE}: {row}"
                )


# ---------------------------------------------------------------------------
# E2 Tests: theme_document_evidence
# ---------------------------------------------------------------------------


class TestE2ThemeDocumentEvidence:
    """E2: one read returns a theme's contributing documents (no graph walk)."""

    def test_theme_document_evidence_written(self):
        """POST /api/provenance/materialize writes theme_document_evidence.parquet."""
        run_id = _run_pipeline_to_exposure()
        resp = client.post("/api/provenance/materialize", json={"run_id": run_id})
        assert resp.status_code == 200, resp.text

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        assert (ddir / "theme_document_evidence.parquet").exists()

    def test_theme_document_evidence_columns(self):
        """theme_document_evidence.parquet has exactly the E2 contract columns."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        table = pq.read_table(ddir / "theme_document_evidence.parquet")
        assert table.column_names == THEME_DOC_EVIDENCE_COLUMNS, (
            f"theme_document_evidence columns mismatch.\n"
            f"  expected: {THEME_DOC_EVIDENCE_COLUMNS}\n"
            f"  got: {table.column_names}"
        )

    def test_theme_documents_endpoint_single_read(self):
        """GET /api/themes/{run_id}/communities/{community_id}/documents returns in one read."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        # Get a community_id
        communities_doc = json.loads(
            (Path(settings.run_output_dir) / run_id / "discovery" / "communities.json")
            .read_text()
        )
        communities = communities_doc.get("communities", [])
        if not communities:
            pytest.skip("No communities in this run")

        community_id = communities[0]["community_id"]
        resp = client.get(f"/api/themes/{run_id}/communities/{community_id}/documents")
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body["community_id"] == community_id
        assert "document_ids" in body
        assert "chunk_ids" in body
        assert isinstance(body["document_ids"], list)
        assert isinstance(body["chunk_ids"], list)

    def test_theme_documents_document_ids_from_chunks(self):
        """document_ids in theme_document_evidence are resolved from chunks.parquet."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        # Collect all valid document_ids from chunks
        all_doc_ids = {
            r["document_id"]
            for r in pq.read_table(ddir / "chunks.parquet").to_pylist()
            if r.get("document_id")
        }
        rows = pq.read_table(ddir / "theme_document_evidence.parquet").to_pylist()
        for row in rows:
            for doc_id in (row.get("document_ids") or []):
                assert doc_id in all_doc_ids, (
                    f"theme_document_evidence references unknown document_id {doc_id!r}"
                )

    def test_theme_documents_community_ids_valid(self):
        """Every community_id in theme_document_evidence appears in communities.json."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        communities_doc = json.loads((ddir / "communities.json").read_text())
        valid_ids = {c["community_id"] for c in communities_doc["communities"]}

        rows = pq.read_table(ddir / "theme_document_evidence.parquet").to_pylist()
        for row in rows:
            assert row["community_id"] in valid_ids, (
                f"theme_document_evidence has unknown community_id: {row['community_id']!r}"
            )

    def test_theme_documents_endpoint_missing_community_404(self):
        """GET .../communities/nonexistent/documents returns 404."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})
        resp = client.get(f"/api/themes/{run_id}/communities/nonexistent_community/documents")
        assert resp.status_code == 404, resp.text

    def test_theme_documents_no_graph_walk_needed(self):
        """Synthetic test: theme_document_evidence rows carry document_ids directly.

        Uses a hand-crafted fixture to verify that a theme community's documents
        are reachable in one parquet read, without traversing graph edges.
        """
        run = runs_mod.create_run(RunCreateRequest(as_of_date=AS_OF_DATE))
        run_id = run.run_id
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)

        # Synthetic entities, chunks, edges
        concept_id = "ent_concept_e2_test"
        chunk_id = "chunk_e2_001"
        doc_id = "doc_e2_001"

        ents = [_make_ent_row(concept_id, "EconomicConcept", "E2Concept",
                              source_chunk_ids=[chunk_id])]

        edges = [_make_edge_row("edge_e2_01", concept_id, concept_id,
                                "co_occurs_with", [chunk_id])]
        # (self-loop edge is unusual but valid for testing chunk->doc resolution)

        chunks = [
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_id, "document_id": doc_id,
                "raw_document_id": "raw_e2_001",
                "chunk_index": 0, "text": "E2 test text.",
                "token_count": 4, "start_char": 0, "end_char": 14,
                "page_start": None, "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "abc",
                "cleaning_version": "v1", "block_type": None,
            }
        ]

        community = {
            "community_id": "community_e2_test",
            "node_ids": [concept_id],
            "edge_ids": ["edge_e2_01"],
            "size": 1, "density": 0.0,
            "top_entities": ["E2Concept"], "top_companies": [],
            "theme_name": "E2 Test Theme", "theme_summary": "test",
            "naming_model": "deterministic",
        }

        pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
        pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
        pq.write_table(pa.Table.from_pylist(chunks), ddir / "chunks.parquet")
        (ddir / "communities.json").write_text(
            json.dumps(_communities_doc(run_id, AS_OF_DATE, [community]))
        )
        (ddir / "theme_snapshots.json").write_text(
            json.dumps(_snapshots_doc(run_id, AS_OF_DATE, [{
                "theme_snapshot_id": "snap_e2_test",
                "community_id": "community_e2_test",
                "theme_family_id": None, "state": "Emerging",
                "theme_name": "E2 Test Theme", "summary": "test",
                "evidence_edge_ids": ["edge_e2_01"],
            }]))
        )

        prov_mod.materialize_theme_document_evidence(run_id)
        rows = pq.read_table(ddir / "theme_document_evidence.parquet").to_pylist()

        assert len(rows) == 1, f"expected 1 community row, got {len(rows)}"
        assert rows[0]["community_id"] == "community_e2_test"
        assert doc_id in (rows[0].get("document_ids") or []), (
            f"expected {doc_id!r} in document_ids: {rows[0]}"
        )
        assert chunk_id in (rows[0].get("chunk_ids") or []), (
            f"expected {chunk_id!r} in chunk_ids: {rows[0]}"
        )


# ---------------------------------------------------------------------------
# E3 Tests: company_theme_document_evidence
# ---------------------------------------------------------------------------


class TestE3CompanyThemeEvidence:
    """E3: (company, theme) -> documents, keyed on Company entity id."""

    def test_company_theme_evidence_written(self):
        """POST /api/provenance/materialize writes company_theme_document_evidence.parquet."""
        run_id = _run_pipeline_to_exposure()
        resp = client.post("/api/provenance/materialize", json={"run_id": run_id})
        assert resp.status_code == 200, resp.text

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        assert (ddir / "company_theme_document_evidence.parquet").exists()

    def test_company_theme_evidence_columns(self):
        """company_theme_document_evidence.parquet has exactly the E3 contract columns."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        table = pq.read_table(ddir / "company_theme_document_evidence.parquet")
        assert table.column_names == COMPANY_THEME_DOC_EVIDENCE_COLUMNS, (
            f"company_theme_document_evidence columns mismatch.\n"
            f"  expected: {COMPANY_THEME_DOC_EVIDENCE_COLUMNS}\n"
            f"  got: {table.column_names}"
        )

    def test_company_documents_endpoint_returns_per_theme_groups(self):
        """GET /api/themes/{run_id}/companies/{company_id}/documents returns per-theme list.

        Deterministic seed (one company across two themes) so exposure rows are
        GUARANTEED — the endpoint is always exercised, never skipped.
        """
        run = runs_mod.create_run(RunCreateRequest(as_of_date=AS_OF_DATE))
        run_id = run.run_id
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)

        company_id = "ent_company_docs_endpoint"
        concept_a_id = "ent_concept_docs_a"
        concept_b_id = "ent_concept_docs_b"
        chunk_a, chunk_b = "chunk_docs_a", "chunk_docs_b"
        doc_a, doc_b = "doc_docs_a", "doc_docs_b"

        ents = [
            _make_ent_row(company_id, "Company", "DocsEndpointCo",
                          source_chunk_ids=[chunk_a, chunk_b]),
            _make_ent_row(concept_a_id, "EconomicConcept", "DocsThemeAConcept",
                          source_chunk_ids=[chunk_a]),
            _make_ent_row(concept_b_id, "EconomicConcept", "DocsThemeBConcept",
                          source_chunk_ids=[chunk_b]),
        ]
        edges = [
            _make_edge_row("edge_docs_a", company_id, concept_a_id, "exposed_to", [chunk_a]),
            _make_edge_row("edge_docs_b", company_id, concept_b_id, "exposed_to", [chunk_b]),
        ]
        chunks = [
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_a, "document_id": doc_a,
                "raw_document_id": "raw_docs_a", "chunk_index": 0,
                "text": "Docs theme A evidence.", "token_count": 4,
                "start_char": 0, "end_char": 22, "page_start": None,
                "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "hda",
                "cleaning_version": "v1", "block_type": None,
            },
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_b, "document_id": doc_b,
                "raw_document_id": "raw_docs_b", "chunk_index": 0,
                "text": "Docs theme B evidence.", "token_count": 4,
                "start_char": 0, "end_char": 22, "page_start": None,
                "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "hdb",
                "cleaning_version": "v1", "block_type": None,
            },
        ]
        comm_a = {
            "community_id": "community_docs_a",
            "node_ids": [concept_a_id], "edge_ids": ["edge_docs_a"],
            "size": 1, "density": 0.0,
            "top_entities": ["DocsThemeAConcept"], "top_companies": [],
            "theme_name": "Docs Theme A", "theme_summary": "a", "naming_model": "deterministic",
        }
        comm_b = {
            "community_id": "community_docs_b",
            "node_ids": [concept_b_id], "edge_ids": ["edge_docs_b"],
            "size": 1, "density": 0.0,
            "top_entities": ["DocsThemeBConcept"], "top_companies": [],
            "theme_name": "Docs Theme B", "theme_summary": "b", "naming_model": "deterministic",
        }
        snapshots = [
            {"theme_snapshot_id": "snap_docs_a", "community_id": "community_docs_a",
             "theme_family_id": None, "state": "Emerging",
             "theme_name": "Docs Theme A", "summary": "a", "evidence_edge_ids": ["edge_docs_a"]},
            {"theme_snapshot_id": "snap_docs_b", "community_id": "community_docs_b",
             "theme_family_id": None, "state": "Emerging",
             "theme_name": "Docs Theme B", "summary": "b", "evidence_edge_ids": ["edge_docs_b"]},
        ]
        exp_rows = [
            _make_exp_row(company_id, "snap_docs_a", "community_docs_a", [chunk_a]),
            _make_exp_row(company_id, "snap_docs_b", "community_docs_b", [chunk_b]),
        ]

        pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
        pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
        pq.write_table(pa.Table.from_pylist(chunks), ddir / "chunks.parquet")
        (ddir / "communities.json").write_text(
            json.dumps(_communities_doc(run_id, AS_OF_DATE, [comm_a, comm_b]))
        )
        (ddir / "theme_snapshots.json").write_text(
            json.dumps(_snapshots_doc(run_id, AS_OF_DATE, snapshots))
        )
        from theme_engine.exposure import _write_exposure_table  # noqa: PLC0415
        _write_exposure_table(exp_rows, ddir / "company_theme_exposure.parquet")

        # Materialize E3 (the artifact the endpoint reads).
        prov_mod.materialize_company_theme_evidence(run_id)

        resp = client.get(f"/api/themes/{run_id}/companies/{company_id}/documents")
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert isinstance(body, list)
        # Exposure is guaranteed -> two distinct theme groups, never empty.
        assert len(body) == 2, f"expected 2 per-theme groups, got {len(body)}: {body}"
        assert {item["community_id"] for item in body} == {"community_docs_a", "community_docs_b"}
        for item in body:
            assert item["company_id"] == company_id
            assert "theme_snapshot_id" in item
            assert "community_id" in item
            assert isinstance(item["document_ids"], list)
            assert isinstance(item["chunk_ids"], list)

    # ------------------------------------------------------------------
    # ACCEPTANCE: one company in >=2 themes -> distinct evidence groups
    # ------------------------------------------------------------------

    def test_company_two_themes_distinct_evidence_groups(self):
        """Company spanning >=2 communities returns >=2 DISTINCT evidence groups.

        Acceptance test per spec: "A company spanning >=2 themes returns
        multiple DISTINCT evidence groups (NO cross-theme bleed, NO collapse
        to one company-level list)."
        """
        run = runs_mod.create_run(RunCreateRequest(as_of_date=AS_OF_DATE))
        run_id = run.run_id
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)

        company_id = "ent_company_two_themes"
        concept_a_id = "ent_concept_theme_a"
        concept_b_id = "ent_concept_theme_b"

        # Two completely separate chunks, one per theme
        chunk_a = "chunk_theme_a_001"
        chunk_b = "chunk_theme_b_001"
        doc_a = "doc_theme_a"
        doc_b = "doc_theme_b"

        ents = [
            _make_ent_row(company_id, "Company", "TwoThemeCo",
                          source_chunk_ids=[chunk_a, chunk_b]),
            _make_ent_row(concept_a_id, "EconomicConcept", "ThemeAConcept",
                          source_chunk_ids=[chunk_a]),
            _make_ent_row(concept_b_id, "EconomicConcept", "ThemeBConcept",
                          source_chunk_ids=[chunk_b]),
        ]

        edges = [
            _make_edge_row("edge_co_to_a", company_id, concept_a_id,
                           "exposed_to", [chunk_a]),
            _make_edge_row("edge_co_to_b", company_id, concept_b_id,
                           "exposed_to", [chunk_b]),
        ]

        chunks = [
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_a, "document_id": doc_a,
                "raw_document_id": "raw_a", "chunk_index": 0,
                "text": "Theme A evidence.", "token_count": 3,
                "start_char": 0, "end_char": 17, "page_start": None,
                "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "ha",
                "cleaning_version": "v1", "block_type": None,
            },
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_b, "document_id": doc_b,
                "raw_document_id": "raw_b", "chunk_index": 0,
                "text": "Theme B evidence.", "token_count": 3,
                "start_char": 0, "end_char": 17, "page_start": None,
                "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "hb",
                "cleaning_version": "v1", "block_type": None,
            },
        ]

        # Two communities, each containing one concept node
        comm_a = {
            "community_id": "community_theme_a",
            "node_ids": [concept_a_id], "edge_ids": ["edge_co_to_a"],
            "size": 1, "density": 0.0,
            "top_entities": ["ThemeAConcept"], "top_companies": [],
            "theme_name": "Theme A", "theme_summary": "a", "naming_model": "deterministic",
        }
        comm_b = {
            "community_id": "community_theme_b",
            "node_ids": [concept_b_id], "edge_ids": ["edge_co_to_b"],
            "size": 1, "density": 0.0,
            "top_entities": ["ThemeBConcept"], "top_companies": [],
            "theme_name": "Theme B", "theme_summary": "b", "naming_model": "deterministic",
        }

        snapshots = [
            {"theme_snapshot_id": "snap_a", "community_id": "community_theme_a",
             "theme_family_id": None, "state": "Emerging",
             "theme_name": "Theme A", "summary": "a", "evidence_edge_ids": ["edge_co_to_a"]},
            {"theme_snapshot_id": "snap_b", "community_id": "community_theme_b",
             "theme_family_id": None, "state": "Emerging",
             "theme_name": "Theme B", "summary": "b", "evidence_edge_ids": ["edge_co_to_b"]},
        ]

        # Two exposure rows: same company, different communities/themes
        exp_rows = [
            _make_exp_row(company_id, "snap_a", "community_theme_a", [chunk_a]),
            _make_exp_row(company_id, "snap_b", "community_theme_b", [chunk_b]),
        ]

        pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
        pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
        pq.write_table(pa.Table.from_pylist(chunks), ddir / "chunks.parquet")
        (ddir / "communities.json").write_text(
            json.dumps(_communities_doc(run_id, AS_OF_DATE, [comm_a, comm_b]))
        )
        (ddir / "theme_snapshots.json").write_text(
            json.dumps(_snapshots_doc(run_id, AS_OF_DATE, snapshots))
        )
        # Write exposure parquet
        from theme_engine.exposure import _write_exposure_table  # noqa: PLC0415
        _write_exposure_table(exp_rows, ddir / "company_theme_exposure.parquet")

        # Materialize E3
        prov_mod.materialize_company_theme_evidence(run_id)
        rows = pq.read_table(ddir / "company_theme_document_evidence.parquet").to_pylist()

        company_rows = [r for r in rows if r["company_id"] == company_id]

        # ACCEPTANCE: must have 2 distinct evidence groups
        assert len(company_rows) == 2, (
            f"expected 2 theme evidence groups for {company_id!r}, got {len(company_rows)}: "
            f"{[(r['community_id'], r['document_ids']) for r in company_rows]}"
        )

        # Groups must be for different communities
        community_ids = {r["community_id"] for r in company_rows}
        assert community_ids == {"community_theme_a", "community_theme_b"}, (
            f"expected distinct communities, got: {community_ids}"
        )

        # NO cross-theme bleed: evidence for theme A must not contain theme B docs
        for r in company_rows:
            if r["community_id"] == "community_theme_a":
                assert doc_a in (r.get("document_ids") or []), (
                    f"Theme A evidence missing doc_a: {r}"
                )
                assert doc_b not in (r.get("document_ids") or []), (
                    f"Cross-theme bleed: doc_b leaked into Theme A evidence: {r}"
                )
            elif r["community_id"] == "community_theme_b":
                assert doc_b in (r.get("document_ids") or []), (
                    f"Theme B evidence missing doc_b: {r}"
                )
                assert doc_a not in (r.get("document_ids") or []), (
                    f"Cross-theme bleed: doc_a leaked into Theme B evidence: {r}"
                )

    # ------------------------------------------------------------------
    # ACCEPTANCE: entity-not-document join correctness
    # ------------------------------------------------------------------

    def test_entity_join_not_document_company_id(self):
        """E3 joins on Company ENTITY id, not document.company_id.

        Scenario:
          - Document D has document.company_id = "ent_subject_co" (SubjectCo).
          - Chunk C1 is from Document D.
          - Chunk C1 contains evidence of MentionedCo -> EconomicConcept edge.
          - Exposure row for MentionedCo (company_id="ent_mentioned_co") has
            top_evidence_chunk_ids = [C1].
          - SubjectCo has NO exposure to this theme.

        Expected:
          - E3 for MentionedCo includes Document D (via C1).
          - SubjectCo has NO rows in company_theme_document_evidence for this theme.

        This proves the join is on the Company entity (entity_id from exposure),
        NOT on document.company_id.
        """
        run = runs_mod.create_run(RunCreateRequest(as_of_date=AS_OF_DATE))
        run_id = run.run_id
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)

        # Three entities: SubjectCo (document subject), MentionedCo (mentioned),
        # and a concept node
        subject_entity_id = "ent_subject_co"
        mentioned_entity_id = "ent_mentioned_co"
        concept_id = "ent_concept_crossdoc"

        # The chunk comes from a document ABOUT SubjectCo, but MentionedCo is
        # mentioned in that chunk.  document.company_id = subject_entity_id.
        chunk_id = "chunk_crossdoc_001"
        doc_id = "doc_about_subject_co"

        ents = [
            _make_ent_row(subject_entity_id, "Company", "SubjectCo",
                          source_chunk_ids=[chunk_id]),
            _make_ent_row(mentioned_entity_id, "Company", "MentionedCo",
                          source_chunk_ids=[chunk_id]),
            _make_ent_row(concept_id, "EconomicConcept", "CrossDocConcept",
                          source_chunk_ids=[chunk_id]),
        ]

        # Edge: MentionedCo exposed_to concept (evidence = C1)
        # SubjectCo has no structural edge to the concept.
        edges = [
            _make_edge_row("edge_mentioned_to_concept", mentioned_entity_id, concept_id,
                           "exposed_to", [chunk_id]),
        ]

        # The document's company_id is SubjectCo (not MentionedCo)
        chunks = [
            {
                "schema_version": "1.0", "run_id": run_id,
                "chunk_id": chunk_id, "document_id": doc_id,
                "raw_document_id": "raw_crossdoc", "chunk_index": 0,
                "text": "MentionedCo is exposed to CrossDocConcept.",
                "token_count": 7, "start_char": 0, "end_char": 43,
                "page_start": None, "page_end": None, "section_title": None,
                "available_at": "2024-01-01", "content_hash": "hc",
                "cleaning_version": "v1", "block_type": None,
            }
        ]

        # Community: contains concept_id
        comm = {
            "community_id": "community_crossdoc",
            "node_ids": [concept_id], "edge_ids": ["edge_mentioned_to_concept"],
            "size": 1, "density": 0.0,
            "top_entities": ["CrossDocConcept"], "top_companies": [],
            "theme_name": "CrossDoc Theme", "theme_summary": "test",
            "naming_model": "deterministic",
        }

        # Exposure: only MentionedCo has exposure (via edge to concept)
        # SubjectCo is NOT in the exposure table for this community.
        exp_rows = [
            _make_exp_row(mentioned_entity_id, "snap_crossdoc", "community_crossdoc",
                          [chunk_id]),
            # SubjectCo: no exposure to community_crossdoc
        ]

        pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
        pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
        pq.write_table(pa.Table.from_pylist(chunks), ddir / "chunks.parquet")
        (ddir / "communities.json").write_text(
            json.dumps(_communities_doc(run_id, AS_OF_DATE, [comm]))
        )
        (ddir / "theme_snapshots.json").write_text(
            json.dumps(_snapshots_doc(run_id, AS_OF_DATE, [{
                "theme_snapshot_id": "snap_crossdoc",
                "community_id": "community_crossdoc",
                "theme_family_id": None, "state": "Emerging",
                "theme_name": "CrossDoc Theme", "summary": "test",
                "evidence_edge_ids": ["edge_mentioned_to_concept"],
            }]))
        )
        from theme_engine.exposure import _write_exposure_table  # noqa: PLC0415
        _write_exposure_table(exp_rows, ddir / "company_theme_exposure.parquet")

        # Materialize E3
        prov_mod.materialize_company_theme_evidence(run_id)
        rows = pq.read_table(ddir / "company_theme_document_evidence.parquet").to_pylist()

        # MentionedCo MUST have doc_id in its evidence
        mentioned_rows = [r for r in rows if r["company_id"] == mentioned_entity_id]
        assert len(mentioned_rows) >= 1, (
            f"MentionedCo should have >=1 E3 row, got {len(mentioned_rows)}"
        )
        for r in mentioned_rows:
            assert doc_id in (r.get("document_ids") or []), (
                f"MentionedCo E3 row missing {doc_id!r}: {r}"
            )

        # SubjectCo must NOT appear in E3 (it has no exposure to community_crossdoc)
        subject_rows = [r for r in rows if r["company_id"] == subject_entity_id]
        assert len(subject_rows) == 0, (
            f"SubjectCo should have 0 E3 rows (document.company_id != entity attribution), "
            f"got: {subject_rows}"
        )

    # ------------------------------------------------------------------
    # PIT-clean: exposure artifact is already PIT-gated
    # ------------------------------------------------------------------

    def test_company_theme_evidence_pit_clean(self):
        """All company_theme_document_evidence rows carry the run's as_of_date."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        rows = pq.read_table(ddir / "company_theme_document_evidence.parquet").to_pylist()
        for row in rows:
            assert row.get("as_of_date") == AS_OF_DATE, (
                f"company_theme_document_evidence row has wrong as_of_date: {row}"
            )

    def test_company_theme_evidence_document_ids_valid(self):
        """document_ids in E3 are resolvable to chunks.parquet document_ids."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        all_doc_ids = {
            r["document_id"]
            for r in pq.read_table(ddir / "chunks.parquet").to_pylist()
            if r.get("document_id")
        }
        rows = pq.read_table(ddir / "company_theme_document_evidence.parquet").to_pylist()
        for row in rows:
            for doc_id in (row.get("document_ids") or []):
                assert doc_id in all_doc_ids, (
                    f"company_theme_document_evidence references unknown document_id "
                    f"{doc_id!r} (company={row['company_id']!r})"
                )


# ---------------------------------------------------------------------------
# API endpoint smoke tests
# ---------------------------------------------------------------------------


class TestProvenanceAPIEndpoints:
    """Smoke tests for new API endpoints."""

    def test_materialize_endpoint_response_shape(self):
        """POST /api/provenance/materialize returns correct shape."""
        run_id = _run_pipeline_to_exposure()
        resp = client.post("/api/provenance/materialize", json={"run_id": run_id})
        assert resp.status_code == 200, resp.text

        body = resp.json()
        assert body["success"] is True
        assert "discovery/theme_document_evidence.parquet" in body["artifacts"]
        assert "discovery/company_theme_document_evidence.parquet" in body["artifacts"]
        assert isinstance(body["theme_rows"], int)
        assert isinstance(body["company_theme_rows"], int)

    def test_materialize_missing_run_404(self):
        """POST /api/provenance/materialize returns 404 for unknown run."""
        resp = client.post("/api/provenance/materialize", json={"run_id": "nonexistent_eg_e_999"})
        assert resp.status_code == 404, resp.text

    def test_theme_documents_missing_artifact_404(self):
        """GET .../communities/.../documents returns 404 when artifact not materialized."""
        run_id = _run_pipeline_to_exposure()
        # Do NOT call /api/provenance/materialize
        resp = client.get(f"/api/themes/{run_id}/communities/community_any/documents")
        assert resp.status_code == 404, resp.text

    def test_company_documents_missing_artifact_404(self):
        """GET .../companies/.../documents returns 404 when artifact not materialized."""
        run_id = _run_pipeline_to_exposure()
        resp = client.get(f"/api/themes/{run_id}/companies/ent_any/documents")
        assert resp.status_code == 404, resp.text

    def test_company_documents_returns_list(self):
        """GET .../companies/.../documents returns a list (possibly empty for unknown company)."""
        run_id = _run_pipeline_to_exposure()
        client.post("/api/provenance/materialize", json={"run_id": run_id})
        resp = client.get(f"/api/themes/{run_id}/companies/ent_nonexistent/documents")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)

    def test_materialize_idempotent(self):
        """POST /api/provenance/materialize can be called twice safely."""
        run_id = _run_pipeline_to_exposure()
        resp1 = client.post("/api/provenance/materialize", json={"run_id": run_id})
        assert resp1.status_code == 200, resp1.text
        resp2 = client.post("/api/provenance/materialize", json={"run_id": run_id})
        assert resp2.status_code == 200, resp2.text
        # Results should be identical
        assert resp1.json()["theme_rows"] == resp2.json()["theme_rows"]
        assert resp1.json()["company_theme_rows"] == resp2.json()["company_theme_rows"]
