"""FI-E: Projection validation / scoring pass — hermetic tests (GitHub #108).

Workstream P-E per docs/design_forward_inference.md.

Acceptance criteria:
  (1) Hit-rate correct: a clearly-right projection (direction matches realized
      return sign) scores a hit; a clearly-wrong one scores a miss.
  (2) Rank correlation: higher-strength projections correlate with larger
      absolute realized returns (basic ordinal check).
  (3) Null handling: hit is None when realized_return == 0 or no price data.
  (4) POST-FREEZE gate: score_projections() raises PermissionError for an
      unfrozen run.
  (5) LEAKAGE TEST — one-way gate:
        (a) propagation.py does NOT import projection_scorer.
        (b) projected_impacts.py does NOT import projection_scorer.
        (c) projection_scorer does NOT import propagation or projected_impacts.
  (6) READS_ONLY: projection_scorer reads only projected_impacts (discovery,
      read-only) + market_prices (validation/); it writes only to validation/.
  (7) Empty-but-schema-valid artifact when no projected impacts exist.
  (8) Coverage gate: windows without sufficient forward prices are skipped.
  (9) Spearman rank correlation helper unit tests.

No network calls; no LLM calls; all data is synthetic in-process or written
to a temporary run directory (conftest.py redirects RUN_OUTPUT_DIR).
"""

from __future__ import annotations

import importlib
import json
import types
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine.projection_scorer import (
    PROJECTION_SCORES_COLUMNS,
    SCHEMA_VERSION,
    SCORER_METHOD,
    _compute_hit,
    _realized_return_for_company,
    _spearman_rho,
    _rank_list,
    score_projections,
)
from theme_engine import runs as _runs


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

AS_OF = "2024-06-30"
AS_OF_DATE = date.fromisoformat(AS_OF)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run(
    as_of_date: str = AS_OF,
    frozen: bool = False,
    impact_rows: Optional[list[dict]] = None,
    price_rows: Optional[list[dict]] = None,
) -> str:
    """Create a minimal run directory wired for projection scoring tests.

    Parameters
    ----------
    frozen : bool
        Whether to mark discovery_frozen=True in the manifest.
    impact_rows : list[dict] | None
        Rows for projected_impacts.parquet.  None → empty table.
    price_rows : list[dict] | None
        Rows for market_prices.parquet.  None → empty table.
    """
    run_id = f"fi_e_test_{uuid.uuid4().hex[:8]}"
    run_dir = _runs.settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    validation = run_dir / "validation"
    discovery.mkdir(parents=True, exist_ok=True)
    validation.mkdir(parents=True, exist_ok=True)

    # Minimal run manifest
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "as_of_date": as_of_date,
        "created_at": _utcnow(),
        "code_version": "test",
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "input_hash": "test_hash",
        "model_config_hash": None,
        "sweep_id": None,
        "sweep_parent_id": None,
        "validation_mode": "single_snapshot",
        "sweep_position": None,
        "discovery_artifact_hashes": None,
        "discovery_frozen": frozen,
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    # Write projected_impacts.parquet
    _write_projected_impacts(discovery / "projected_impacts.parquet", impact_rows or [])

    # Write market_prices.parquet
    _write_market_prices(validation / "market_prices.parquet", price_rows or [])

    return run_id


def _write_projected_impacts(path: Path, rows: list[dict]) -> None:
    """Write projected_impacts.parquet with the FI-C schema."""
    from theme_engine.projected_impacts import _parquet_schema, PROJECTED_IMPACTS_COLUMNS
    if not rows:
        schema = _parquet_schema()
        empty = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), path)
        return
    # Write via pydict — fill optional list fields with empty lists
    arrays: dict[str, pa.Array] = {}
    for col in PROJECTED_IMPACTS_COLUMNS:
        values = [row.get(col) for row in rows]
        if col == "direction":
            arrays[col] = pa.array(values, type=pa.int32())
        elif col in {"strength", "confidence"}:
            arrays[col] = pa.array(
                [float(v) if v is not None else None for v in values],
                type=pa.float64(),
            )
        elif col in {"path", "contributing_edge_ids", "evidence_chunk_ids"}:
            arrays[col] = pa.array(
                [v if v is not None else [] for v in values],
                type=pa.list_(pa.string()),
            )
        else:
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )
    pq.write_table(pa.table(arrays), path)


def _write_market_prices(path: Path, rows: list[dict]) -> None:
    """Write market_prices.parquet with the §19 schema."""
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
        pq.write_table(pa.table(empty, schema=schema), path)
        return
    pq.write_table(pa.Table.from_pylist(rows), path)


def _price_row(
    company_id: str,
    price_date: str,
    adjusted_close: float,
    available_at: Optional[str] = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "",
        "as_of_date": AS_OF,
        "company_id": company_id,
        "ticker": None,
        "price_date": price_date,
        "close": adjusted_close,
        "adjusted_close": adjusted_close,
        "currency": "USD",
        "source": "test",
        "source_id": None,
        "available_at": available_at or price_date,
        "created_at": _utcnow(),
    }


def _impact_row(
    trigger_id: str,
    company_id: str,
    direction: int,
    strength: float,
    as_of_date: str = AS_OF,
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "test",
        "as_of_date": as_of_date,
        "trigger_id": trigger_id,
        "trigger_kind": "Event",
        "company_id": company_id,
        "direction": direction,
        "strength": strength,
        "path": ["e1"],
        "contributing_edge_ids": ["e1"],
        "evidence_chunk_ids": ["c1"],
        "confidence": 0.8,
        "method": "propagation_v1_event_trigger",
    }


# ---------------------------------------------------------------------------
# (9) Spearman / rank helpers — unit tests
# ---------------------------------------------------------------------------


class TestSpearmanHelpers:
    """Pure unit tests for _rank_list and _spearman_rho."""

    def test_rank_simple_ascending(self):
        r = _rank_list([10.0, 20.0, 30.0])
        assert r == [1.0, 2.0, 3.0]

    def test_rank_with_ties(self):
        # [10, 10, 30] -> ranks 1.5, 1.5, 3
        r = _rank_list([10.0, 10.0, 30.0])
        assert r[0] == r[1] == 1.5
        assert r[2] == 3.0

    def test_rank_descending_values(self):
        r = _rank_list([30.0, 20.0, 10.0])
        assert r == [3.0, 2.0, 1.0]

    def test_spearman_perfect_positive(self):
        rho = _spearman_rho([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        assert rho is not None
        assert abs(rho - 1.0) < 1e-9

    def test_spearman_perfect_negative(self):
        rho = _spearman_rho([1.0, 2.0, 3.0], [30.0, 20.0, 10.0])
        assert rho is not None
        assert abs(rho - (-1.0)) < 1e-9

    def test_spearman_none_for_n_lt_2(self):
        assert _spearman_rho([1.0], [2.0]) is None
        assert _spearman_rho([], []) is None

    def test_spearman_none_for_zero_variance(self):
        # All same rank -> zero variance -> None
        rho = _spearman_rho([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])
        assert rho is None


# ---------------------------------------------------------------------------
# (3) _compute_hit unit tests
# ---------------------------------------------------------------------------


class TestComputeHit:
    """_compute_hit returns 1, 0, or None correctly."""

    def test_hit_when_direction_positive_return_positive(self):
        assert _compute_hit(+1, 0.10) == 1

    def test_hit_when_direction_negative_return_negative(self):
        assert _compute_hit(-1, -0.05) == 1

    def test_miss_when_direction_positive_return_negative(self):
        assert _compute_hit(+1, -0.05) == 0

    def test_miss_when_direction_negative_return_positive(self):
        assert _compute_hit(-1, 0.10) == 0

    def test_none_when_realized_return_is_zero(self):
        assert _compute_hit(+1, 0.0) is None
        assert _compute_hit(-1, 0.0) is None

    def test_none_when_realized_return_is_none(self):
        assert _compute_hit(+1, None) is None
        assert _compute_hit(-1, None) is None


# ---------------------------------------------------------------------------
# _realized_return_for_company unit tests
# ---------------------------------------------------------------------------


class TestRealizedReturn:
    """_realized_return_for_company reuses validation leakage filter correctly."""

    def test_basic_return(self):
        # 1M window from 2024-06-30 ends on 2024-07-30 (inclusive)
        rows = [
            _price_row("CO1", "2024-07-01", 100.0),  # entry
            _price_row("CO1", "2024-07-30", 110.0),  # exit (last day of 1M window)
        ]
        ret = _realized_return_for_company(
            "CO1", rows, AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is not None
        assert abs(ret - 0.10) < 1e-9

    def test_price_at_as_of_excluded(self):
        """Price on as_of_date itself must NOT be used as entry (leak)."""
        rows = [
            _price_row("CO1", "2024-06-30", 50.0),   # AT as_of — excluded
            _price_row("CO1", "2024-07-01", 100.0),  # entry
            _price_row("CO1", "2024-07-30", 110.0),  # exit (within 1M window)
        ]
        ret = _realized_return_for_company(
            "CO1", rows, AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is not None
        # Entry = 100, exit = 110 => 10%; NOT (110-50)/50 = 120%
        assert abs(ret - 0.10) < 1e-9

    def test_price_before_as_of_excluded(self):
        """Prices before as_of_date must not contribute."""
        rows = [
            _price_row("CO1", "2024-06-20", 200.0),  # BEFORE as_of — excluded
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 120.0),
        ]
        ret = _realized_return_for_company(
            "CO1", rows, AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is not None
        assert abs(ret - 0.20) < 1e-9

    def test_restated_row_excluded(self):
        """Row with available_at > price_date is excluded (restatement guard)."""
        rows = [
            _price_row("CO1", "2024-07-15", 200.0,
                       available_at="2024-09-01"),  # restated — excluded
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 115.0),
        ]
        ret = _realized_return_for_company(
            "CO1", rows, AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is not None
        assert abs(ret - 0.15) < 1e-9

    def test_no_prices_returns_none(self):
        ret = _realized_return_for_company(
            "CO1", [], AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is None

    def test_wrong_company_returns_none(self):
        rows = [_price_row("CO2", "2024-07-01", 100.0)]
        ret = _realized_return_for_company(
            "CO1", rows, AS_OF_DATE, date(2024, 7, 30)
        )
        assert ret is None


# ---------------------------------------------------------------------------
# (1) Hit-rate correctness — the core acceptance criterion
# ---------------------------------------------------------------------------


class TestHitRateCorrectness:
    """A clearly-right projection scores a hit; a clearly-wrong one scores a miss."""

    def test_clearly_right_projection_scores_hit(self):
        """Projected direction=+1, realized return is clearly positive -> hit."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        # Entry 100, exit 120 -> +20% return -> sign matches direction +1 -> hit=1
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 120.0),
            _price_row("CO1", "2024-09-30", 125.0),  # coverage
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        result = score_projections(run_id)
        assert result["success"] is True
        assert result["scored_rows"] > 0

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        assert len(rows) >= 1

        # Find the row for CO1 in the 1M window
        co1_rows = [r for r in rows if r["company_id"] == "CO1" and r["forward_window"] == "1M"]
        assert co1_rows, "No 1M row found for CO1"
        row = co1_rows[0]
        assert row["hit"] == 1, (
            f"Expected hit=1 for clearly-right projection, got hit={row['hit']!r}, "
            f"realized_return={row['realized_return']!r}"
        )

    def test_clearly_wrong_projection_scores_miss(self):
        """Projected direction=+1 but realized return is clearly negative -> miss."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        # Entry 100, exit 80 -> -20% return -> sign contradicts direction +1 -> hit=0
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 80.0),
            _price_row("CO1", "2024-09-30", 75.0),  # coverage
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        result = score_projections(run_id)
        assert result["success"] is True

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        co1_rows = [r for r in rows if r["company_id"] == "CO1" and r["forward_window"] == "1M"]
        assert co1_rows, "No 1M row found for CO1"
        row = co1_rows[0]
        assert row["hit"] == 0, (
            f"Expected hit=0 for clearly-wrong projection, got hit={row['hit']!r}, "
            f"realized_return={row['realized_return']!r}"
        )

    def test_negative_direction_hit(self):
        """Projected direction=-1 with negative realized return -> hit."""
        impacts = [_impact_row("EV1", "CO1", direction=-1, strength=0.6)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 85.0),   # -15% return
            _price_row("CO1", "2024-09-30", 80.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        co1_rows = [r for r in rows if r["company_id"] == "CO1" and r["forward_window"] == "1M"]
        assert co1_rows
        assert co1_rows[0]["hit"] == 1

    def test_hit_rate_by_trigger_all_hits(self):
        """hit_rate_by_trigger == 1.0 when all projections for a trigger are hits."""
        impacts = [
            _impact_row("EV1", "CO1", direction=+1, strength=0.9),
            _impact_row("EV1", "CO2", direction=+1, strength=0.6),
        ]
        # Both companies rise over the 1M window
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 115.0),
            _price_row("CO2", "2024-07-01", 200.0),
            _price_row("CO2", "2024-07-30", 220.0),
            _price_row("CO1", "2024-09-30", 120.0),
            _price_row("CO2", "2024-09-30", 230.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        window_rows = [r for r in rows if r["forward_window"] == "1M" and r["trigger_id"] == "EV1"]
        assert window_rows, "No 1M rows for EV1"
        # All hits -> hit_rate == 1.0
        hit_rates = {r["hit_rate_by_trigger"] for r in window_rows}
        assert len(hit_rates) == 1, f"Expected uniform hit_rate, got: {hit_rates}"
        assert abs(list(hit_rates)[0] - 1.0) < 1e-9

    def test_hit_rate_by_trigger_half_hits(self):
        """hit_rate == 0.5 when exactly half of projections are hits."""
        impacts = [
            _impact_row("EV1", "CO1", direction=+1, strength=0.9),  # will be hit
            _impact_row("EV1", "CO2", direction=+1, strength=0.5),  # will be miss
        ]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),  # +10% -> hit for +1
            _price_row("CO2", "2024-07-01", 100.0),
            _price_row("CO2", "2024-07-30", 90.0),   # -10% -> miss for +1
            _price_row("CO1", "2024-09-30", 115.0),
            _price_row("CO2", "2024-09-30", 85.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        window_rows = [r for r in rows if r["forward_window"] == "1M" and r["trigger_id"] == "EV1"]
        assert window_rows
        hit_rates = {r["hit_rate_by_trigger"] for r in window_rows}
        assert len(hit_rates) == 1
        assert abs(list(hit_rates)[0] - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# (2) Rank correlation
# ---------------------------------------------------------------------------


class TestRankCorrelation:
    """rank_corr_by_trigger captures ordinal strength vs |realized_return|."""

    def test_rank_corr_positive_when_stronger_implies_larger_return(self):
        """Higher strength -> larger |realized_return| yields positive Spearman."""
        impacts = [
            _impact_row("EV1", "CO1", direction=+1, strength=0.9),  # strong
            _impact_row("EV1", "CO2", direction=+1, strength=0.4),  # weak
        ]
        prices = [
            # CO1 (strong): +30% return
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 130.0),
            # CO2 (weak): +5% return
            _price_row("CO2", "2024-07-01", 100.0),
            _price_row("CO2", "2024-07-30", 105.0),
            # coverage for 3M window
            _price_row("CO1", "2024-09-30", 135.0),
            _price_row("CO2", "2024-09-30", 106.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        window_rows = [r for r in rows if r["forward_window"] == "1M" and r["trigger_id"] == "EV1"]
        assert window_rows, "No 1M rows for EV1"
        rank_corrs = {r["rank_corr_by_trigger"] for r in window_rows}
        assert len(rank_corrs) == 1
        rho = list(rank_corrs)[0]
        assert rho is not None
        assert rho > 0, f"Expected positive Spearman, got {rho}"

    def test_rank_corr_none_when_only_one_data_point(self):
        """Spearman is undefined for a single data point -> rank_corr is null."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        window_rows = [r for r in rows if r["forward_window"] == "1M"]
        assert window_rows
        # Only 1 data point -> None
        for r in window_rows:
            assert r["rank_corr_by_trigger"] is None, (
                f"Expected null rank_corr for single data point, got {r['rank_corr_by_trigger']}"
            )


# ---------------------------------------------------------------------------
# (4) POST-FREEZE gate
# ---------------------------------------------------------------------------


class TestFreezeGate:
    """score_projections() raises PermissionError for an unfrozen run."""

    def test_unfrozen_run_raises_permission_error(self):
        run_id = _make_run(frozen=False)
        with pytest.raises(PermissionError, match="frozen"):
            score_projections(run_id)

    def test_frozen_run_does_not_raise(self):
        run_id = _make_run(
            frozen=True,
            impact_rows=[],
            price_rows=[],
        )
        # Should not raise; may produce an empty result
        result = score_projections(run_id)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# (5) LEAKAGE TEST — one-way gate
# ---------------------------------------------------------------------------


class TestLeakageOneWayGate:
    """Verify no feedback path between scorer and discovery modules."""

    def test_propagation_does_not_import_projection_scorer(self):
        """propagation.py must NOT import projection_scorer (one-way gate)."""
        import importlib.util
        import sys

        # Freshly load propagation (not the cached sys.modules version)
        spec = importlib.util.find_spec("theme_engine.propagation")
        assert spec is not None, "theme_engine.propagation not found"

        # Inspect its source for any import of projection_scorer
        source = Path(spec.origin).read_text(encoding="utf-8")
        forbidden_patterns = [
            "projection_scorer",
            "from .projection_scorer",
            "import projection_scorer",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"LEAKAGE: propagation.py imports '{pattern}'. "
                "One-way gate violated: scorer must not be reachable from discovery."
            )

    def test_projected_impacts_does_not_import_projection_scorer(self):
        """projected_impacts.py must NOT import projection_scorer (one-way gate)."""
        spec = importlib.util.find_spec("theme_engine.projected_impacts")
        assert spec is not None, "theme_engine.projected_impacts not found"

        source = Path(spec.origin).read_text(encoding="utf-8")
        forbidden_patterns = [
            "projection_scorer",
            "from .projection_scorer",
            "import projection_scorer",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"LEAKAGE: projected_impacts.py imports '{pattern}'. "
                "One-way gate violated: scorer must not be reachable from discovery."
            )

    def test_projection_scorer_does_not_import_propagation(self):
        """projection_scorer.py must NOT import propagation (no circular dep)."""
        spec = importlib.util.find_spec("theme_engine.projection_scorer")
        assert spec is not None, "theme_engine.projection_scorer not found"

        source = Path(spec.origin).read_text(encoding="utf-8")
        for pattern in ("from .propagation", "import propagation",
                        "from theme_engine.propagation"):
            assert pattern not in source, (
                f"projection_scorer imports propagation ({pattern!r}). "
                "This creates a discovery dependency from the scorer."
            )

    def test_projection_scorer_does_not_import_projected_impacts(self):
        """projection_scorer.py must NOT import projected_impacts (one-way)."""
        spec = importlib.util.find_spec("theme_engine.projection_scorer")
        assert spec is not None

        source = Path(spec.origin).read_text(encoding="utf-8")
        for pattern in ("from .projected_impacts", "import projected_impacts",
                        "from theme_engine.projected_impacts"):
            assert pattern not in source, (
                f"projection_scorer imports projected_impacts ({pattern!r}). "
                "One-way gate violated."
            )

    def test_scorer_only_writes_to_validation_dir(self):
        """score_projections() only writes artifacts to the validation/ directory."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        run_dir = _runs.settings.run_output_dir / run_id

        # Snapshot discovery/ contents before scoring
        discovery_before = set(
            p.name for p in (run_dir / "discovery").rglob("*") if p.is_file()
        )

        score_projections(run_id)

        # Discovery directory must be unchanged
        discovery_after = set(
            p.name for p in (run_dir / "discovery").rglob("*") if p.is_file()
        )
        assert discovery_before == discovery_after, (
            f"LEAKAGE: scorer modified discovery/ contents.\n"
            f"  Before: {sorted(discovery_before)}\n"
            f"  After:  {sorted(discovery_after)}"
        )

        # Validation artifact must exist
        assert (run_dir / "validation" / "projection_scores.parquet").exists()


# ---------------------------------------------------------------------------
# (6) READS_ONLY: artifact sources
# ---------------------------------------------------------------------------


class TestReadsOnly:
    """projection_scorer reads only projected_impacts + market_prices."""

    def test_scorer_reads_projected_impacts(self):
        """Modifying projected_impacts changes the output (it is actually read)."""
        # Run 1: direction=+1 for CO1
        impacts_a = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_a = _make_run(frozen=True, impact_rows=impacts_a, price_rows=prices)
        score_projections(run_a)
        rows_a = pq.read_table(
            _runs.settings.run_output_dir / run_a / "validation" / "projection_scores.parquet"
        ).to_pylist()

        # Run 2: direction=-1 for CO1 (same prices but opposite direction)
        impacts_b = [_impact_row("EV1", "CO1", direction=-1, strength=0.8)]
        run_b = _make_run(frozen=True, impact_rows=impacts_b, price_rows=prices)
        score_projections(run_b)
        rows_b = pq.read_table(
            _runs.settings.run_output_dir / run_b / "validation" / "projection_scores.parquet"
        ).to_pylist()

        # Hits should differ: run_a has hit=1 (rise matched +1), run_b has hit=0 (rise contradicts -1)
        hits_a = [r["hit"] for r in rows_a if r["forward_window"] == "1M" and r["company_id"] == "CO1"]
        hits_b = [r["hit"] for r in rows_b if r["forward_window"] == "1M" and r["company_id"] == "CO1"]
        assert hits_a and hits_b, "No 1M rows found"
        assert hits_a[0] != hits_b[0], (
            "Expected different hits for +1 vs -1 direction but got same result. "
            "Scorer may not be reading projected_impacts."
        )

    def test_scorer_reads_market_prices(self):
        """Changing market_prices changes realized_return (it is actually read)."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]

        # Run 1: price goes up
        prices_up = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 120.0),
            _price_row("CO1", "2024-09-30", 125.0),
        ]
        run_up = _make_run(frozen=True, impact_rows=impacts, price_rows=prices_up)
        score_projections(run_up)
        rows_up = pq.read_table(
            _runs.settings.run_output_dir / run_up / "validation" / "projection_scores.parquet"
        ).to_pylist()

        # Run 2: price goes down
        prices_down = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 80.0),
            _price_row("CO1", "2024-09-30", 75.0),
        ]
        run_down = _make_run(frozen=True, impact_rows=impacts, price_rows=prices_down)
        score_projections(run_down)
        rows_down = pq.read_table(
            _runs.settings.run_output_dir / run_down / "validation" / "projection_scores.parquet"
        ).to_pylist()

        ret_up = [r["realized_return"] for r in rows_up if r["forward_window"] == "1M" and r["company_id"] == "CO1"]
        ret_down = [r["realized_return"] for r in rows_down if r["forward_window"] == "1M" and r["company_id"] == "CO1"]
        assert ret_up and ret_down
        assert ret_up[0] > 0 and ret_down[0] < 0, (
            "realized_return should differ between up-price and down-price runs; "
            "scorer may not be reading market_prices."
        )


# ---------------------------------------------------------------------------
# (7) Empty-but-schema-valid artifact
# ---------------------------------------------------------------------------


class TestEmptyButSchemaValid:
    """Empty projection_scores.parquet has correct schema."""

    def test_empty_projected_impacts_produces_empty_artifact(self):
        """No projected impacts -> empty-but-schema-valid parquet."""
        run_id = _make_run(frozen=True, impact_rows=[], price_rows=[])
        result = score_projections(run_id)
        assert result["success"] is True
        assert result["scored_rows"] == 0

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        assert out_path.exists()
        table = pq.read_table(out_path)
        assert table.num_rows == 0
        for col in PROJECTION_SCORES_COLUMNS:
            assert col in table.schema.names, f"Missing column: {col}"

    def test_schema_column_types_when_empty(self):
        """Empty table column types match the contract."""
        run_id = _make_run(frozen=True, impact_rows=[], price_rows=[])
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        table = pq.read_table(out_path)
        assert table.schema.field("direction").type == pa.int32()
        assert table.schema.field("strength").type == pa.float64()
        assert table.schema.field("realized_return").type == pa.float64()
        assert table.schema.field("hit").type == pa.int32()
        assert table.schema.field("hit_rate_by_trigger").type == pa.float64()
        assert table.schema.field("rank_corr_by_trigger").type == pa.float64()


# ---------------------------------------------------------------------------
# (8) Coverage gate: windows without sufficient prices are skipped
# ---------------------------------------------------------------------------


class TestCoverageGate:
    """Forward windows without sufficient price coverage are skipped."""

    def test_window_without_coverage_produces_no_rows(self):
        """Only a 1M price is available; 3M window should yield no rows."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        # Only prices through 2024-07-30 (covers 1M but not 3M from 2024-06-30)
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            # NO prices through 2024-09-30 -> 3M window blocked
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        windows = {r["forward_window"] for r in rows}
        # 3M window should be absent
        assert "3M" not in windows, (
            f"3M window should not appear without 3M price coverage; got windows={windows}"
        )
        # 1M should be present
        assert "1M" in windows, (
            f"1M window should be present; got windows={windows}"
        )

    def test_no_prices_produces_empty_artifact(self):
        """No prices at all -> no valid windows -> empty artifact."""
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=[])
        result = score_projections(run_id)
        assert result["success"] is True
        assert result["scored_rows"] == 0


# ---------------------------------------------------------------------------
# Schema contract: all columns present; scorer_method and schema_version
# ---------------------------------------------------------------------------


class TestArtifactContract:
    """Output parquet conforms to PROJECTION_SCORES_COLUMNS contract."""

    def test_all_columns_present(self):
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        table = pq.read_table(out_path)
        for col in PROJECTION_SCORES_COLUMNS:
            assert col in table.schema.names, f"Missing column: {col}"

    def test_scorer_method_and_schema_version(self):
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        for row in rows:
            assert row["schema_version"] == SCHEMA_VERSION
            assert row["scorer_method"] == SCORER_METHOD

    def test_caveats_present_on_every_row(self):
        impacts = [_impact_row("EV1", "CO1", direction=+1, strength=0.8)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 110.0),
            _price_row("CO1", "2024-09-30", 115.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        for row in rows:
            assert row.get("caveats"), f"caveats must be non-empty: {row}"
            assert "POST-FREEZE" in row["caveats"] or "ILLUSTRATIVE" in row["caveats"], (
                f"Expected one-way or illustrative caveat in caveats: {row['caveats']}"
            )

    def test_direction_strength_inherited_from_projected_impacts(self):
        """direction and strength values match what was in projected_impacts."""
        impacts = [_impact_row("EV1", "CO1", direction=-1, strength=0.73)]
        prices = [
            _price_row("CO1", "2024-07-01", 100.0),
            _price_row("CO1", "2024-07-30", 90.0),
            _price_row("CO1", "2024-09-30", 85.0),
        ]
        run_id = _make_run(frozen=True, impact_rows=impacts, price_rows=prices)
        score_projections(run_id)

        out_path = (
            _runs.settings.run_output_dir / run_id
            / "validation" / "projection_scores.parquet"
        )
        rows = pq.read_table(out_path).to_pylist()
        assert rows
        row = rows[0]
        assert row["direction"] == -1
        assert abs(row["strength"] - 0.73) < 1e-6
