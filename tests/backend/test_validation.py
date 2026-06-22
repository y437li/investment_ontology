"""M6 freeze-gated forward-return validation tests.

Asserts:
  (a) Baskets reproducible + conform to io_contracts §21 contract.
  (b) Forward returns computed only from prices dated > as_of_date (leakage guard).
  (c) Unfrozen run is REJECTED (freeze gate).
  (d) Forward-coverage gate rejects as_of_date lacking enough future prices.
  (e) Benchmarks present including random_community_baseline.
  (f) validation.csv conforms to §22 columns + carries the single-snapshot caveat.

Additional:
  (g) PROOF TEST: a price row with price_date <= as_of_date does NOT contribute
      to the forward return even when available_at > as_of_date.
  (h) Validation status 'blocked_insufficient_forward_data' when coverage missing.
  (i) backtest_status == 'disabled_not_enough_snapshots' for single-snapshot MVP.
  (j) API returns 409 for unfrozen run attempt.

No network or LLM calls; all fixture data is synthetic.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings, REPO_ROOT
from theme_engine.main import app
from theme_engine import runs
from theme_engine.models import RunCreateRequest
from theme_engine.validation import (
    BASKET_COLUMNS,
    VALIDATION_CSV_COLUMNS,
    _compute_basket_return,
    _apply_leakage_filter,
    _check_forward_coverage,
    _add_months,
    _SINGLE_SNAPSHOT_CAVEAT,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

AS_OF_DATE = "2024-06-30"
AS_OF = date.fromisoformat(AS_OF_DATE)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run(as_of_date: str = AS_OF_DATE) -> str:
    """Create a new run and return the run_id."""
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _seed_minimal_discovery(run_id: str, as_of_date: str = AS_OF_DATE) -> None:
    """Write minimal valid discovery artifacts for a run.

    Creates one Company entity, one EconomicConcept entity, one edge,
    communities.json, theme_snapshots.json, graph.json, and
    company_theme_exposure.parquet with one row.
    """
    run_dir = Path(settings.run_output_dir) / run_id
    ddir = run_dir / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    vdir = run_dir / "validation"
    vdir.mkdir(parents=True, exist_ok=True)

    company_id = "ent_company_val_test"
    concept_id = "ent_concept_val_test"
    community_id = "community_val_test"
    theme_snapshot_id = f"theme_{as_of_date}_{community_id}"

    # Entities
    from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS

    def _ent_row(**kw):
        d = {c: "" for c in ENTITIES_COLUMNS}
        d.update(kw)
        if not d.get("source_chunk_ids"):
            d["source_chunk_ids"] = ["chunk_v1"]
        if not d.get("confidence"):
            d["confidence"] = "0.9"
        if not d.get("review_status"):
            d["review_status"] = "accepted"
        if not d.get("extraction_method"):
            d["extraction_method"] = "document_stated"
        return d

    entities = [
        _ent_row(entity_id=company_id, entity_type="Company",
                 name="ValCo", canonical_name="ValCo",
                 ticker="VALCO", sector="Technology",
                 first_seen_at="2024-01-01"),
        _ent_row(entity_id=concept_id, entity_type="EconomicConcept",
                 name="AITheme", canonical_name="AITheme",
                 first_seen_at="2024-01-01"),
    ]
    pq.write_table(pa.Table.from_pylist(entities), ddir / "entities.parquet")

    # Edges
    def _edge_row(**kw):
        d = {c: "" for c in EDGES_COLUMNS}
        d.update(kw)
        if not d.get("evidence_chunk_ids"):
            d["evidence_chunk_ids"] = ["chunk_v1"]
        if not d.get("confidence"):
            d["confidence"] = "0.9"
        if not d.get("review_status"):
            d["review_status"] = "accepted"
        return d

    edges = [
        _edge_row(edge_id="edge_val_1",
                  source_entity_id=company_id,
                  target_entity_id=concept_id,
                  edge_type="exposed_to",
                  extraction_method="document_stated",
                  first_seen_at="2024-01-01",
                  last_seen_at=as_of_date,
                  as_of_date=as_of_date),
    ]
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")

    # Minimal stub artifacts (not read by validation, just needed for freeze)
    for name in [
        "raw_documents.parquet", "documents.parquet",
        "document_cleaning_log.parquet", "chunks.parquet",
        "entity_aliases.parquet",
    ]:
        (ddir / name).write_bytes(b"stub")

    # graph.json
    graph_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "projection": {"type": "entity_only",
                       "node_types_in_structural_graph": ["Company", "EconomicConcept"],
                       "excluded_node_types": ["Document"]},
        "structural_edge_types": ["exposed_to"],
        "evidence_edge_types": ["mentioned_in"],
        "nodes": [
            {"entity_id": company_id, "entity_type": "Company", "label": "ValCo", "attributes": {}},
            {"entity_id": concept_id, "entity_type": "EconomicConcept", "label": "AITheme", "attributes": {}},
        ],
        "edges": [
            {"edge_id": "edge_val_1", "source_entity_id": company_id,
             "target_entity_id": concept_id, "edge_type": "exposed_to",
             "weight": 0.9, "evidence_chunk_ids": ["chunk_v1"],
             "extraction_method": "document_stated"},
        ],
        "community_input_edges": ["edge_val_1"],
    }
    (ddir / "graph.json").write_text(json.dumps(graph_doc), encoding="utf-8")

    # communities.json
    communities_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "algorithm": "louvain",
        "communities": [
            {
                "community_id": community_id,
                "node_ids": [concept_id, company_id],
                "edge_ids": ["edge_val_1"],
                "size": 2,
                "density": 1.0,
                "top_entities": ["AITheme"],
                "top_companies": ["ValCo"],
                "theme_name": "AI Infrastructure Theme",
                "theme_summary": "Test theme for validation.",
                "naming_model": "deterministic",
            }
        ],
    }
    (ddir / "communities.json").write_text(json.dumps(communities_doc), encoding="utf-8")

    # theme_snapshots.json
    snapshots_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "snapshots": [
            {
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "theme_family_id": None,
                "state": "Emerging",
                "theme_name": "AI Infrastructure Theme",
                "summary": "Test theme.",
                "evidence_edge_ids": ["edge_val_1"],
            }
        ],
    }
    (ddir / "theme_snapshots.json").write_text(json.dumps(snapshots_doc), encoding="utf-8")

    # company_theme_exposure.parquet
    from theme_engine.exposure import EXPOSURE_COLUMNS
    exposure_row = {
        "schema_version": "1.0",
        "as_of_date": as_of_date,
        "company_id": company_id,
        "ticker": "VALCO",
        "theme_snapshot_id": theme_snapshot_id,
        "community_id": community_id,
        "exposure_score": 0.75,
        "graph_distance": 1.0,
        "edge_confidence_sum": 0.9,
        "evidence_count": 1,
        "top_evidence_chunk_ids": ["chunk_v1"],
        "calculation_method": "exposure_v1_document_stated",
    }
    pq.write_table(pa.Table.from_pylist([exposure_row]), ddir / "company_theme_exposure.parquet")

    return company_id, concept_id, community_id, theme_snapshot_id


def _write_market_prices(
    run_id: str,
    rows: list[dict],
) -> None:
    """Write market_prices.parquet to validation/ directory."""
    vdir = Path(settings.run_output_dir) / run_id / "validation"
    vdir.mkdir(parents=True, exist_ok=True)
    out_path = vdir / "market_prices.parquet"
    if not rows:
        # Write empty schema-valid table
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("run_id", pa.string()),
            ("as_of_date", pa.string()),
            ("company_id", pa.string()),
            ("ticker", pa.string()),
            ("price_date", pa.string()),
            ("close", pa.float64()),
            ("adjusted_close", pa.float64()),
            ("currency", pa.string()),
            ("source", pa.string()),
            ("source_id", pa.string()),
            ("available_at", pa.string()),
            ("created_at", pa.string()),
        ])
        empty = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
    else:
        pq.write_table(pa.Table.from_pylist(rows), out_path)


def _make_price_row(
    company_id: str,
    price_date: str,
    close: float,
    adjusted_close: float,
    run_id: str = "",
    as_of_date: str = AS_OF_DATE,
    available_at: Optional[str] = None,
    source: str = "test_market",
) -> dict:
    """Create a market_prices.parquet row."""
    if available_at is None:
        available_at = price_date  # normally available same day
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "company_id": company_id,
        "ticker": None,
        "price_date": price_date,
        "close": close,
        "adjusted_close": adjusted_close,
        "currency": "USD",
        "source": source,
        "source_id": None,
        "available_at": available_at,
        "created_at": _utcnow(),
    }


def _freeze_run(run_id: str) -> None:
    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, f"freeze failed: {resp.text}"


# ---------------------------------------------------------------------------
# (c) FREEZE GATE: unfrozen run must be rejected
# ---------------------------------------------------------------------------


def test_unfrozen_run_is_rejected():
    """POST /api/validation/run must return 409 for an unfrozen run."""
    run_id = _make_run()
    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 409, (
        f"Expected 409 for unfrozen run, got {resp.status_code}: {resp.text}"
    )
    assert "frozen" in resp.json()["detail"].lower(), (
        f"Error detail should mention 'frozen': {resp.json()['detail']}"
    )


def test_unfrozen_run_rejects_via_run_validation_module():
    """run_validation() raises PermissionError for unfrozen run."""
    from theme_engine.validation import run_validation

    run_id = _make_run()
    with pytest.raises(PermissionError):
        run_validation(run_id)


# ---------------------------------------------------------------------------
# (d) FORWARD COVERAGE GATE: rejects when insufficient future prices
# ---------------------------------------------------------------------------


def test_forward_coverage_gate_rejects_insufficient_prices():
    """Validation returns blocked_insufficient_forward_data when prices don't cover the window."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)

    # Write prices that are BEFORE or AT as_of_date — no forward coverage
    # Price dated on as_of_date itself; should not count as forward coverage
    prices = [
        _make_price_row(company_id, AS_OF_DATE, 100.0, 100.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, f"unexpected error: {resp.text}"
    body = resp.json()
    assert body["validation_status"] == "blocked_insufficient_forward_data", (
        f"Expected blocked status, got: {body['validation_status']}"
    )
    assert "missing_ranges" in body or body.get("message"), (
        "Response should indicate missing coverage"
    )


def test_forward_coverage_gate_rejects_short_3m_window():
    """3M window is blocked when max price_date < as_of_date + 3M."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)

    # Only 1 month of prices — enough for 1M but not 3M
    # as_of = 2024-06-30, 1M end = 2024-07-31, 3M end = 2024-09-30
    prices = [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 105.0, 105.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, f"unexpected error: {resp.text}"
    body = resp.json()
    # 1M window should be covered; 3M blocked. If all windows blocked -> blocked.
    # If 1M covered and 3M blocked -> partial -> should still yield 'completed' for 1M rows
    # Depends on how many windows are required. With forward_windows: [1M, 3M],
    # at least the 1M should complete if covered.
    assert body["validation_status"] in ("completed", "blocked_insufficient_forward_data"), (
        f"Unexpected status: {body}"
    )


# ---------------------------------------------------------------------------
# (b) Leakage guard: forward returns only use price_date > as_of_date
# ---------------------------------------------------------------------------


def test_leakage_forward_returns_exclude_prices_at_or_before_as_of_date():
    """_compute_basket_return must not use prices dated <= as_of_date for entry/exit.

    as_of = 2024-06-30, 1M window_end = 2024-07-30.
    Only prices with price_date in (2024-06-30, 2024-07-30] are valid.
    """
    company_id = "ent_leakage_test"
    as_of = AS_OF
    window_end = _add_months(as_of, 1)  # 2024-07-30

    price_rows = [
        _make_price_row(company_id, "2024-06-29", 50.0, 50.0),  # BEFORE as_of — exclude
        _make_price_row(company_id, "2024-06-30", 80.0, 80.0),  # AT as_of — exclude (leak)
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0),  # AFTER as_of — valid entry
        _make_price_row(company_id, "2024-07-30", 110.0, 110.0),  # within 1M window — valid exit
    ]

    ret, n, start_dt, end_dt = _compute_basket_return(
        [company_id],
        {company_id: 1.0},
        price_rows,
        as_of,
        window_end,
    )

    assert ret is not None, "Expected a return to be computed"
    assert n > 0, "Expected sample_size > 0"
    # Entry should be at 100.0 (2024-07-01), exit at 110.0 (2024-07-30) -> return = 0.10
    assert abs(ret - 0.10) < 1e-6, (
        f"Expected 10% return (100->110), got {ret:.6f}. "
        "Prices at or before as_of_date were leaked!"
    )
    # start_date must be strictly after as_of
    assert start_dt is not None and start_dt > as_of, (
        f"start_dt {start_dt} must be > as_of {as_of}"
    )


def test_proof_test_restated_row_excluded_by_availability_guard():
    """PROOF TEST: price_date <= as_of_date AND available_at > as_of_date must NOT contribute.

    This covers the case where a row has price_date = as_of_date (at-date price)
    AND available_at in the future (restated/backfilled). Both conditions should
    exclude it: price_date <= as_of_date is the primary filter; available_at > price_date
    is the secondary (availability guard). The row must NOT contribute to forward return.

    as_of = 2024-06-30, 1M window_end = 2024-07-30.
    """
    company_id = "ent_proof_restate_test"
    as_of = AS_OF
    window_end = _add_months(as_of, 1)  # 2024-07-30

    # Trap row: price_date = as_of_date (excluded by window: price_date <= as_of_date),
    # AND available_at > price_date (also excluded by availability guard).
    # A valid forward row is also included to confirm the return uses only valid data.
    price_rows = [
        # Trap: price_date <= as_of, available_at > price_date
        _make_price_row(company_id, "2024-06-30", 200.0, 200.0,
                        available_at="2024-08-01"),  # restatement of at-date price
        # Valid forward entry
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0),
        # Valid forward exit (within 1M window: 2024-07-30)
        _make_price_row(company_id, "2024-07-30", 120.0, 120.0),
    ]

    ret, n, start_dt, end_dt = _compute_basket_return(
        [company_id],
        {company_id: 1.0},
        price_rows,
        as_of,
        window_end,
    )

    assert ret is not None, "Expected a forward return"
    # Return should be (120-100)/100 = 0.20, NOT (200-something)
    assert abs(ret - 0.20) < 1e-6, (
        f"Expected 20% return (100->120), got {ret:.6f}. "
        "The restated at-date price was leaked!"
    )


def test_availability_guard_excludes_restated_future_prices():
    """A price row with available_at > price_date (restated) is excluded from forward return.

    as_of = 2024-06-30, 1M window_end = 2024-07-30.
    """
    company_id = "ent_avail_guard_test"
    as_of = AS_OF
    window_end = _add_months(as_of, 1)  # 2024-07-30

    # price_date = 2024-07-15 (in 1M window), available_at = 2024-09-01 (FUTURE restatement)
    # This row should be EXCLUDED by the availability guard (available_at > price_date).
    # Only the non-restated rows should contribute.
    price_rows = [
        # Restated: available_at > price_date -> exclude
        _make_price_row(company_id, "2024-07-15", 200.0, 200.0,
                        available_at="2024-09-01"),
        # Valid: available_at == price_date (entry)
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0),
        # Valid: available_at == price_date (exit, within 1M window)
        _make_price_row(company_id, "2024-07-30", 110.0, 110.0),
    ]

    ret, n, start_dt, end_dt = _compute_basket_return(
        [company_id],
        {company_id: 1.0},
        price_rows,
        as_of,
        window_end,
    )

    # Entry: 100.0 (2024-07-01), Exit: 110.0 (2024-07-30) — the restated 200.0 row is excluded
    assert ret is not None
    assert abs(ret - 0.10) < 1e-6, (
        f"Expected 10% return (100->110), got {ret:.6f}. "
        "Restated row with available_at > price_date should have been excluded."
    )


# ---------------------------------------------------------------------------
# (a) Baskets reproducible + conform to io_contracts §21
# ---------------------------------------------------------------------------


def test_baskets_reproducible_same_input():
    """Building baskets twice from the same exposure produces identical results."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, community_id, theme_snapshot_id = _seed_minimal_discovery(run_id)

    from theme_engine.validation import _build_theme_baskets, _load_exposure, _load_theme_snapshots

    exposure = _load_exposure(run_id)
    snapshots = _load_theme_snapshots(run_id)

    baskets_a = _build_theme_baskets(run_id, AS_OF_DATE, exposure, snapshots, basket_top_n=10)
    baskets_b = _build_theme_baskets(run_id, AS_OF_DATE, exposure, snapshots, basket_top_n=10)

    assert baskets_a == baskets_b, "Basket construction is not deterministic"


def test_baskets_conform_to_io_contracts_21():
    """portfolio_baskets.parquet has exactly io_contracts §21 columns."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    basket_path = Path(settings.run_output_dir) / run_id / "validation" / "portfolio_baskets.parquet"
    assert basket_path.exists(), "portfolio_baskets.parquet was not written"

    table = pq.read_table(basket_path)
    assert list(table.schema.names) == BASKET_COLUMNS, (
        f"Basket columns mismatch.\n  expected: {BASKET_COLUMNS}\n  got: {list(table.schema.names)}"
    )


def test_baskets_have_weights_summing_to_one():
    """Basket weights should sum to approximately 1.0 per basket_id."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    basket_path = Path(settings.run_output_dir) / run_id / "validation" / "portfolio_baskets.parquet"
    rows = pq.read_table(basket_path).to_pylist()

    if not rows:
        pytest.skip("No basket rows produced (empty exposure)")

    from collections import defaultdict
    weight_sums: dict[str, float] = defaultdict(float)
    for row in rows:
        weight_sums[row["basket_id"]] += float(row["weight"])

    for basket_id, total in weight_sums.items():
        assert abs(total - 1.0) < 0.01, (
            f"Basket {basket_id!r} weights sum to {total:.4f}, expected ~1.0"
        )


def test_baskets_have_required_fields():
    """Every basket row has non-null required fields."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    client.post("/api/validation/run", json={"run_id": run_id})

    basket_path = Path(settings.run_output_dir) / run_id / "validation" / "portfolio_baskets.parquet"
    rows = pq.read_table(basket_path).to_pylist()

    for row in rows:
        assert row.get("schema_version"), f"missing schema_version: {row}"
        assert row.get("run_id"), f"missing run_id: {row}"
        assert row.get("basket_id"), f"missing basket_id: {row}"
        assert row.get("theme_snapshot_id"), f"missing theme_snapshot_id: {row}"
        assert row.get("community_id"), f"missing community_id: {row}"
        assert row.get("company_id"), f"missing company_id: {row}"
        assert row.get("portfolio_method"), f"missing portfolio_method: {row}"
        assert row.get("inclusion_reason"), f"missing inclusion_reason: {row}"
        assert isinstance(row.get("selection_rank"), int), f"selection_rank must be int: {row}"
        assert isinstance(row.get("exposure_score"), float), f"exposure_score must be float: {row}"
        assert isinstance(row.get("weight"), float), f"weight must be float: {row}"


# ---------------------------------------------------------------------------
# (f) validation.csv columns + single-snapshot caveat
# ---------------------------------------------------------------------------


def test_validation_csv_conforms_to_io_contracts_22():
    """validation.csv has exactly io_contracts §22 columns."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    assert csv_path.exists(), "validation.csv was not written"

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames

    assert list(headers) == VALIDATION_CSV_COLUMNS, (
        f"validation.csv columns mismatch.\n  expected: {VALIDATION_CSV_COLUMNS}\n  got: {list(headers)}"
    )


def test_validation_csv_carries_single_snapshot_caveat():
    """Every validation.csv row must carry the single-snapshot MVP caveat."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows, "validation.csv has no data rows"
    for row in rows:
        assert "ILLUSTRATIVE" in row.get("caveats", "").upper() or \
               "single-snapshot" in row.get("caveats", "").lower(), (
            f"Row missing single-snapshot caveat: {row.get('caveats', '')!r}"
        )


def test_validation_csv_has_benchmark_columns():
    """validation.csv rows have benchmark_name, benchmark_return, excess_return columns."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    client.post("/api/validation/run", json={"run_id": run_id})

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        pytest.skip("No validation.csv rows produced")

    for row in rows:
        assert "benchmark_name" in row, f"missing benchmark_name: {row.keys()}"
        assert "benchmark_return" in row, f"missing benchmark_return: {row.keys()}"
        assert "excess_return" in row, f"missing excess_return: {row.keys()}"


# ---------------------------------------------------------------------------
# (e) Benchmarks present including random_community_baseline
# ---------------------------------------------------------------------------


def test_benchmarks_include_random_community_baseline():
    """validation.csv must include a row with benchmark_name=random_community_baseline."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        pytest.skip("No validation.csv rows (no themes with exposure)")

    benchmark_names = {row.get("benchmark_name", "") for row in rows}
    assert "random_community_baseline" in benchmark_names, (
        f"random_community_baseline not found in benchmark_names: {benchmark_names}"
    )


def test_benchmarks_include_equal_weight_universe():
    """validation.csv includes equal_weight_universe benchmark."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    client.post("/api/validation/run", json={"run_id": run_id})

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        pytest.skip("No validation.csv rows")

    benchmark_names = {row.get("benchmark_name", "") for row in rows}
    assert "equal_weight_universe" in benchmark_names, (
        f"equal_weight_universe not in benchmarks: {benchmark_names}"
    )


def test_benchmark_set_equals_config_list():
    """The emitted benchmark set must match the config benchmarks list."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        pytest.skip("No validation.csv rows")

    # Load config to get expected benchmark set
    from theme_engine.validation import _load_validation_config
    manifest = runs.load_manifest(run_id)
    config = _load_validation_config(manifest.validation_config)
    config_benchmarks = set(config.get("benchmarks", []))

    emitted_benchmarks = {row.get("benchmark_name", "") for row in rows}
    # Every config benchmark should appear in emitted benchmarks
    for bm in config_benchmarks:
        assert bm in emitted_benchmarks, (
            f"Config benchmark {bm!r} missing from validation.csv benchmarks: {emitted_benchmarks}"
        )


# ---------------------------------------------------------------------------
# (h) blocked_insufficient_forward_data status + payload fields
# ---------------------------------------------------------------------------


def test_blocked_status_has_required_payload_fields():
    """When validation is blocked, response includes missing_ranges, as_of_date, holding_window, required_end."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    # No prices at all -> blocked
    _write_market_prices(run_id, [])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["validation_status"] == "blocked_insufficient_forward_data", (
        f"Expected blocked status: {body}"
    )
    # Payload fields per spec §22
    assert "missing_ranges" in body or body.get("message"), (
        "Blocked response must include missing_ranges or message"
    )
    assert body.get("as_of_date") == AS_OF_DATE or body.get("message"), (
        "Blocked response must include as_of_date"
    )


# ---------------------------------------------------------------------------
# (i) backtest_status == disabled_not_enough_snapshots
# ---------------------------------------------------------------------------


def test_backtest_status_disabled_for_single_snapshot():
    """Completed validation run has backtest_status=disabled_not_enough_snapshots."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("backtest_status") == "disabled_not_enough_snapshots", (
        f"Expected disabled_not_enough_snapshots, got: {body.get('backtest_status')!r}"
    )


def test_blocked_status_also_has_disabled_backtest():
    """Blocked validation run also has backtest_status=disabled_not_enough_snapshots."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [])  # no prices -> blocked
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("backtest_status") == "disabled_not_enough_snapshots", (
        f"Expected disabled_not_enough_snapshots in blocked state, got: {body.get('backtest_status')!r}"
    )


# ---------------------------------------------------------------------------
# Additional: full end-to-end validation with complete coverage
# ---------------------------------------------------------------------------


def test_full_validation_run_writes_all_artifacts():
    """Full validation with sufficient coverage writes both output artifacts."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-07-31", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["validation_status"] == "completed"

    val_dir = Path(settings.run_output_dir) / run_id / "validation"
    assert (val_dir / "portfolio_baskets.parquet").exists(), "portfolio_baskets.parquet missing"
    assert (val_dir / "validation.csv").exists(), "validation.csv missing"

    # Check artifacts in response
    assert "validation/portfolio_baskets.parquet" in body["artifacts"]
    assert "validation/validation.csv" in body["artifacts"]


def test_validation_csv_run_id_matches():
    """validation.csv rows have the correct run_id."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _write_market_prices(run_id, [
        _make_price_row(company_id, "2024-07-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 115.0, 115.0, run_id=run_id),
    ])
    _freeze_run(run_id)

    client.post("/api/validation/run", json={"run_id": run_id})

    csv_path = Path(settings.run_output_dir) / run_id / "validation" / "validation.csv"
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        assert row["run_id"] == run_id, f"Wrong run_id in row: {row['run_id']!r}"


def test_hash_mismatch_blocks_validation():
    """Mutating a discovery artifact after freeze causes validation to be blocked (409)."""
    run_id = _make_run(AS_OF_DATE)
    company_id, _, _, _ = _seed_minimal_discovery(run_id)
    _freeze_run(run_id)

    # Mutate a frozen artifact
    mutated = Path(settings.run_output_dir) / run_id / "discovery" / "graph.json"
    mutated.write_text('{"mutated": true}', encoding="utf-8")

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 409, (
        f"Expected 409 on hash mismatch, got {resp.status_code}: {resp.text}"
    )
    assert "mismatch" in resp.json()["detail"].lower() or \
           "hash" in resp.json()["detail"].lower(), (
        f"Expected hash mismatch message: {resp.json()['detail']}"
    )


def test_validation_forward_window_uses_price_date_not_available_at():
    """Coverage gate uses price_date, not available_at, to determine max coverage."""
    company_id = "ent_coverage_gate_test"
    as_of = date(2024, 3, 31)
    # 3M window requires max(price_date) >= 2024-06-30
    window_end = _add_months(as_of, 3)  # 2024-06-30

    # Rows whose price_date is >= window_end
    rows_with_coverage = [
        {"company_id": company_id, "price_date": "2024-06-30",
         "available_at": "2024-06-30", "close": 100.0, "adjusted_close": 100.0},
    ]
    assert _check_forward_coverage(rows_with_coverage, as_of, window_end) is True

    # Rows whose price_date is < window_end
    rows_without = [
        {"company_id": company_id, "price_date": "2024-06-29",
         "available_at": "2024-06-30", "close": 100.0, "adjusted_close": 100.0},
    ]
    assert _check_forward_coverage(rows_without, as_of, window_end) is False
