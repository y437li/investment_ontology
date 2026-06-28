"""FI-E: Projection validation / scoring pass (GitHub #108).

Workstream P-E per docs/design_forward_inference.md.
Depends on FI-C (``projected_impacts.parquet`` in ``discovery/``).

POST-FREEZE, ONE-WAY
---------------------
This module is executed AFTER the discovery freeze gate has been verified.
It compares each projected impact's direction (and ordinal strength) against
REALIZED forward-window returns drawn from ``validation/market_prices.parquet``.

CRITICAL leakage discipline:
  - Scores must NEVER flow back into discovery-time projection.
  - This module does NOT import from and is NOT imported by
    ``propagation.py`` or ``projected_impacts.py``.
  - Realized prices are sourced EXCLUSIVELY from ``validation/`` artifacts;
    never from ``discovery/``.
  - The scorer may ONLY be called after the freeze gate has been verified
    (``run_manifest.discovery_frozen == True``).

Reads
-----
  - ``discovery/projected_impacts.parquet``   (io_contracts §FI-C)
  - ``validation/market_prices.parquet``       (io_contracts §19)

Writes
------
  - ``validation/projection_scores.parquet``   (io_contracts §FI-E)

Scoring algorithm
-----------------
For each (trigger_id, company_id) row in projected_impacts:

  1. Compute ``realized_return`` using the SAME forward-window machinery as
     ``validation.py`` (reused: ``_apply_leakage_filter``, ``_to_date`` from
     ``validation.py``).  Entry = earliest price_date > as_of_date; exit =
     latest price_date <= as_of_date + holding_window.

  2. ``hit = 1`` if ``sign(realized_return) == direction`` (i.e. the projection
     correctly called the direction of the realized forward return).
     ``hit = 0`` if the signs disagree.
     ``hit = None`` when realized_return == 0.0 or no price data is available.

  3. Aggregate per (trigger_id, forward_window):
       * ``hit_rate``  = mean(hit) over non-null hits
       * ``rank_corr`` = Spearman(strength, abs(realized_return)) over rows
                         with non-null realized_return; None when n < 2.

Output columns (projection_scores.parquet)
------------------------------------------
See PROJECTION_SCORES_COLUMNS below and io_contracts §FI-E.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from . import runs

# ---------------------------------------------------------------------------
# Never import from propagation or projected_impacts here.
# Reuse the pure helper functions from validation (no circular dependency).
# ---------------------------------------------------------------------------
from .validation import (
    _add_months,
    _apply_leakage_filter,
    _to_date,
    _window_months,
    _load_validation_config,
    _check_forward_coverage,
)

SCHEMA_VERSION = "1.0"
SCORER_METHOD = "projection_scorer_v1"

# io_contracts §FI-E columns (exact order)
PROJECTION_SCORES_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "as_of_date",
    "trigger_id",
    "company_id",
    "direction",
    "strength",
    "forward_window",
    "realized_return",
    "hit",
    "hit_rate_by_trigger",
    "rank_corr_by_trigger",
    "scorer_method",
    "caveats",
]

_ONE_WAY_CAVEAT = (
    "POST-FREEZE scorer: direction/strength compared against realized forward "
    "returns ONLY. Scores never written back into discovery-time projection. "
    "hit=1 means projected direction matched realized return sign; "
    "rank_corr is Spearman(strength, |realized_return|) by trigger. "
    "ILLUSTRATIVE ONLY: single-snapshot; no statistical claim."
)


# ---------------------------------------------------------------------------
# Rank-correlation (Spearman) — pure stdlib; no scipy dependency
# ---------------------------------------------------------------------------


def _rank_list(vals: list[float]) -> list[float]:
    """Return 1-based average ranks for a list of floats (handles ties)."""
    n = len(vals)
    if n == 0:
        return []
    sorted_idx = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and vals[sorted_idx[j]] == vals[sorted_idx[i]]:
            j += 1
        avg_rank = (i + j - 1) / 2.0 + 1  # 1-based, average for ties
        for k in range(i, j):
            ranks[sorted_idx[k]] = avg_rank
        i = j
    return ranks


def _spearman_rho(x: list[float], y: list[float]) -> Optional[float]:
    """Spearman rank correlation coefficient; None if n < 2 or zero variance."""
    n = len(x)
    if n < 2:
        return None
    rx = _rank_list(x)
    ry = _rank_list(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den_x = sum((rx[i] - mx) ** 2 for i in range(n))
    den_y = sum((ry[i] - my) ** 2 for i in range(n))
    denom = (den_x * den_y) ** 0.5
    if denom == 0.0:
        return None
    return num / denom


# ---------------------------------------------------------------------------
# Artifact readers (validation-scope only)
# ---------------------------------------------------------------------------


def _load_projected_impacts(run_id: str) -> list[dict]:
    """Load projected_impacts.parquet from discovery/ (read-only; not imported).

    This is a DISCOVERY artifact read post-freeze for scoring purposes only.
    The scorer never modifies it.
    """
    path = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR / "projected_impacts.parquet"
    if not path.exists():
        return []
    try:
        return pq.read_table(path).to_pylist()
    except Exception as exc:
        raise ValueError(f"corrupt projected_impacts.parquet for run {run_id}: {exc}")


def _load_market_prices_for_scoring(run_id: str) -> list[dict]:
    """Load market_prices.parquet from validation/ (FUTURE data only).

    Identical sourcing rule as validation.py — only the validation/ directory
    is consulted; this is never the same file as any discovery artifact.
    """
    path = runs.get_run_dir(run_id) / runs.VALIDATION_DIR / "market_prices.parquet"
    if not path.exists():
        return []
    try:
        return pq.read_table(path).to_pylist()
    except Exception as exc:
        raise ValueError(f"corrupt market_prices.parquet for run {run_id}: {exc}")


# ---------------------------------------------------------------------------
# Per-company realized-return computation (reuses validation.py helpers)
# ---------------------------------------------------------------------------


def _realized_return_for_company(
    company_id: str,
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
    price_col: str = "adjusted_close",
) -> Optional[float]:
    """Compute the realized forward return for a single company.

    Uses only price rows with:
      - price_date STRICTLY > as_of_date  (no look-ahead at as_of_date itself)
      - price_date <= window_end           (within the holding window)
      - available_at <= price_date         (no restated/backfilled rows)

    Returns None if insufficient price data.  Returns 0.0 if prices are equal.
    """
    forward_rows = _apply_leakage_filter(
        all_price_rows, as_of_date, as_of_date, window_end
    )
    company_rows = [
        r for r in forward_rows if str(r.get("company_id") or "") == company_id
    ]
    if not company_rows:
        return None

    # Sort by price_date ascending
    company_rows.sort(key=lambda r: _to_date(r["price_date"]) or date.min)

    def _price(r: dict) -> Optional[float]:
        v = r.get(price_col)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
        fallback = r.get("close")
        if fallback is not None:
            try:
                return float(fallback)
            except (ValueError, TypeError):
                pass
        return None

    entry_price: Optional[float] = None
    exit_price: Optional[float] = None

    for r in company_rows:
        p = _price(r)
        if p is None:
            continue
        if entry_price is None:
            entry_price = p
        exit_price = p

    if entry_price is None or exit_price is None or entry_price == 0.0:
        return None
    return (exit_price - entry_price) / entry_price


# ---------------------------------------------------------------------------
# Hit computation
# ---------------------------------------------------------------------------


def _compute_hit(direction: int, realized_return: Optional[float]) -> Optional[int]:
    """Return hit (1), miss (0), or None.

    hit  = 1  : sign(realized_return) == direction
    miss = 0  : sign(realized_return) != direction
    None      : realized_return is None OR realized_return == 0.0 (ambiguous)
    """
    if realized_return is None:
        return None
    if realized_return == 0.0:
        return None
    realized_sign = 1 if realized_return > 0.0 else -1
    return 1 if realized_sign == direction else 0


# ---------------------------------------------------------------------------
# Trigger-level aggregates
# ---------------------------------------------------------------------------


def _aggregate_by_trigger(
    rows: list[dict],
    window: str,
) -> dict[str, dict]:
    """Compute hit_rate and rank_corr per trigger_id for a given window.

    Returns {trigger_id: {"hit_rate": float|None, "rank_corr": float|None}}.
    """
    # Group by trigger_id
    by_trigger: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("forward_window") != window:
            continue
        tid = str(r.get("trigger_id") or "")
        by_trigger.setdefault(tid, []).append(r)

    result: dict[str, dict] = {}
    for tid, trows in by_trigger.items():
        # Hit rate: mean over non-null hits
        hits = [r["hit"] for r in trows if r.get("hit") is not None]
        hit_rate: Optional[float] = (sum(hits) / len(hits)) if hits else None

        # Rank correlation: Spearman(strength, |realized_return|)
        valid = [
            r for r in trows
            if r.get("realized_return") is not None
            and r.get("strength") is not None
        ]
        if len(valid) >= 2:
            strengths = [float(r["strength"]) for r in valid]
            abs_returns = [abs(float(r["realized_return"])) for r in valid]
            rank_corr = _spearman_rho(strengths, abs_returns)
        else:
            rank_corr = None

        result[tid] = {"hit_rate": hit_rate, "rank_corr": rank_corr}
    return result


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


def score_projections(run_id: str) -> dict:
    """Execute the projection-scoring pass for a frozen run.

    POST-FREEZE: verifies ``discovery_frozen == True`` before reading future
    prices.  Scores are written to ``validation/projection_scores.parquet``.

    Parameters
    ----------
    run_id : str
        The frozen run to score.

    Returns
    -------
    dict
        Keys: success, scored_rows, windows_scored, message, artifacts.

    Raises
    ------
    PermissionError
        If ``discovery_frozen != True`` in the run manifest (freeze gate).
    ValueError
        If projected_impacts.parquet or market_prices.parquet is corrupt.
    """
    # --- FREEZE GATE (post-freeze only) ---
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise PermissionError(f"run not found: {run_id}")
    if not getattr(manifest, "discovery_frozen", False):
        raise PermissionError(
            f"run {run_id} is not frozen; projection scoring requires "
            "discovery_frozen=True (run /api/discovery/freeze first)"
        )

    as_of_date_str: str = manifest.as_of_date
    as_of_date: date = date.fromisoformat(as_of_date_str)

    # --- Load config for forward windows ---
    config = _load_validation_config(manifest.validation_config)
    forward_windows_raw: list[str] = config.get("forward_windows", ["1M", "3M"])

    # --- Read projected_impacts (discovery artifact, read-only) ---
    impact_rows = _load_projected_impacts(run_id)

    # --- Read realized prices (validation artifact — future data) ---
    all_price_rows = _load_market_prices_for_scoring(run_id)

    # --- Score for each valid forward window ---
    score_rows: list[dict] = []

    for win_str in forward_windows_raw:
        try:
            win_months = _window_months(win_str)
        except ValueError:
            continue

        window_end = _add_months(as_of_date, win_months)

        # Check coverage (same gate as validation.py)
        if not _check_forward_coverage(all_price_rows, as_of_date, window_end):
            # Skip this window — no forward coverage
            continue

        # Build row-level scores for this window
        raw_window_rows: list[dict] = []
        for imp in impact_rows:
            company_id = str(imp.get("company_id") or "")
            trigger_id = str(imp.get("trigger_id") or "")
            direction = int(imp.get("direction") or 0)
            strength = float(imp.get("strength") or 0.0)

            if not company_id or not trigger_id or direction not in (1, -1):
                continue

            realized_return = _realized_return_for_company(
                company_id, all_price_rows, as_of_date, window_end
            )
            hit = _compute_hit(direction, realized_return)

            raw_window_rows.append({
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "as_of_date": as_of_date_str,
                "trigger_id": trigger_id,
                "company_id": company_id,
                "direction": direction,
                "strength": strength,
                "forward_window": win_str,
                "realized_return": realized_return,
                "hit": hit,
                "hit_rate_by_trigger": None,   # filled in below
                "rank_corr_by_trigger": None,  # filled in below
                "scorer_method": SCORER_METHOD,
                "caveats": _ONE_WAY_CAVEAT,
            })

        # Aggregate trigger-level statistics
        trigger_agg = _aggregate_by_trigger(raw_window_rows, win_str)

        # Back-fill aggregates onto each row
        for row in raw_window_rows:
            tid = row["trigger_id"]
            agg = trigger_agg.get(tid, {})
            row["hit_rate_by_trigger"] = agg.get("hit_rate")
            row["rank_corr_by_trigger"] = agg.get("rank_corr")

        score_rows.extend(raw_window_rows)

    # Sort deterministically by (trigger_id, company_id, forward_window)
    score_rows.sort(key=lambda r: (
        r["trigger_id"], r["company_id"], r["forward_window"]
    ))

    # --- Write output artifact ---
    out_path = (
        runs.get_run_dir(run_id) / runs.VALIDATION_DIR / "projection_scores.parquet"
    )
    _write_projection_scores(score_rows, out_path)

    windows_scored = len({r["forward_window"] for r in score_rows})
    artifacts = ["validation/projection_scores.parquet"]

    return {
        "success": True,
        "scored_rows": len(score_rows),
        "windows_scored": windows_scored,
        "artifacts": artifacts,
        "message": (
            f"Projection scoring complete: {len(score_rows)} rows scored across "
            f"{windows_scored} window(s). "
            "Scores are post-freeze only; never written back into discovery."
        ),
    }


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------


def _write_projection_scores(rows: list[dict], out_path: Path) -> None:
    """Write projection_scores.parquet (io_contracts §FI-E).

    An empty table is written when no rows were produced (e.g. no forward
    coverage or no projected_impacts rows), preserving the schema contract.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        schema = _projection_scores_schema()
        empty: dict = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    arrays: dict[str, pa.Array] = {}
    for col in PROJECTION_SCORES_COLUMNS:
        values = [row.get(col) for row in rows]
        if col == "direction":
            arrays[col] = pa.array(values, type=pa.int32())
        elif col in {"strength", "realized_return",
                     "hit_rate_by_trigger", "rank_corr_by_trigger"}:
            arrays[col] = pa.array(
                [float(v) if v is not None else None for v in values],
                type=pa.float64(),
            )
        elif col == "hit":
            arrays[col] = pa.array(
                [int(v) if v is not None else None for v in values],
                type=pa.int32(),
            )
        else:
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )

    pq.write_table(pa.table(arrays), out_path)


def _projection_scores_schema() -> pa.Schema:
    """Canonical PyArrow schema for projection_scores.parquet (io_contracts §FI-E)."""
    return pa.schema([
        ("schema_version", pa.string()),
        ("run_id", pa.string()),
        ("as_of_date", pa.string()),
        ("trigger_id", pa.string()),
        ("company_id", pa.string()),
        ("direction", pa.int32()),
        ("strength", pa.float64()),
        ("forward_window", pa.string()),
        ("realized_return", pa.float64()),
        ("hit", pa.int32()),
        ("hit_rate_by_trigger", pa.float64()),
        ("rank_corr_by_trigger", pa.float64()),
        ("scorer_method", pa.string()),
        ("caveats", pa.string()),
    ])
