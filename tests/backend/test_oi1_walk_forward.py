"""OI-1: Minimal walk-forward validation + illustrative discipline tests.

Asserts (per OI-1 spec §22 acceptance):
  (1) The walk-forward runner computes a panel across >= 3 monthly as_of points
      from a market_prices fixture spanning the points, with per-point excess vs
      baseline + pooled stats (mean_excess, hit_rate, n_points).
  (2) A single-snapshot result carries illustrative=True / claim_supported=False.
  (3) A panel with n_points >= min_points_for_claim can carry claim_supported=True.
  (4) No code path produces an excess-return claim from a single snapshot (hard rule
      proved by test: single-snapshot output is always flagged illustrative).
  (5) PIT per point: forward window strictly after each point's as_of; the
      OI-3 freeze guard is respected.

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
from theme_engine import (
    exposure as exposure_mod,
    freeze as freeze_mod,
    graph_build,
    runs,
    themes,
)
from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS
from theme_engine.models import RunCreateRequest
from theme_engine.validation import (
    VALIDATION_PANEL_FILENAME,
    VALIDATION_PANEL_SCHEMA_VERSION,
    _add_months,
    _compute_basket_return,
    run_validation,
    run_walk_forward_validation,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The run's as_of_date must be >= the latest walk_forward.as_of_date in config
# (2024-09-30), so we use 2024-09-30 as the discovery snapshot date.
RUN_AS_OF = "2024-09-30"

# These match walk_forward.as_of_dates in configs/validation.example.yml
WF_POINT_1 = "2024-03-31"
WF_POINT_2 = "2024-06-30"
WF_POINT_3 = "2024-09-30"

COMPANY_ID = "ent_wf_test_company"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run(as_of_date: str = RUN_AS_OF) -> str:
    """Create a new run and return the run_id."""
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _freeze_run(run_id: str) -> None:
    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, f"freeze failed: {resp.text}"


def _seed_minimal_discovery(run_id: str, as_of_date: str = RUN_AS_OF) -> tuple:
    """Write minimal valid discovery artifacts for a walk-forward test run."""
    run_dir = Path(settings.run_output_dir) / run_id
    ddir = run_dir / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    vdir = run_dir / "validation"
    vdir.mkdir(parents=True, exist_ok=True)

    concept_id = "ent_wf_concept"
    community_id = "community_wf_test"
    theme_snapshot_id = f"theme_{as_of_date}_{community_id}"

    from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS

    def _ent_row(**kw):
        d = {c: "" for c in ENTITIES_COLUMNS}
        d.update(kw)
        if not d.get("source_chunk_ids"):
            d["source_chunk_ids"] = ["chunk_wf1"]
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
            name="WFCo",
            canonical_name="WFCo",
            ticker="WFCO",
            sector="Technology",
            first_seen_at="2024-01-01",
        ),
        _ent_row(
            entity_id=concept_id,
            entity_type="EconomicConcept",
            name="WFTheme",
            canonical_name="WFTheme",
            first_seen_at="2024-01-01",
        ),
    ]
    pq.write_table(pa.Table.from_pylist(entities), ddir / "entities.parquet")

    def _edge_row(**kw):
        d = {c: "" for c in EDGES_COLUMNS}
        d.update(kw)
        if not d.get("evidence_chunk_ids"):
            d["evidence_chunk_ids"] = ["chunk_wf1"]
        if not d.get("confidence"):
            d["confidence"] = "0.9"
        if not d.get("review_status"):
            d["review_status"] = "accepted"
        return d

    edges = [
        _edge_row(
            edge_id="edge_wf_1",
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
            {"entity_id": COMPANY_ID, "entity_type": "Company", "label": "WFCo", "attributes": {}},
            {"entity_id": concept_id, "entity_type": "EconomicConcept", "label": "WFTheme", "attributes": {}},
        ],
        "edges": [
            {
                "edge_id": "edge_wf_1",
                "source_entity_id": COMPANY_ID,
                "target_entity_id": concept_id,
                "edge_type": "exposed_to",
                "weight": 0.9,
                "evidence_chunk_ids": ["chunk_wf1"],
                "extraction_method": "document_stated",
            },
        ],
        "community_input_edges": ["edge_wf_1"],
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
                "edge_ids": ["edge_wf_1"],
                "size": 2,
                "density": 1.0,
                "top_entities": ["WFTheme"],
                "top_companies": ["WFCo"],
                "theme_name": "Walk-Forward Test Theme",
                "theme_summary": "Test theme for OI-1 walk-forward validation.",
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
                "theme_name": "Walk-Forward Test Theme",
                "summary": "Test theme.",
                "evidence_edge_ids": ["edge_wf_1"],
            }
        ],
    }
    (ddir / "theme_snapshots.json").write_text(json.dumps(snapshots_doc), encoding="utf-8")

    from theme_engine.exposure import EXPOSURE_COLUMNS

    exposure_row = {
        "schema_version": "1.0",
        "as_of_date": as_of_date,
        "company_id": COMPANY_ID,
        "ticker": "WFCO",
        "theme_snapshot_id": theme_snapshot_id,
        "community_id": community_id,
        "exposure_score": 0.80,
        "graph_distance": 1.0,
        "edge_confidence_sum": 0.9,
        "evidence_count": 1,
        "top_evidence_chunk_ids": ["chunk_wf1"],
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

    return COMPANY_ID, concept_id, community_id, theme_snapshot_id


def _make_price_row(
    company_id: str,
    price_date: str,
    close: float,
    adjusted_close: float,
    run_id: str = "",
    as_of_date: str = RUN_AS_OF,
    available_at: Optional[str] = None,
) -> dict:
    """Create a market_prices.parquet row."""
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
        "source": "test_wf",
        "source_id": None,
        "available_at": available_at,
        "created_at": _utcnow(),
    }


def _write_market_prices(run_id: str, rows: list[dict]) -> None:
    """Write market_prices.parquet to the run's validation/ directory."""
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


def _wf_prices_spanning_3_points(company_id: str, run_id: str = "") -> list[dict]:
    """Market prices spanning all 3 walk-forward as_of points with 3M forward window each.

    Updated for OI-7 (sweep.forward_window=3M): each point's window extends 3 months
    past its as_of date, so prices must cover the full 3M period for each point.

    Points:
      - 2024-03-31: entry 2024-04-01 @ 100, exit 2024-06-28 @ 105  (+5%)  window→2024-06-30
      - 2024-06-30: entry 2024-07-01 @ 110, exit 2024-09-30 @ 116  (+5.45%) window→2024-09-30
      - 2024-09-30: entry 2024-10-01 @ 120, exit 2024-12-31 @ 126  (+5%)  window→2024-12-31
    """
    return [
        # Point 1: 2024-03-31 (entry after, exit within 3M window [2024-06-30])
        _make_price_row(company_id, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(company_id, "2024-06-28", 105.0, 105.0, run_id=run_id),
        # Point 2: 2024-06-30 (entry after, exit within 3M window [2024-09-30])
        _make_price_row(company_id, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(company_id, "2024-09-30", 116.0, 116.0, run_id=run_id),
        # Point 3: 2024-09-30 (entry after, exit within 3M window [2024-12-31])
        _make_price_row(company_id, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(company_id, "2024-12-31", 126.0, 126.0, run_id=run_id),
    ]


# ---------------------------------------------------------------------------
# OI-6 R3a: per-point discovery seeding (parseable per-point artifacts)
# ---------------------------------------------------------------------------


def _ent(eid: str, etype: str, first_seen: str = "2024-01-01") -> dict:
    r = {c: "" for c in ENTITIES_COLUMNS}
    r.update(
        entity_id=eid, entity_type=etype, name=eid, canonical_name=eid,
        first_seen_at=first_seen, confidence="0.9",
        extraction_method="document_stated", review_status="accepted",
    )
    return r


def _edge(eid: str, src: str, tgt: str, etype: str = "exposed_to",
          first_seen: str = "2024-01-01", confidence: str = "0.9") -> dict:
    r = {c: "" for c in EDGES_COLUMNS}
    r.update(
        edge_id=eid, source_entity_id=src, target_entity_id=tgt,
        edge_type=etype, first_seen_at=first_seen, confidence=confidence,
        extraction_method="document_stated",
    )
    return r


def _seed_minimal_discovery_point(
    run_id: str,
    as_of: str,
    *,
    companies: list[str],
    concept: str = "ec_wf",
) -> None:
    """Write parseable per-point discovery into discovery/<as_of>/ (OI-6 R3a).

    Mirrors test_oi6_r2_panel_loop._seed_point: write entities/edges parquet then
    drive graph_build -> themes -> exposure for that point so the point has a real,
    distinct, parseable composition. Each company in ``companies`` is exposed to a
    shared concept; exposure rows carry company_id == entity_id.
    """
    ents = [_ent(c, "Company") for c in companies] + [_ent(concept, "EconomicConcept")]
    edges = [
        _edge(f"edge_{as_of}_{c}", c, concept, "exposed_to", first_seen=as_of)
        for c in companies
    ]
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    pq.write_table(pa.Table.from_pylist(ents), d / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), d / "edges.parquet")
    graph_build.build_graph(run_id, as_of=as_of)
    themes.discover_themes(run_id, as_of=as_of)
    exposure_mod.compute_exposure(run_id, as_of=as_of)
    # Upstream ingest artifacts are stubs here; freeze only requires their presence
    # (their bytes are hashed but not read by validation).
    for name in (
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entity_aliases.parquet",
    ):
        if not (d / name).exists():
            (d / name).write_bytes(b"stub")


# ---------------------------------------------------------------------------
# (1) Walk-forward panel: >= 3 monthly points, per-point data + pooled stats
# ---------------------------------------------------------------------------


def test_walk_forward_panel_computes_across_3_points():
    """run_walk_forward_validation computes a valid panel across 3 monthly as_of points.

    Asserts:
    - n_points == 3 (all points covered).
    - Each point has as_of, theme_basket_return, baseline_return, excess.
    - Pooled stats present: mean_excess (float), hit_rate (float in [0,1]).
    - success == True.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)
    prices = _wf_prices_spanning_3_points(COMPANY_ID, run_id=run_id)
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True, f"Expected success: {result}"
    assert result["n_points"] >= 3, (
        f"Expected n_points >= 3, got {result['n_points']}. "
        f"Points: {result.get('points')}"
    )

    # All 3 points should be valid (not skipped)
    valid = [p for p in result["points"] if p.get("excess") is not None]
    assert len(valid) >= 3, f"Expected >= 3 valid points, got {len(valid)}: {result['points']}"

    # Per-point structure
    for pt in valid:
        assert "as_of" in pt, f"Missing as_of in point: {pt}"
        assert pt.get("theme_basket_return") is not None, f"Missing theme_basket_return: {pt}"
        assert pt.get("baseline_return") is not None, f"Missing baseline_return: {pt}"
        assert pt.get("excess") is not None, f"Missing excess: {pt}"
        expected_excess = pt["theme_basket_return"] - pt["baseline_return"]
        assert abs(pt["excess"] - expected_excess) < 1e-9, (
            f"excess != theme_basket_return - baseline_return: {pt}"
        )

    # Pooled stats
    assert result.get("mean_excess") is not None, "mean_excess missing from panel result"
    assert result.get("hit_rate") is not None, "hit_rate missing from panel result"
    assert 0.0 <= result["hit_rate"] <= 1.0, f"hit_rate out of [0,1]: {result['hit_rate']}"

    # Consistency check: mean_excess matches manual calculation
    excesses = [p["excess"] for p in valid]
    expected_mean = sum(excesses) / len(excesses)
    assert abs(result["mean_excess"] - expected_mean) < 1e-9, (
        f"mean_excess {result['mean_excess']} != manual {expected_mean}"
    )

    # hit_rate consistency
    expected_hit_rate = sum(1 for e in excesses if e > 0) / len(excesses)
    assert abs(result["hit_rate"] - expected_hit_rate) < 1e-9, (
        f"hit_rate {result['hit_rate']} != manual {expected_hit_rate}"
    )


def test_walk_forward_panel_per_point_excess_correct():
    """Per-point excess is theme_basket_return - baseline_return for each as_of.

    With a single company universe, theme basket == universe, so excess should be
    approximately 0 (or exactly 0 when basket == baseline exactly).
    This test verifies the arithmetic, not that themes outperform.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)
    prices = _wf_prices_spanning_3_points(COMPANY_ID, run_id=run_id)
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    for pt in result["points"]:
        if pt.get("excess") is not None:
            computed = pt["theme_basket_return"] - pt["baseline_return"]
            assert abs(pt["excess"] - computed) < 1e-9, (
                f"Point {pt['as_of']}: excess arithmetic mismatch. "
                f"excess={pt['excess']}, theme-base={computed}"
            )


# ---------------------------------------------------------------------------
# (2) Single-snapshot: always illustrative=True / claim_supported=False
# ---------------------------------------------------------------------------


def test_single_snapshot_run_validation_always_illustrative():
    """run_validation() (single-snapshot) must always return illustrative=True, claim_supported=False.

    This is the OI-1 hard rule: no excess-return claim from a single cross-sectional draw.
    """
    run_id = _make_run("2024-06-30")
    _seed_minimal_discovery(run_id, "2024-06-30")
    # Provide prices so validation completes (not blocked)
    prices = [
        _make_price_row(COMPANY_ID, "2024-07-01", 100.0, 100.0, run_id=run_id, as_of_date="2024-06-30"),
        _make_price_row(COMPANY_ID, "2024-07-31", 110.0, 110.0, run_id=run_id, as_of_date="2024-06-30"),
        _make_price_row(COMPANY_ID, "2024-09-30", 115.0, 115.0, run_id=run_id, as_of_date="2024-06-30"),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_validation(run_id)

    assert result.get("illustrative") is True, (
        f"Single-snapshot run_validation must return illustrative=True. Got: {result.get('illustrative')!r}"
    )
    assert result.get("claim_supported") is False, (
        f"Single-snapshot run_validation must return claim_supported=False. Got: {result.get('claim_supported')!r}"
    )


def test_single_snapshot_blocked_also_illustrative():
    """run_validation() blocked by coverage must also return illustrative=True, claim_supported=False."""
    run_id = _make_run("2024-06-30")
    _seed_minimal_discovery(run_id, "2024-06-30")
    _write_market_prices(run_id, [])  # no prices -> blocked
    _freeze_run(run_id)

    result = run_validation(run_id)

    assert result.get("illustrative") is True, (
        f"Blocked single-snapshot must be illustrative=True. Got: {result.get('illustrative')!r}"
    )
    assert result.get("claim_supported") is False, (
        f"Blocked single-snapshot must have claim_supported=False. Got: {result.get('claim_supported')!r}"
    )


def test_single_snapshot_api_response_has_illustrative_flags():
    """API /api/validation/run for single-snapshot returns illustrative=True, claim_supported=False in JSON."""
    run_id = _make_run("2024-06-30")
    _seed_minimal_discovery(run_id, "2024-06-30")
    prices = [
        _make_price_row(COMPANY_ID, "2024-07-01", 100.0, 100.0, run_id=run_id, as_of_date="2024-06-30"),
        _make_price_row(COMPANY_ID, "2024-09-30", 115.0, 115.0, run_id=run_id, as_of_date="2024-06-30"),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body.get("illustrative") is True, (
        f"API single-snapshot must return illustrative=true. Got: {body.get('illustrative')!r}"
    )
    assert body.get("claim_supported") is False, (
        f"API single-snapshot must return claim_supported=false. Got: {body.get('claim_supported')!r}"
    )


# ---------------------------------------------------------------------------
# (3) Walk-forward: claim_supported=True when n_points >= min_points_for_claim
# ---------------------------------------------------------------------------


def test_walk_forward_claim_supported_with_3_valid_points():
    """Walk-forward panel with n_points >= min_points_for_claim emits claim_supported=True.

    The config's sweep.min_points_for_claim=3 and walk_forward.as_of_dates has 3 dates.
    With all 3 points covered by market_prices, claim_supported must be True.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)
    prices = _wf_prices_spanning_3_points(COMPANY_ID, run_id=run_id)
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["n_points"] >= result["min_points_for_claim"], (
        f"Expected n_points >= min_points_for_claim, got n_points={result['n_points']}, "
        f"min_points_for_claim={result['min_points_for_claim']}"
    )
    assert result.get("claim_supported") is True, (
        f"Expected claim_supported=True when n_points >= min_points_for_claim. "
        f"Got: {result.get('claim_supported')!r}. Full result: {result}"
    )
    assert result.get("illustrative") is False, (
        f"Expected illustrative=False when claim_supported=True. "
        f"Got: {result.get('illustrative')!r}"
    )


def test_walk_forward_claim_not_supported_with_insufficient_points():
    """Walk-forward with fewer valid points than min_points_for_claim stays illustrative.

    We provide prices only for 1 point (2024-09-30 onward), so only point 3 is valid.
    n_points=1 < min_points_for_claim=3 -> claim_supported=False, illustrative=True.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)
    # Only provide prices for point 3 (2024-09-30 window)
    prices = [
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["n_points"] < result["min_points_for_claim"], (
        f"Expected n_points < min_points_for_claim, got {result['n_points']} >= {result['min_points_for_claim']}"
    )
    assert result.get("claim_supported") is False, (
        f"Expected claim_supported=False with insufficient points. Got: {result.get('claim_supported')!r}"
    )
    assert result.get("illustrative") is True, (
        f"Expected illustrative=True with insufficient points. Got: {result.get('illustrative')!r}"
    )


# ---------------------------------------------------------------------------
# (4) Hard rule: no code path emits claim_supported=True from single snapshot
# ---------------------------------------------------------------------------


def test_hard_rule_single_snapshot_never_claim_supported():
    """HARD RULE: run_validation() (single-snapshot) NEVER returns claim_supported=True.

    This test is the proof: regardless of how many themes, how good the returns,
    or what benchmarks are configured, run_validation() must always have
    claim_supported=False. This guards against future regressions.
    """
    # Multiple scenarios tested
    scenarios = [
        ("2024-06-30", [  # good prices, validation completes
            _make_price_row(COMPANY_ID, "2024-07-01", 100.0, 100.0, as_of_date="2024-06-30"),
            _make_price_row(COMPANY_ID, "2024-07-31", 200.0, 200.0, as_of_date="2024-06-30"),  # 100% return
            _make_price_row(COMPANY_ID, "2024-09-30", 300.0, 300.0, as_of_date="2024-06-30"),
        ]),
        ("2024-06-30", []),  # blocked
    ]

    for as_of_date, price_rows in scenarios:
        run_id = _make_run(as_of_date)
        _seed_minimal_discovery(run_id, as_of_date)
        prices_with_run = [
            {**r, "run_id": run_id} for r in price_rows
        ]
        _write_market_prices(run_id, prices_with_run)
        _freeze_run(run_id)

        result = run_validation(run_id)

        # THE HARD RULE
        assert result.get("claim_supported") is not True, (
            f"HARD RULE VIOLATED: run_validation() returned claim_supported=True "
            f"for scenario as_of={as_of_date}. Result: {result}"
        )
        assert result.get("illustrative") is not False, (
            f"HARD RULE VIOLATED: run_validation() returned illustrative=False "
            f"for scenario as_of={as_of_date}. Result: {result}"
        )


# ---------------------------------------------------------------------------
# (5) PIT per point: forward window strictly after each point's as_of
# ---------------------------------------------------------------------------


def test_pit_per_point_prices_at_or_before_as_of_excluded():
    """PIT per point: prices at or before each walk-forward as_of date are excluded.

    For walk-forward point 2024-03-31:
    - price_date=2024-03-31 (AT as_of) must be excluded (not a valid entry price)
    - price_date=2024-04-01 (AFTER as_of) is the valid entry price
    - We can verify this by checking the per-point theme_basket_return uses
      the 2024-04-01 price as entry, not the 2024-03-31 price.

    This is verified by checking the computed return against expected.
    If the 2024-03-31 price (50.0) were used as entry, the return would be
    different from using 2024-04-01 (100.0) as entry.
    """
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    # Walk-forward point 2024-03-31:
    # - AT as_of price: 50.0 (should be EXCLUDED)
    # - Valid entry (day after): 100.0
    # - Valid exit (within 1M window ending 2024-04-30): 110.0
    # Expected return from valid prices: (110-100)/100 = 10%
    # If leak: entry=50, exit=110, return=(110-50)/50 = 120% — obviously wrong

    # Also provide prices for points 2 and 3 to make the full panel valid
    prices = [
        # Point 1 (2024-03-31): leak trap at as_of, valid entry/exit after
        _make_price_row(COMPANY_ID, "2024-03-31", 50.0, 50.0, run_id=run_id),   # AT as_of — must exclude
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id), # valid entry
        _make_price_row(COMPANY_ID, "2024-04-30", 110.0, 110.0, run_id=run_id), # valid exit
        # Point 2 (2024-06-30)
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-07-31", 115.0, 115.0, run_id=run_id),
        # Point 3 (2024-09-30)
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True

    # Find the point for 2024-03-31
    point1 = next((p for p in result["points"] if p["as_of"] == WF_POINT_1), None)
    assert point1 is not None, f"Point for {WF_POINT_1} not found in {result['points']}"
    assert point1.get("theme_basket_return") is not None, (
        f"Point 1 has no theme_basket_return: {point1}"
    )

    # Return must be ~10% (100->110), NOT ~120% (50->110)
    expected_return = (110.0 - 100.0) / 100.0  # 0.10
    assert abs(point1["theme_basket_return"] - expected_return) < 1e-6, (
        f"PIT VIOLATION at {WF_POINT_1}: expected return {expected_return:.4f} "
        f"(entry=100, exit=110), got {point1['theme_basket_return']:.4f}. "
        f"The price at as_of_date (50.0) was leaked as the entry price!"
    )


def test_pit_per_point_availability_guard_respected():
    """A restated price (available_at > price_date) is excluded per point's PIT discipline."""
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)

    prices = [
        # Point 1 (2024-03-31): restated entry (available_at in future) — must be excluded
        _make_price_row(
            COMPANY_ID, "2024-04-07", 200.0, 200.0, run_id=run_id,
            available_at="2024-08-01",  # restated: available_at > price_date -> exclude
        ),
        # Valid entry/exit for point 1
        _make_price_row(COMPANY_ID, "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-04-30", 110.0, 110.0, run_id=run_id),
        # Points 2 and 3
        _make_price_row(COMPANY_ID, "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-07-31", 115.0, 115.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-01", 120.0, 120.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-10-31", 126.0, 126.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True

    point1 = next((p for p in result["points"] if p["as_of"] == WF_POINT_1), None)
    assert point1 is not None
    assert point1.get("theme_basket_return") is not None

    # Return must be ~10% (100->110), NOT affected by the restated 200.0 row
    expected = (110.0 - 100.0) / 100.0  # 0.10
    assert abs(point1["theme_basket_return"] - expected) < 1e-6, (
        f"Restated price (available_at > price_date) leaked into point 1 return. "
        f"Expected {expected:.4f}, got {point1['theme_basket_return']:.4f}"
    )


# ---------------------------------------------------------------------------
# Additional: freeze gate respected by walk-forward
# ---------------------------------------------------------------------------


def test_walk_forward_requires_freeze_gate():
    """run_walk_forward_validation must raise PermissionError for unfrozen run."""
    run_id = _make_run(RUN_AS_OF)
    # Do NOT freeze

    with pytest.raises(PermissionError):
        run_walk_forward_validation(run_id)


def test_walk_forward_multipoint_run_evaluates_per_point():
    """OI-6 R3a: a multi-point authored run is evaluated genuinely per-point.

    Each authored point t_i is scored against its OWN frozen discovery/<t_i>/
    basket over the 3M forward window. No deferral; success=True; per-point
    results; claim_supported gated on n_points >= min_points_for_claim.
    """
    t1, t2, t3 = WF_POINT_1, WF_POINT_2, WF_POINT_3
    run = runs.create_run(RunCreateRequest(as_of_date=t3, as_of_dates=[t1, t2, t3]))
    run_id = run.run_id

    for as_of in (t1, t2, t3):
        _seed_minimal_discovery_point(run_id, as_of, companies=["c1"])
    _write_market_prices(run_id, _wf_prices_spanning_3_points("c1", run_id=run_id))
    runs.freeze_all_points(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True, f"Expected success: {result}"
    assert "R2" not in (result.get("message") or "")
    covered = [p for p in result["points"] if p.get("covered")]
    assert len(covered) == 3, f"Expected 3 covered points: {result['points']}"
    valid = [p for p in result["points"] if p.get("excess") is not None]
    assert result["n_points"] == len(valid)
    assert {p["as_of"] for p in result["points"]} == {t1, t2, t3}
    assert result["claim_supported"] == (
        result["n_points"] >= result["min_points_for_claim"]
    )


def test_multipoint_authored_run_oos_panel_uses_per_point_discovery():
    """3-point run, all covered: per-point OOS panel + artifact with schema."""
    t1, t2, t3 = WF_POINT_1, WF_POINT_2, WF_POINT_3
    run = runs.create_run(RunCreateRequest(as_of_date=t3, as_of_dates=[t1, t2, t3]))
    run_id = run.run_id

    for as_of in (t1, t2, t3):
        _seed_minimal_discovery_point(run_id, as_of, companies=["c1"])
    _write_market_prices(run_id, _wf_prices_spanning_3_points("c1", run_id=run_id))
    runs.freeze_all_points(run_id)

    result = run_walk_forward_validation(run_id)

    assert result["success"] is True
    assert result["n_points"] >= 3
    assert {p["as_of"] for p in result["points"]} == {t1, t2, t3}

    valid = [p for p in result["points"] if p.get("excess") is not None]
    for pt in valid:
        assert pt["as_of"] in {t1, t2, t3}
        assert pt["basket_return"] is not None
        assert pt["baseline_return"] is not None
        expected_excess = pt["basket_return"] - pt["baseline_return"]
        assert abs(pt["excess"] - expected_excess) < 1e-9

    # Pooled stats arithmetically consistent.
    excesses = [p["excess"] for p in valid]
    assert abs(result["mean_excess"] - sum(excesses) / len(excesses)) < 1e-9
    expected_hit = sum(1 for e in excesses if e > 0) / len(excesses)
    assert abs(result["hit_rate"] - expected_hit) < 1e-9

    # Panel artifact exists with documented schema.
    panel_path = runs.panel_dir(run_id) / VALIDATION_PANEL_FILENAME
    assert panel_path.exists(), "validation_panel.json not written"
    assert result["panel_artifact"] == f"panel/{VALIDATION_PANEL_FILENAME}"
    doc = json.loads(panel_path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == VALIDATION_PANEL_SCHEMA_VERSION
    assert doc["run_id"] == run_id
    assert doc["forward_window"] == "3M"
    assert doc["coverage_policy"] == "skip"
    assert doc["min_points_for_claim"] >= 3
    assert doc["n_points"] == result["n_points"]
    assert doc["n_points_authored"] == 3
    assert doc["claim_supported"] == result["claim_supported"]
    assert doc["illustrative"] == result["illustrative"]
    assert "generated_at" in doc
    assert len(doc["points"]) == 3
    for pt in doc["points"]:
        for key in ("as_of", "window_end", "basket_return", "baseline_return",
                    "excess", "covered", "skipped", "skipped_reason"):
            assert key in pt, f"panel point missing {key}: {pt}"


def test_multipoint_each_point_uses_its_own_discovery_not_latest():
    """Anti-leakage: T1's basket derives from T1's company, T2's from T2's.

    T1 discovery has company A only (prices only in T1's window); T2 discovery has
    company B only (prices only in T2's window). If T1 had (wrongly) used the
    latest (T2) basket of company B, T1's return would be None (B has no price in
    T1's window). Proves no cross-point bleed.
    """
    t1, t2 = WF_POINT_1, WF_POINT_2
    run = runs.create_run(RunCreateRequest(as_of_date=t2, as_of_dates=[t1, t2]))
    run_id = run.run_id

    _seed_minimal_discovery_point(run_id, t1, companies=["companyA"])
    _seed_minimal_discovery_point(run_id, t2, companies=["companyB"])

    prices = [
        # company A: only inside T1's window [>2024-03-31, <=2024-06-30]
        _make_price_row("companyA", "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row("companyA", "2024-06-28", 105.0, 105.0, run_id=run_id),
        # company B: only inside T2's window [>2024-06-30, <=2024-09-30]
        _make_price_row("companyB", "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row("companyB", "2024-09-30", 116.0, 116.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    runs.freeze_all_points(run_id)

    result = run_walk_forward_validation(run_id)
    assert result["success"] is True

    p1 = next(p for p in result["points"] if p["as_of"] == t1)
    p2 = next(p for p in result["points"] if p["as_of"] == t2)

    # T1 basket return derives from company A (100 -> 105 = +5%).
    assert p1["basket_return"] is not None
    assert abs(p1["basket_return"] - (105.0 - 100.0) / 100.0) < 1e-6
    # T2 basket return derives from company B (110 -> 116 ≈ +5.45%).
    assert p2["basket_return"] is not None
    assert abs(p2["basket_return"] - (116.0 - 110.0) / 110.0) < 1e-6
    # The two returns differ -> each point used its own discovery, not a shared one.
    assert p1["basket_return"] != p2["basket_return"]

    # Counter-proof: company B (the latest basket) has NO price in T1's window,
    # so substituting it for T1 would have yielded None, not +5%.
    win_end_t1 = _add_months(date.fromisoformat(t1), 3)
    ret_if_leaked, _, _, _ = _compute_basket_return(
        ["companyB"], {"companyB": 1.0}, prices, date.fromisoformat(t1), win_end_t1
    )
    assert ret_if_leaked is None


def test_multipoint_point_with_insufficient_forward_coverage_is_skipped():
    """3-point run where T3's [T3, T3+3M] window lacks prices -> T3 skipped.

    skip-not-shrink: T3 has covered=False/excess=None with an
    insufficient_forward_coverage reason and is excluded from n_points; T1/T2 valid.
    """
    t1, t2, t3 = WF_POINT_1, WF_POINT_2, WF_POINT_3
    run = runs.create_run(RunCreateRequest(as_of_date=t3, as_of_dates=[t1, t2, t3]))
    run_id = run.run_id

    for as_of in (t1, t2, t3):
        _seed_minimal_discovery_point(run_id, as_of, companies=["c1"])

    # Prices cover T1 and T2 windows but stop at 2024-09-30, so T3's window
    # (ending 2024-12-31) is NOT covered.
    prices = [
        _make_price_row("c1", "2024-04-01", 100.0, 100.0, run_id=run_id),
        _make_price_row("c1", "2024-06-28", 105.0, 105.0, run_id=run_id),
        _make_price_row("c1", "2024-07-01", 110.0, 110.0, run_id=run_id),
        _make_price_row("c1", "2024-09-30", 116.0, 116.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    runs.freeze_all_points(run_id)

    result = run_walk_forward_validation(run_id)
    assert result["success"] is True
    assert result["n_points"] == 2, f"Expected 2 valid points: {result['points']}"

    p3 = next(p for p in result["points"] if p["as_of"] == t3)
    assert p3["covered"] is False
    assert p3["excess"] is None
    assert "insufficient_forward_coverage" in (p3["skipped_reason"] or "")
    # skip-not-shrink: T3 window_end stays at +3M (never clamped to coverage).
    assert p3["window_end"] == _add_months(date.fromisoformat(t3), 3).isoformat()

    for pt in (t1, t2):
        p = next(x for x in result["points"] if x["as_of"] == pt)
        assert p["excess"] is not None


def test_multipoint_claim_supported_iff_n_points_ge_min():
    """claim_supported is True iff n_points >= min_points_for_claim (>=3 floor)."""
    # Setup A: 3 covered points -> claim_supported True, illustrative False.
    t1, t2, t3 = WF_POINT_1, WF_POINT_2, WF_POINT_3
    run_a = runs.create_run(RunCreateRequest(as_of_date=t3, as_of_dates=[t1, t2, t3]))
    rid_a = run_a.run_id
    for as_of in (t1, t2, t3):
        _seed_minimal_discovery_point(rid_a, as_of, companies=["c1"])
    _write_market_prices(rid_a, _wf_prices_spanning_3_points("c1", run_id=rid_a))
    runs.freeze_all_points(rid_a)

    res_a = run_walk_forward_validation(rid_a)
    assert res_a["n_points"] == 3
    assert res_a["claim_supported"] is True
    assert res_a["illustrative"] is False

    # Setup B: 2 covered points, min=3 -> claim_supported False, illustrative True.
    run_b = runs.create_run(RunCreateRequest(as_of_date=t2, as_of_dates=[t1, t2]))
    rid_b = run_b.run_id
    for as_of in (t1, t2):
        _seed_minimal_discovery_point(rid_b, as_of, companies=["c1"])
    prices_b = [
        _make_price_row("c1", "2024-04-01", 100.0, 100.0, run_id=rid_b),
        _make_price_row("c1", "2024-06-28", 105.0, 105.0, run_id=rid_b),
        _make_price_row("c1", "2024-07-01", 110.0, 110.0, run_id=rid_b),
        _make_price_row("c1", "2024-09-30", 116.0, 116.0, run_id=rid_b),
    ]
    _write_market_prices(rid_b, prices_b)
    runs.freeze_all_points(rid_b)

    res_b = run_walk_forward_validation(rid_b)
    assert res_b["n_points"] == 2
    assert res_b["min_points_for_claim"] >= 3
    assert res_b["claim_supported"] is False
    assert res_b["illustrative"] is True


def test_multipoint_unfrozen_or_partial_freeze_rejected():
    """Partial freeze (only T1 of a 2-point run) -> PermissionError.

    Run-level discovery_frozen stays False until every authored point is frozen
    (runs.py:377-380), so validate_ready_for_validation rejects the run.
    """
    t1, t2 = WF_POINT_1, WF_POINT_2
    run = runs.create_run(RunCreateRequest(as_of_date=t2, as_of_dates=[t1, t2]))
    run_id = run.run_id

    _seed_minimal_discovery_point(run_id, t1, companies=["c1"])
    _seed_minimal_discovery_point(run_id, t2, companies=["c1"])
    # Freeze ONLY T1.
    freeze_mod.freeze_discovery(run_id, as_of=t1)

    with pytest.raises(PermissionError):
        run_walk_forward_validation(run_id)


def test_walk_forward_with_no_as_of_dates_returns_failure():
    """run_walk_forward_validation with empty as_of_dates returns illustrative=True, claim_supported=False.

    Tests the edge case where the config has no walk_forward.as_of_dates.
    We test this by directly calling the function with a run whose config
    would fall back to defaults — but since the default config HAS dates,
    we rely on the n_points check: 0 valid points < min_points_for_claim.
    """
    # This is tested implicitly via test_walk_forward_claim_not_supported_with_insufficient_points
    # but here we also verify the edge case of no valid points
    run_id = _make_run(RUN_AS_OF)
    _seed_minimal_discovery(run_id, RUN_AS_OF)
    # Write prices that cover NONE of the 3 walk-forward points
    # (prices only before first as_of_date)
    prices = [
        _make_price_row(COMPANY_ID, "2024-01-15", 90.0, 90.0, run_id=run_id),
        _make_price_row(COMPANY_ID, "2024-02-15", 92.0, 92.0, run_id=run_id),
    ]
    _write_market_prices(run_id, prices)
    _freeze_run(run_id)

    result = run_walk_forward_validation(run_id)

    # No valid points (all skipped due to insufficient coverage)
    assert result["n_points"] == 0, (
        f"Expected n_points=0 when no points have coverage, got {result['n_points']}"
    )
    assert result.get("claim_supported") is False, (
        f"claim_supported must be False with 0 valid points. Got: {result.get('claim_supported')!r}"
    )
    assert result.get("illustrative") is True, (
        f"illustrative must be True with 0 valid points. Got: {result.get('illustrative')!r}"
    )
