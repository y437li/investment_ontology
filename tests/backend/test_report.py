"""M7 report generation tests (io_contracts §23, spec §9.9).

Asserts:
  (a) report.md written and non-empty after POST /api/report/generate.
  (b) Report references real community/theme/company IDs that exist in artifacts
      (traceability).
  (c) Report contains NO unsupported-claim phrasing (no 'will outperform',
      'guaranteed', 'buy', 'sell', 'proven alpha').
  (d) Carries the single-snapshot / illustrative caveat when validation is
      absent OR when validation.csv contains the illustrative caveat.
  (e) Deterministic: generate twice -> identical bytes.
  (f) API response shape matches io_contracts §24.
  (g) Missing optional validation artifact -> graceful note (not 500 error).
  (h) Missing run -> 404.
  (i) Report section headers match io_contracts §23 required sections.

No network or LLM calls. DeterministicNarrator used throughout.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app
from theme_engine import runs
from theme_engine.models import RunCreateRequest
from theme_engine.report import (
    DeterministicNarrator,
    generate_report,
    _ILLUSTRATIVE_CAVEAT,
    _VALIDATION_ABSENT_NOTE,
    _FORBIDDEN_PHRASES,
)

client = TestClient(app)

AS_OF_DATE = "2024-06-30"

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_run() -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": AS_OF_DATE})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _seed_full_artifacts(
    run_id: str,
    *,
    include_validation: bool = True,
    include_exposure: bool = True,
    include_communities: bool = True,
) -> None:
    """Seed a run directory with minimal but complete artifacts for report generation.

    All artifacts are synthetic / deterministic.
    No network or LLM calls.
    """
    run_dir = Path(settings.run_output_dir) / run_id
    ddir = run_dir / "discovery"
    vdir = run_dir / "validation"
    ddir.mkdir(parents=True, exist_ok=True)
    vdir.mkdir(parents=True, exist_ok=True)

    company_id = "ent_company_report_test"
    concept_id = "ent_concept_report_test"
    community_id = "community_report_001"
    theme_snapshot_id = f"theme_{AS_OF_DATE}_{community_id}"

    # ------- communities.json -------
    if include_communities:
        communities_doc = {
            "schema_version": "1.0",
            "run_id": run_id,
            "as_of_date": AS_OF_DATE,
            "algorithm": "louvain",
            "communities": [
                {
                    "community_id": community_id,
                    "node_ids": [company_id, concept_id],
                    "edge_ids": ["edge_report_1"],
                    "size": 2,
                    "density": 0.5,
                    "top_entities": ["AITheme"],
                    "top_companies": ["ReportCo"],
                    "theme_name": "AI Infrastructure",
                    "theme_summary": "Community of 2 entities connected by 1 structural edge.",
                    "naming_model": "deterministic",
                }
            ],
        }
        (ddir / "communities.json").write_text(
            json.dumps(communities_doc, indent=2), encoding="utf-8"
        )

        # ------- theme_snapshots.json -------
        snapshots_doc = {
            "schema_version": "1.0",
            "run_id": run_id,
            "as_of_date": AS_OF_DATE,
            "snapshots": [
                {
                    "theme_snapshot_id": theme_snapshot_id,
                    "community_id": community_id,
                    "theme_family_id": None,
                    "state": "Emerging",
                    "theme_name": "AI Infrastructure",
                    "summary": "Community of 2 entities.",
                    "evidence_edge_ids": ["edge_report_1"],
                }
            ],
        }
        (ddir / "theme_snapshots.json").write_text(
            json.dumps(snapshots_doc, indent=2), encoding="utf-8"
        )

        # ------- theme_metrics.parquet -------
        metrics_rows = [
            {
                "schema_version": "1.0",
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "as_of_date": AS_OF_DATE,
                "strength": 0.85,
                "momentum": None,
                "birth_score": None,
                "cohesion": 0.5,
                "novelty": None,
                "saturation": 0.04,
                "macro_linkage": None,
                "commodity_linkage": None,
            }
        ]
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("theme_snapshot_id", pa.string()),
            ("community_id", pa.string()),
            ("as_of_date", pa.string()),
            ("strength", pa.float64()),
            ("momentum", pa.float64()),
            ("birth_score", pa.float64()),
            ("cohesion", pa.float64()),
            ("novelty", pa.float64()),
            ("saturation", pa.float64()),
            ("macro_linkage", pa.float64()),
            ("commodity_linkage", pa.float64()),
        ])
        tbl = pa.table(
            {
                "schema_version": ["1.0"],
                "theme_snapshot_id": [theme_snapshot_id],
                "community_id": [community_id],
                "as_of_date": [AS_OF_DATE],
                "strength": pa.array([0.85], type=pa.float64()),
                "momentum": pa.array([None], type=pa.float64()),
                "birth_score": pa.array([None], type=pa.float64()),
                "cohesion": pa.array([0.5], type=pa.float64()),
                "novelty": pa.array([None], type=pa.float64()),
                "saturation": pa.array([0.04], type=pa.float64()),
                "macro_linkage": pa.array([None], type=pa.float64()),
                "commodity_linkage": pa.array([None], type=pa.float64()),
            },
            schema=schema,
        )
        pq.write_table(tbl, ddir / "theme_metrics.parquet")

    # ------- company_theme_exposure.parquet -------
    if include_exposure:
        exp_schema = pa.schema([
            ("schema_version", pa.string()),
            ("as_of_date", pa.string()),
            ("company_id", pa.string()),
            ("ticker", pa.string()),
            ("theme_snapshot_id", pa.string()),
            ("community_id", pa.string()),
            ("exposure_score", pa.float64()),
            ("graph_distance", pa.float64()),
            ("edge_confidence_sum", pa.float64()),
            ("evidence_count", pa.int64()),
            ("top_evidence_chunk_ids", pa.list_(pa.string())),
            ("calculation_method", pa.string()),
        ])
        exp_tbl = pa.table(
            {
                "schema_version": ["1.0"],
                "as_of_date": [AS_OF_DATE],
                "company_id": [company_id],
                "ticker": ["RPTCO"],
                "theme_snapshot_id": [theme_snapshot_id],
                "community_id": [community_id],
                "exposure_score": pa.array([0.72], type=pa.float64()),
                "graph_distance": pa.array([1.0], type=pa.float64()),
                "edge_confidence_sum": pa.array([0.9], type=pa.float64()),
                "evidence_count": pa.array([1], type=pa.int64()),
                "top_evidence_chunk_ids": pa.array(
                    [["chunk_report_1"]], type=pa.list_(pa.string())
                ),
                "calculation_method": ["exposure_v1_document_stated"],
            },
            schema=exp_schema,
        )
        pq.write_table(exp_tbl, ddir / "company_theme_exposure.parquet")

    # ------- validation/validation.csv (optional) -------
    if include_validation:
        from theme_engine.validation import VALIDATION_CSV_COLUMNS, _SINGLE_SNAPSHOT_CAVEAT

        basket_id = "basket_report_test_001"
        val_rows = [
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "as_of_date": AS_OF_DATE,
                "basket_id": basket_id,
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "theme_name": "AI Infrastructure",
                "forward_window": "1M",
                "portfolio_method": "equal_weight_top_n_exposure",
                "company_count": "1",
                "start_date": "2024-07-01",
                "end_date": "2024-07-31",
                "theme_basket_return": "0.050000",
                "benchmark_name": "equal_weight_universe",
                "benchmark_return": "0.030000",
                "excess_return": "0.020000",
                "sample_size": "1",
                "market_data_source": "test",
                "caveats": _SINGLE_SNAPSHOT_CAVEAT,
            }
        ]
        csv_path = vdir / "validation.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=VALIDATION_CSV_COLUMNS)
            writer.writeheader()
            for row in val_rows:
                writer.writerow({col: row.get(col, "") for col in VALIDATION_CSV_COLUMNS})


def _generate_via_api(run_id: str) -> dict:
    """Call POST /api/report/generate and return the JSON response."""
    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, f"report generate failed: {resp.text}"
    return resp.json()


def _read_report(run_id: str) -> str:
    report_path = Path(settings.run_output_dir) / run_id / "report.md"
    assert report_path.exists(), f"report.md not found at {report_path}"
    return report_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) report.md written and non-empty
# ---------------------------------------------------------------------------


def test_report_written_and_nonempty():
    """POST /api/report/generate writes report.md and it is non-empty."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    body = _generate_via_api(run_id)

    assert body["success"] is True
    assert body["artifact"] == "report.md"
    assert f"data/runs/{run_id}/report.md" == body["report_path"]

    report_text = _read_report(run_id)
    assert len(report_text) > 100, "report.md is too short to be valid"
    assert "# Theme Discovery Report" in report_text


def test_report_written_via_direct_api():
    """generate_report() directly writes report.md."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    path = generate_report(run_id)
    assert path.exists()
    assert path.name == "report.md"
    assert path.read_text(encoding="utf-8").strip() != ""


# ---------------------------------------------------------------------------
# (b) Traceability: report references real artifact IDs
# ---------------------------------------------------------------------------


def test_report_references_real_community_ids():
    """Report contains the real community_id from communities.json."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    # Load actual community_id from communities.json
    communities_doc = json.loads(
        (Path(settings.run_output_dir) / run_id / "discovery" / "communities.json")
        .read_text(encoding="utf-8")
    )
    for community in communities_doc["communities"]:
        cid = community["community_id"]
        assert cid in report_text, (
            f"community_id {cid!r} from communities.json not found in report.md"
        )


def test_report_references_real_theme_snapshot_ids():
    """Report contains real theme_snapshot_id from theme_snapshots.json."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    snapshots_doc = json.loads(
        (Path(settings.run_output_dir) / run_id / "discovery" / "theme_snapshots.json")
        .read_text(encoding="utf-8")
    )
    for snap in snapshots_doc["snapshots"]:
        sid = snap["theme_snapshot_id"]
        assert sid in report_text, (
            f"theme_snapshot_id {sid!r} from theme_snapshots.json not found in report.md"
        )


def test_report_references_real_company_ids():
    """Report contains real company_id from company_theme_exposure.parquet."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    exp_rows = pq.read_table(
        Path(settings.run_output_dir) / run_id / "discovery" / "company_theme_exposure.parquet"
    ).to_pylist()
    for row in exp_rows:
        cid = row["company_id"]
        assert cid in report_text, (
            f"company_id {cid!r} from company_theme_exposure.parquet not found in report.md"
        )


def test_report_references_evidence_chunk_ids():
    """Report references evidence chunk IDs from exposure artifact."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    exp_rows = pq.read_table(
        Path(settings.run_output_dir) / run_id / "discovery" / "company_theme_exposure.parquet"
    ).to_pylist()
    for row in exp_rows:
        for chunk_id in (row.get("top_evidence_chunk_ids") or []):
            assert chunk_id in report_text, (
                f"evidence chunk_id {chunk_id!r} not referenced in report.md"
            )


def test_report_references_validation_basket_ids():
    """Report contains basket_id from validation.csv when validation is present."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_validation=True)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    val_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(val_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            basket_id = row.get("basket_id", "")
            if basket_id:
                assert basket_id in report_text, (
                    f"basket_id {basket_id!r} from validation.csv not found in report.md"
                )


# ---------------------------------------------------------------------------
# (c) No unsupported claim phrasing
# ---------------------------------------------------------------------------


def test_report_no_unsupported_claim_phrases():
    """Report must not contain forbidden investment claim phrases."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id).lower()

    for phrase in _FORBIDDEN_PHRASES:
        assert phrase.lower() not in report_text, (
            f"Forbidden phrase {phrase!r} found in report.md — no unsupported claims allowed"
        )


def test_report_no_hardcoded_investment_advice():
    """Report must not contain explicit investment advice language."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id).lower()

    bad_phrases = [
        "will outperform",
        "guaranteed return",
        "buy this stock",
        "proven alpha",
    ]
    for phrase in bad_phrases:
        assert phrase not in report_text, (
            f"Forbidden phrase {phrase!r} found in report — no investment advice allowed"
        )


# ---------------------------------------------------------------------------
# (d) Single-snapshot / illustrative caveat present
# ---------------------------------------------------------------------------


def test_report_has_illustrative_caveat_when_validation_absent():
    """Report carries the illustrative/single-snapshot caveat when validation.csv is absent."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_validation=False)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    # Must contain the absent-validation note
    assert "validation" in report_text.lower(), "Report missing validation section"
    assert "ILLUSTRATIVE" in report_text or "illustrative" in report_text.lower(), (
        "Report must carry illustrative caveat when validation is absent"
    )
    # Must NOT claim any alpha
    assert "no alpha" in report_text.lower() or "no alpha or causal claim" in report_text.lower(), (
        "Report must explicitly state no alpha claim when validation absent"
    )


def test_report_has_illustrative_caveat_when_validation_present():
    """Report carries the illustrative/single-snapshot caveat even when validation.csv exists."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_validation=True)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    # The illustrative caveat must appear in the validation section
    assert "ILLUSTRATIVE" in report_text or "illustrative" in report_text.lower(), (
        "Report must carry the single-snapshot illustrative caveat"
    )
    assert "single-snapshot" in report_text.lower(), (
        "Report must explicitly state single-snapshot limitation"
    )


def test_report_temporal_metrics_shown_as_unavailable():
    """Report must label temporal metrics (momentum, birth_score, novelty) as N/A."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    # Temporal metrics must be explicitly called out as unavailable
    assert "single-snapshot" in report_text.lower(), (
        "Report must note single-snapshot limitation for temporal metrics"
    )
    # The word "N/A" or "not available" must appear near temporal metric discussion
    assert "N/A" in report_text or "not available" in report_text.lower(), (
        "Temporal metrics must be shown as N/A or not available"
    )


# ---------------------------------------------------------------------------
# (e) Determinism: generate twice -> identical bytes
# ---------------------------------------------------------------------------


def test_report_is_deterministic():
    """Generating the report twice on the same run produces identical bytes."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)

    path1 = generate_report(run_id)
    content1 = path1.read_bytes()

    path2 = generate_report(run_id)
    content2 = path2.read_bytes()

    assert content1 == content2, (
        "Report is not deterministic: two runs produced different report.md bytes"
    )


def test_report_api_deterministic():
    """POST /api/report/generate twice on the same run produces identical report.md."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)

    _generate_via_api(run_id)
    content1 = _read_report(run_id)

    _generate_via_api(run_id)
    content2 = _read_report(run_id)

    assert content1 == content2, (
        "Report not deterministic across two API calls on the same run"
    )


# ---------------------------------------------------------------------------
# (f) API response shape
# ---------------------------------------------------------------------------


def test_report_api_response_shape():
    """POST /api/report/generate returns correct response shape (io_contracts §24)."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)

    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    assert body["artifact"] == "report.md"
    assert body["report_path"] == f"data/runs/{run_id}/report.md"


# ---------------------------------------------------------------------------
# (g) Missing optional artifacts handled gracefully
# ---------------------------------------------------------------------------


def test_report_no_validation_graceful():
    """Report generates successfully even when validation/validation.csv is absent."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_validation=False)

    # Should not raise / return 500
    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    report_text = _read_report(run_id)
    assert "# Theme Discovery Report" in report_text


def test_report_no_exposure_graceful():
    """Report generates successfully even when company_theme_exposure.parquet is absent."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_exposure=False)

    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    report_text = _read_report(run_id)
    assert "# Theme Discovery Report" in report_text


def test_report_no_communities_graceful():
    """Report generates successfully even when communities.json is absent."""
    run_id = _make_run()
    _seed_full_artifacts(run_id, include_communities=False, include_validation=False)

    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    report_text = _read_report(run_id)
    assert "# Theme Discovery Report" in report_text


# ---------------------------------------------------------------------------
# (h) Missing run -> 404
# ---------------------------------------------------------------------------


def test_report_missing_run_returns_404():
    """POST /api/report/generate returns 404 for unknown run_id."""
    resp = client.post("/api/report/generate", json={"run_id": "nonexistent_run_report_999"})
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# (i) Required section headers per io_contracts §23
# ---------------------------------------------------------------------------


def test_report_has_required_section_headers():
    """report.md must contain all required section headers from io_contracts §23."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)
    _generate_via_api(run_id)
    report_text = _read_report(run_id)

    required_sections = [
        "# Theme Discovery Report",
        "## Run Metadata",
        "## Data Coverage",
        "## Emerging Themes",
        "## Accelerating Themes",
        "## Company Exposure",
        "## Validation Results",
        "## Evidence Notes",
        "## Caveats",
    ]
    for section in required_sections:
        assert section in report_text, (
            f"Required section {section!r} missing from report.md (io_contracts §23)"
        )


# ---------------------------------------------------------------------------
# End-to-end: full pipeline -> report
# ---------------------------------------------------------------------------


def test_report_end_to_end_from_full_pipeline():
    """End-to-end: run full discovery pipeline via API, then generate report.

    Uses the real extraction fixture to run the full pipeline (same as
    test_exposure_and_freeze.py helpers). Verifies the report is written.
    """
    from pathlib import Path as _Path
    FIXTURES = _Path(__file__).resolve().parents[1] / "fixtures" / "extraction"

    resp = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Full pipeline
    client.post("/api/data/import", json={
        "run_id": run_id,
        "documents_dir": str(FIXTURES),
        "source_manifest_path": str(FIXTURES / "source_manifest.csv"),
    })
    client.post("/api/data/clean", json={"run_id": run_id, "documents_dir": str(FIXTURES)})
    client.post("/api/data/chunk", json={"run_id": run_id})
    client.post("/api/extraction/run", json={"run_id": run_id})
    client.post("/api/extraction/resolve", json={"run_id": run_id})
    client.post("/api/graph/build", json={"run_id": run_id})
    client.post("/api/themes/discover", json={"run_id": run_id})
    client.post("/api/exposure/compute", json={"run_id": run_id})

    # Generate report — should succeed
    resp = client.post("/api/report/generate", json={"run_id": run_id})
    assert resp.status_code == 200, f"report generate failed: {resp.text}"

    report_text = _read_report(run_id)
    assert "# Theme Discovery Report" in report_text
    assert run_id in report_text
    assert len(report_text) > 200

    # Traceability: communities.json community_ids must appear
    communities_doc = json.loads(
        (_Path(settings.run_output_dir) / run_id / "discovery" / "communities.json")
        .read_text(encoding="utf-8")
    )
    for community in communities_doc["communities"]:
        assert community["community_id"] in report_text, (
            f"community_id {community['community_id']!r} not in report.md"
        )

    # Illustrative caveat must be present (single-snapshot run, no validation.csv)
    assert "ILLUSTRATIVE" in report_text or "illustrative" in report_text.lower()


# ---------------------------------------------------------------------------
# DeterministicNarrator unit tests
# ---------------------------------------------------------------------------


def test_deterministic_narrator_describe_theme():
    """DeterministicNarrator.describe_theme produces stable output."""
    narrator = DeterministicNarrator()
    out1 = narrator.describe_theme("AI Infrastructure", "Summary text.", {"strength": 0.85, "cohesion": 0.5})
    out2 = narrator.describe_theme("AI Infrastructure", "Summary text.", {"strength": 0.85, "cohesion": 0.5})
    assert out1 == out2, "DeterministicNarrator must produce identical output"
    assert "AI Infrastructure" in out1
    assert "0.8500" in out1
    assert "0.5000" in out1


def test_deterministic_narrator_describe_validation():
    """DeterministicNarrator.describe_validation_summary produces stable output."""
    narrator = DeterministicNarrator()
    rows = [{"theme_name": "T1", "forward_window": "1M"}, {"theme_name": "T2", "forward_window": "3M"}]
    out1 = narrator.describe_validation_summary(rows)
    out2 = narrator.describe_validation_summary(rows)
    assert out1 == out2
    assert "2" in out1 or "row" in out1.lower()


def test_narrator_injectable_interface():
    """generate_report accepts an injected DeterministicNarrator without network calls."""
    run_id = _make_run()
    _seed_full_artifacts(run_id)

    custom_narrator = DeterministicNarrator()
    path = generate_report(run_id, narrator=custom_narrator)
    assert path.exists()
    assert "# Theme Discovery Report" in path.read_text(encoding="utf-8")
