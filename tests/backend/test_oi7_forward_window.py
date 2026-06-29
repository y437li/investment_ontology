"""OI-7: Forward-window = 3M; skip-not-shrink coverage policy.

Asserts (per OI-7 spec acceptance):
  (1) sweep.forward_window is 3M in config and the runner uses it.
  (2) A walk-forward point with <3M forward price coverage is SKIPPED (not shrunk),
      recorded with skipped=True and a skipped_reason, and has excess=None.
  (3) A SKIPPED point is EXCLUDED from n_points (and therefore from claim_supported).
  (4) A point WITH >=3M forward price coverage is included and measured over the
      full 3M window.
  (5) OI-7 interacts correctly with OI-1: n_points counts only fully-covered points.

Hermetic: no network or LLM calls; all data is synthetic in-test fixtures.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app
from theme_engine import runs
from theme_engine.validation import (
    _add_months,
    _check_forward_coverage,
    _window_months,
    _load_validation_config,
    run_walk_forward_validation,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Run as_of must cover the latest walk-forward point (2024-09-30 from config).
RUN_AS_OF = "2024-09-30"

# Walk-forward points from configs/validation.example.yml
WF_POINT_1 = "2024-03-31"
WF_POINT_2 = "2024-06-30"
WF_POINT_3 = "2024-09-30"

COMPANY_ID = "ent_oi7_test_company"

# 3M window ends for each point
WINDOW_END_1 = date(2024, 6, 30)   # 2024-03-31 + 3M
WINDOW_END_2 = date(2024, 9, 30)   # 2024-06-30 + 3M
WINDOW_END_3 = date(2024, 12, 31)  # 2024-09-30 + 3M


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run(as_of_date: str = RUN_AS_OF) -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _freeze_run(run_id: str) -> None:
    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, f"freeze failed: {resp.text}"


def _seed_minimal_discovery(run_id: str, as_of_date: str = RUN_AS_OF) -> None:
    """Write minimal valid discovery artifacts for an OI-7 test run."""
    run_dir = Path(settings.run_output_dir) / run_id
    ddir = run_dir / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    vdir = run_dir / "validation"
    vdir.mkdir(parents=True, exist_ok=True)

    concept_id = "ent_oi7_concept"
    community_id = "community_oi7_test"
    theme_snapshot_id = f"theme_{as_of_date}_{community_id}"

    from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS

    def _ent_row(**kw):
        d = {c: "" for c in ENTITIES_COLUMNS}
        d.update(kw)
        if not d.get("source_chunk_ids"):
            d["source_chunk_ids"] = ["chunk_oi7"]
        if not d.get("confidence"):
            d["confidence"] = "0.9"
        if not d.get("review_status"):
            d["review_status"] = "accepted"
        if not d.get("extraction_method"):
            d["extraction_method"] = "document_stated"
        return d

    entities = [
        _ent_row(
            entity_id=COMPANY_ID,
            entity_type="Company",
            name="OI7Co",
            canonical_name="OI7Co",
            ticker="OI7C",
            sector="Technology",
            first_seen_at="2024-01-01",
        ),
        _ent_row(
            entity_id=concept_id,
            entity_type="EconomicConcept",
            name="OI7Theme",
            canonical_name="OI7Theme",
            first_seen_at="2024-01-01",
        ),
    ]
    pq.write_table(pa.Table.from_pylist(entities), ddir / "entities.parquet")

    def _edge_row(**kw):
        d = {c: "" for c in EDGES_COLUMNS}
        d.update(kw)
        if not d.get("evidence_chunk_ids"):
            d["evidence_chunk_ids"] = ["chunk_oi7"]
        if not d.get("confidence"):
            d["confidence"] = "0.9"
        if not d.get("review_status"):
            d["review_status"] = "accepted"
        return d

    edges = [
        _edge_row(
            edge_id="edge_oi7_1",
            source_entity_id=COMPANY_ID,
            target_entity_id=concept_id,
            edge_type="exposed_to",
            extraction_method="document_stated",
            first_seen_at="2024-01-01",
            last_seen_at=as_of_date,
            as_of_date=as_of_date,
        ),
    ]
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")

    for name in [
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entity_aliases.parquet",
    ]:
        (ddir / name).write_bytes(b"stub")

    graph_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "projection": {
            "type": "entity_only",
            "node_types_in_structural_graph": ["Company", "EconomicConcept"],
            "excluded_node_types": ["Document"],
        },
        "structural_edge_types": ["exposed_to"],
        "evidence_edge_types": ["mentioned_in"],
        "nodes": [
            {"entity_id": COMPANY_ID, "entity_type": "Company", "label": "OI7Co", "attributes": {}},
            {"entity_id": concept_id, "entity_type": "EconomicConcept", "label": "OI7Theme", "attributes": {}},
        ],
        "edges": [
            {
                "edge_id": "edge_oi7_1",
                "source_entity_id": COMPANY_ID,
                "target_entity_id": concept_id,
                "edge_type": "exposed_to",
                "weight": 0.9,
                "evidence_chunk_ids": ["chunk_oi7"],
                "extraction_method": "document_stated",
            },
        ],
        "community_input_edges": ["edge_oi7_1"],
    }
    (ddir / "graph.json").write_text(json.dumps(graph_doc), encoding="utf-8")

    communities_doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "algorithm": "louvain",
        "communities": [
            {
                "community_id": community_id,
                "node_ids": [concept_id, COMPANY_ID],
                "edge_ids": ["edge_oi7_1"],
                "size": 2,
                "density": 1.0,
                "top_entities": ["OI7Theme"],
                "top_companies": ["OI7Co"],
                "theme_name": "OI-7 Test Theme",
                "theme_summary": "Test theme for OI-7 forward-window validation.",
                "naming_model": "deterministic",
            }
        ],
    }
    (ddir / "communities.json").write_text(json.dumps(communities_doc), encoding="utf-8")

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
                "theme_name": "OI-7 Test Theme",
                "summary": "Test theme for OI-7.",
                "evidence_edge_ids": ["edge_oi7_1"],
            }
        ],
    }
    (ddir / "theme_snapshots.json").write_text(json.dumps(snapshots_doc), encoding="utf-8")

    from theme_engine.exposure import EXPOSURE_COLUMNS

    exposure_row = {
        "schema_version": "1.0",
        "as_of_date": as_of_date,
        "company_id": COMPANY_ID,
        "ticker": "OI7C",
        "theme_snapshot_id": theme_snapshot_id,
        "community_id": community_id,
        "exposure_score": 0.80,
        "graph_distance": 1.0,
        "edge_confidence_sum": 0.9,
        "evidence_count": 1,
        "top_evidence_chunk_ids": ["chunk_oi7"],
        "calculation_method": "exposure_v1_document_stated",
    }
    pq.write_table(pa.Table.from_pylist([exposure_row]), ddir / "company_theme_exposure.parquet")

    metrics_row = {
        "schema_version": "1.0",
        "theme_snapshot_id": theme_snapshot_id,
        "community_id": community_id,
        "as_of_date": as_of_date,
        "strength": 0.8,
        "cohesion": 0.5,
        "saturation": 0.01,
    }
    pq.write_table(pa.Table.from_pylist([metrics_row]), ddir / "theme_metrics.parquet")


def _make_price_row(
    company_id: str,
    price_date: str,
    close: float,
    adjusted_close: float,
    run_id: str = "",
    as_of_date: str = RUN_AS_OF,
    available_at: Optional[str] = None,
) -> dict:
    if available_at is None:
        available_at = price_date
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
        "source": "test_oi7",
        "source_id": None,
        "available_at": available_at,
        "created_at": _utcnow(),
    }


def _write_market_prices(run_id: str, rows: list[dict]) -> None:
    vdir = Path(settings.run_output_dir) / run_id / "validation"
    vdir.mkdir(parents=True, exist_ok=True)
    out_path = vdir / "market_prices.parquet"
    if not rows:
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


# ---------------------------------------------------------------------------
# (1) forward_window is 3M in config
# ---------------------------------------------------------------------------


def test_config_forward_window_is_3m():
    """configs/validation.example.yml sweep.forward_window must be 3M (OI-7 LOCKED).

    Asserts that the config file's sweep.forward_window reads as 3M and
    that _window_months() parses it to 3.
    """
    # Load the config the same way validation.py does.
    from theme_engine.validation import _load_validation_config

    config = _load_validation_config("configs/validation.example.yml")
    sweep = config.get("sweep", {}) or {}
    fw = str(sweep.get("forward_window", ""))

    assert fw == "3M", (
        f"sweep.forward_window must be '3M' in configs/validation.example.yml. "
        f"Got: {fw!r}"
    )
    assert _window_months(fw) == 3, (
        f"_window_months('3M') must return 3. Got: {_window_months(fw)}"
    )


def test_config_on_insufficient_coverage_is_skip():
    """configs/validation.example.yml sweep.on_insufficient_coverage must be 'skip' (OI-7).

    The skip-not-shrink policy must be expressed in the config.
    """
    from theme_engine.validation import _load_validation_config

    config = _load_validation_config("configs/validation.example.yml")
    sweep = config.get("sweep", {}) or {}
    policy = sweep.get("on_insufficient_coverage", "")

    assert policy == "skip", (
        f"sweep.on_insufficient_coverage must be 'skip' in configs/validation.example.yml. "
        f"Got: {policy!r}"
    )


def test_runner_uses_3m_window_by_default():
    """run_walk_forward_validation uses 3M forward window (from config default).

    We verify by checking the 'forward_window' in the returned result and
    confirming a point spanning exactly 3M is valid while one without 3M is skipped.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Provide prices covering all three 3M windows exactly.
    prices = [
        # Point 1 (2024-03-31): 3M window ends 2024-06-30
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
        # Point 2 (2024-06-30): 3M window ends 2024-09-30
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3 (2024-09-30): 3M window ends 2024-12-31
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-12-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["forward_window"] == "3M", (
        f"run_walk_forward_validation must use forward_window='3M'. "
        f"Got: {result['forward_window']!r}"
    )
    assert result["n_points"] == 3, (
        f"Expected 3 valid points when all 3M windows are covered. "
        f"Got n_points={result['n_points']}. Points: {result.get('points')}"
    )


# ---------------------------------------------------------------------------
# (2) A point with <3M coverage is SKIPPED, not shrunk
# ---------------------------------------------------------------------------


def test_point_with_insufficient_3m_coverage_is_skipped():
    """A walk-forward point lacking >=3M of forward price coverage is SKIPPED.

    Scenario: only Points 1 and 2 have full 3M coverage.
    Point 3 (2024-09-30) needs prices up to 2024-12-31 but we only provide
    prices through 2024-11-30 — 2M forward from Point 3.
    Point 3 must be SKIPPED (not measured with a shortened 2M window).
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Points 1 and 2: full 3M coverage.
    # Point 3 (2024-09-30): only 2M forward prices (ends 2024-11-30 < 2024-12-31).
    prices = [
        # Point 1: full 3M (window ends 2024-06-30)
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
        # Point 2: full 3M (window ends 2024-09-30)
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3: only 2M coverage (max price_date = 2024-11-30 < window_end 2024-12-31)
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-11-30", 124.0, 124.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True

    # n_points must be 2 (Points 1 and 2 covered; Point 3 skipped)
    assert result["n_points"] == 2, (
        f"Expected n_points=2 (Points 1+2 valid, Point 3 skipped). "
        f"Got n_points={result['n_points']}. Points: {result.get('points')}"
    )

    # Find Point 3 in the panel
    point3 = next(
        (p for p in result["points"] if p["as_of"] == WF_POINT_3), None
    )
    assert point3 is not None, f"Point for {WF_POINT_3} not found in {result['points']}"

    # Point 3 must be skipped: excess is None, NOT a computed return
    assert point3.get("excess") is None, (
        f"SKIP-NOT-SHRINK VIOLATED: Point 3 has a computed excess={point3['excess']!r}. "
        f"It should be None (skipped) because it lacks 3M coverage. "
        f"The window must NOT be shortened to admit a partial-coverage point."
    )

    # Point 3 must have a skipped reason
    assert point3.get("skipped_reason"), (
        f"Skipped point must have a skipped_reason. Got: {point3}"
    )
    assert "insufficient_forward_coverage" in point3["skipped_reason"], (
        f"skipped_reason should indicate insufficient_forward_coverage. "
        f"Got: {point3['skipped_reason']!r}"
    )


def test_skipped_point_has_skipped_flag_and_reason():
    """A SKIPPED point carries skipped=True and a non-empty skipped_reason.

    Verifies the output schema of a skipped point.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Provide prices only for Point 1's 3M window; Points 2 and 3 lack coverage.
    prices = [
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    # Find all skipped points
    skipped_points = [
        p for p in result["points"]
        if p.get("skipped") is True or p.get("skipped_reason")
    ]
    assert len(skipped_points) >= 2, (
        f"Expected at least 2 skipped points (Points 2 and 3), "
        f"got {len(skipped_points)}: {result['points']}"
    )

    for sp in skipped_points:
        assert sp.get("skipped") is True, (
            f"Skipped point must have skipped=True. Got: {sp}"
        )
        assert sp.get("skipped_reason"), (
            f"Skipped point must have a non-empty skipped_reason. Got: {sp}"
        )
        assert sp.get("excess") is None, (
            f"Skipped point must have excess=None (not a computed value). Got: {sp}"
        )


# ---------------------------------------------------------------------------
# (3) Skipped points are excluded from n_points (OI-1 interaction)
# ---------------------------------------------------------------------------


def test_skipped_point_excluded_from_n_points():
    """A SKIPPED point does NOT count toward n_points or claim_supported.

    Scenario:
    - 3 walk-forward points configured.
    - Only Points 1 and 2 have full 3M coverage; Point 3 is skipped.
    - n_points must be 2 (not 3).
    - claim_supported=False because n_points=2 < min_points_for_claim=3.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Points 1 and 2 have 3M coverage; Point 3 does not.
    prices = [
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3 (2024-09-30): only 1M of prices — insufficient for 3M window
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-31", 125.0, 125.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True

    # Skipped point must not contribute to n_points
    assert result["n_points"] == 2, (
        f"n_points must be 2 (Point 3 skipped, excluded from count). "
        f"Got n_points={result['n_points']}. Points: {result.get('points')}"
    )

    # With n_points=2 < min_points_for_claim=3, claim must not be supported
    assert result.get("claim_supported") is False, (
        f"claim_supported must be False when n_points ({result['n_points']}) < "
        f"min_points_for_claim ({result['min_points_for_claim']}). "
        f"A skipped point must NOT inflate n_points into supporting a claim."
    )
    assert result.get("illustrative") is True, (
        f"illustrative must be True when n_points < min_points_for_claim. "
        f"Got: {result.get('illustrative')!r}"
    )


def test_all_3_skipped_gives_n_points_zero():
    """When all 3 points are skipped (no coverage for any), n_points=0.

    n_points=0 means claim_supported=False and illustrative=True.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # No prices at all -> all 3 points will have no coverage -> all skipped.
    _write_market_prices(run_id, [])
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["n_points"] == 0, (
        f"Expected n_points=0 when no forward coverage exists. "
        f"Got n_points={result['n_points']}"
    )
    assert result.get("claim_supported") is False, (
        f"claim_supported must be False with n_points=0. "
        f"Got: {result.get('claim_supported')!r}"
    )
    assert result.get("illustrative") is True, (
        f"illustrative must be True with n_points=0. "
        f"Got: {result.get('illustrative')!r}"
    )

    # All points should be in the output with skipped=True
    for pt in result["points"]:
        assert pt.get("skipped") is True, (
            f"All points should be skipped when no coverage. Point: {pt}"
        )


# ---------------------------------------------------------------------------
# (4) A point WITH >=3M coverage is included and measured over the full 3M
# ---------------------------------------------------------------------------


def test_covered_point_is_included_and_measured_over_full_3m():
    """A point with full 3M forward price coverage is included in the panel.

    The measured return must use prices from strictly after as_of through
    the full 3M window_end — NOT a shorter window.

    Scenario: Point 1 (2024-03-31, window ends 2024-06-30).
    entry=2024-04-01 @ 100, exit=2024-06-30 @ 109.
    Expected return: (109-100)/100 = 9%.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Point 1: full 3M coverage (entry shortly after as_of, exit at window_end).
    # Points 2 and 3: also covered (needed so the panel runs without other issues).
    prices = [
        # Point 1: 3M window [2024-04-01, 2024-06-30]
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        # Mid-window price (should be the exit if it's the latest within 3M)
        _make_price_row(COMPANY_ID, "2024-05-15", 104.0, 104.0, run_id=run_id),
        # Full 3M exit — latest price within the 3M window
        _make_price_row(COMPANY_ID, "2024-06-30", 109.0, 109.0, run_id=run_id),
        # Point 2: 3M window [2024-07-01, 2024-09-30]
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3: 3M window [2024-10-01, 2024-12-31]
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-12-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["n_points"] == 3, (
        f"All 3 points should be valid with full 3M coverage. "
        f"Got n_points={result['n_points']}"
    )

    point1 = next(
        (p for p in result["points"] if p["as_of"] == WF_POINT_1), None
    )
    assert point1 is not None, f"Point for {WF_POINT_1} not found"
    assert point1.get("excess") is not None, (
        f"Covered point must have a computed excess (not None). Got: {point1}"
    )
    assert point1.get("skipped_reason") is None, (
        f"Covered point must NOT have a skipped_reason. Got: {point1}"
    )

    # Return must use the full 3M window exit (2024-06-30 @ 109), not a shorter window.
    # Expected: (109 - 100) / 100 = 0.09
    expected_return = (109.0 - 100.0) / 100.0
    theme_ret = point1.get("theme_basket_return")
    assert theme_ret is not None, f"theme_basket_return is None for covered point: {point1}"
    assert abs(theme_ret - expected_return) < 1e-6, (
        f"Point 1 return must use full 3M window. "
        f"Expected {expected_return:.4f} (entry=100@2024-04-01, exit=109@2024-06-30), "
        f"got {theme_ret:.4f}. The window may have been incorrectly shortened."
    )


def test_3m_window_boundary_exact_coverage():
    """A point whose max(price_date) == window_end exactly is included (not skipped).

    Coverage check: max(price_date) >= window_end.
    Exact equality (max == window_end) satisfies the condition.
    """
    as_of = date(2024, 3, 31)
    window_end = _add_months(as_of, 3)  # 2024-06-30

    # Exactly one price AT window_end
    rows = [
        {"company_id": COMPANY_ID, "price_date": "2024-06-30",
         "available_at": "2024-06-30", "close": 100.0, "adjusted_close": 100.0},
    ]
    assert _check_forward_coverage(rows, as_of, window_end) is True, (
        "A price_date == window_end must satisfy the coverage gate (>=, not >)."
    )


def test_just_under_3m_coverage_fails_gate():
    """max(price_date) one day before window_end fails the 3M coverage gate.

    Ensures skip-not-shrink: a point with max_price_date = window_end - 1 day
    does NOT pass the coverage check and must be SKIPPED.
    """
    as_of = date(2024, 3, 31)
    window_end = _add_months(as_of, 3)  # 2024-06-30

    # max price_date is 2024-06-29 (one day short of 2024-06-30)
    rows = [
        {"company_id": COMPANY_ID, "price_date": "2024-06-29",
         "available_at": "2024-06-29", "close": 100.0, "adjusted_close": 100.0},
    ]
    assert _check_forward_coverage(rows, as_of, window_end) is False, (
        "max(price_date) < window_end must FAIL the coverage gate. "
        "The point must be SKIPPED, not admitted with a shortened window."
    )


# ---------------------------------------------------------------------------
# (5) OI-7 + OI-1 interaction: claim requires >= 3 COVERED (non-skipped) points
# ---------------------------------------------------------------------------


def test_claim_requires_3_fully_covered_points():
    """claim_supported=True requires n_points >= 3 fully-covered (non-skipped) points.

    When all 3 walk-forward points have full 3M coverage, n_points=3 and
    claim_supported=True (assuming min_points_for_claim=3 in config).
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Provide full 3M coverage for all 3 points.
    prices = [
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-12-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["n_points"] == 3, (
        f"Expected n_points=3 with all 3 points fully covered. "
        f"Got n_points={result['n_points']}"
    )
    assert result.get("claim_supported") is True, (
        f"claim_supported must be True when n_points (3) >= min_points_for_claim (3). "
        f"Got: {result.get('claim_supported')!r}"
    )


def test_2_covered_1_skipped_does_not_support_claim():
    """2 covered + 1 skipped = n_points=2, which is below min_points_for_claim=3.

    Verifies that a skipped point cannot be used to reach the claim threshold.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Points 1 and 2: full 3M coverage. Point 3: only 1M coverage (skipped).
    prices = [
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-06-30", 106.0, 106.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3: only 2024-10-01 to 2024-10-31 — not enough for 3M window
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-31", 124.0, 124.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["n_points"] == 2, (
        f"n_points must be 2 (Point 3 skipped). Got: {result['n_points']}"
    )
    assert result.get("claim_supported") is False, (
        f"claim_supported must be False: n_points=2 < min_points_for_claim=3. "
        f"A skipped point must NOT pad n_points. Got: {result.get('claim_supported')!r}"
    )
    assert result.get("illustrative") is True, (
        f"illustrative must be True when n_points < min_points_for_claim. "
        f"Got: {result.get('illustrative')!r}"
    )
