"""M6: Freeze-gated forward-return validation service.

Reads:
  - ``discovery/company_theme_exposure.parquet``  (io_contracts §18)
  - ``discovery/theme_snapshots.json``            (io_contracts §15)
  - ``discovery/communities.json``               (io_contracts §14)
  - ``discovery/entities.parquet``               (io_contracts §9)
  - ``validation/market_prices.parquet``          (io_contracts §19)
  - ``configs/validation.example.yml``            (via run_manifest.validation_config)

Writes:
  - ``validation/portfolio_baskets.parquet``      (io_contracts §21)
  - ``validation/validation.csv``                 (io_contracts §22)

Precondition (OI-3):
  - ``run_manifest.discovery_frozen`` must be True.
  - ``discovery_artifact_hashes`` must match current file hashes.

Forward-return leakage prevention:
  - Only prices with price_date STRICTLY > as_of_date and
    price_date <= as_of_date + holding_window are used.
  - Additionally, available_at must be <= price_date (restated rows excluded).
  - Entry price: earliest price_date strictly after as_of_date.
  - Exit price: latest price_date <= as_of_date + holding_window.

Coverage gate (OI-7):
  - max(price_date) >= as_of_date + holding_window; else blocked.
  - Uses price_date, NOT available_at (matches test_leakage_gates.py ~line 81).

Benchmarks:
  - equal_weight_universe: equal-weight of all universe companies.
  - sector_equal_weight: equal-weight by sector (from entities.parquet).
  - random_community_baseline: deterministic random basket (seed from config).

Single-snapshot caveat (spec §2 MVP Caveats):
  - backtest_status='disabled_not_enough_snapshots'
  - Single-snapshot runs are ILLUSTRATIVE only; no statistical/alpha claim.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import random
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from . import runs

SCHEMA_VERSION = "1.0"

# io_contracts §21 columns (exact order)
BASKET_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "as_of_date",
    "basket_id",
    "theme_snapshot_id",
    "community_id",
    "portfolio_method",
    "selection_rank",
    "company_id",
    "ticker",
    "exposure_score",
    "weight",
    "inclusion_reason",
    "calculation_method",
    "created_at",
]

# io_contracts §22 columns (exact order)
VALIDATION_CSV_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "as_of_date",
    "basket_id",
    "theme_snapshot_id",
    "community_id",
    "theme_name",
    "forward_window",
    "portfolio_method",
    "company_count",
    "start_date",
    "end_date",
    "theme_basket_return",
    "benchmark_name",
    "benchmark_return",
    "excess_return",
    "sample_size",
    "market_data_source",
    "caveats",
]

_SINGLE_SNAPSHOT_CAVEAT = (
    "ILLUSTRATIVE ONLY: single-snapshot MVP. One as_of_date over this universe "
    "yields a single cross-sectional draw which cannot support any statistical "
    "claim that themes are associated with future outcomes. No alpha or causal "
    "claim is made. Backtesting requires a multi-period walk-forward panel."
)


# ---------------------------------------------------------------------------
# Date arithmetic helpers
# ---------------------------------------------------------------------------


def _to_date(val: Any) -> Optional[date]:
    """Coerce a value to a Python date, or None if unparseable."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val)
    # Truncate to date part
    if "T" in s:
        s = s.split("T")[0]
    s = s[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping to last day of month."""
    year = d.year
    month = d.month + months
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _window_months(window_str: str) -> int:
    """Parse '1M' -> 1, '3M' -> 3, etc."""
    if window_str.upper().endswith("M"):
        return int(window_str[:-1])
    raise ValueError(f"Unsupported forward window format: {window_str!r}")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_validation_config(config_path: str) -> dict:
    """Load validation YAML config. Falls back to defaults if YAML unavailable."""
    import os
    from pathlib import Path as _Path

    # Resolve config path relative to repo root
    from .config import REPO_ROOT
    p = _Path(config_path)
    if not p.is_absolute():
        p = REPO_ROOT / config_path

    if not p.exists():
        return {}

    try:
        import yaml  # type: ignore
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Manual minimal YAML parsing fallback (handles simple key: value)
        return _parse_simple_yaml(p.read_text(encoding="utf-8"))


def _parse_simple_yaml(text: str) -> dict:
    """Very minimal YAML parser for flat key: value pairs."""
    result: dict = {}
    current_section: Optional[str] = None
    current_dict: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Section header (no leading spaces, ends with colon, no value after)
        if not line.startswith(" ") and not line.startswith("\t"):
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if val:
                    # inline value
                    if current_section and current_dict:
                        result[current_section] = current_dict
                    current_section = None
                    current_dict = {}
                    # Try int/float/bool
                    result[key] = _coerce_scalar(val)
                else:
                    # Section start
                    if current_section and current_dict:
                        result[current_section] = current_dict
                    current_section = key
                    current_dict = {}
        else:
            if current_section is not None:
                stripped2 = stripped.lstrip("- ").strip()
                if ":" in stripped2:
                    k, _, v = stripped2.partition(":")
                    current_dict[k.strip()] = _coerce_scalar(v.strip())
                elif stripped.lstrip().startswith("- "):
                    # list item
                    item = stripped.lstrip().lstrip("- ").strip().strip('"').strip("'")
                    if current_section not in result:
                        result[current_section] = []
                    if isinstance(result.get(current_section), list):
                        result[current_section].append(item)
    if current_section and current_dict:
        result[current_section] = current_dict
    return result


def _coerce_scalar(v: str) -> Any:
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v.strip('"').strip("'")


# ---------------------------------------------------------------------------
# Market price loading (validation/ directory only — future data)
# ---------------------------------------------------------------------------


def _load_market_prices(run_id: str) -> list[dict]:
    """Load market_prices.parquet from the validation/ directory.

    This is FUTURE data. Must only be called after freeze gate passes.
    """
    prices_path = runs.get_run_dir(run_id) / runs.VALIDATION_DIR / "market_prices.parquet"
    if not prices_path.exists():
        return []
    table = pq.read_table(prices_path)
    rows = table.to_pylist()
    return rows


def _apply_leakage_filter(
    rows: list[dict],
    as_of_date: date,
    window_start: date,
    window_end: date,
) -> list[dict]:
    """Filter price rows for a forward window.

    Keeps only rows where:
      - price_date is STRICTLY > as_of_date   (no leak at entry)
      - price_date is <= window_end           (within the holding window)
      - available_at is not in the future relative to price_date  (no restatement leak)

    ``window_start`` and ``window_end`` are computed from as_of_date + holding window.
    ``as_of_date`` is the snapshot cutoff; ``window_start == as_of_date + 1 day`` logically,
    but we use ``price_date > as_of_date`` strictly.
    """
    filtered: list[dict] = []
    for row in rows:
        pd = _to_date(row.get("price_date"))
        if pd is None:
            continue
        # Forward window: price_date must be STRICTLY AFTER as_of_date
        if pd <= as_of_date:
            continue
        # Within holding window
        if pd > window_end:
            continue
        # Availability guard: available_at must be <= price_date
        # (a row whose available_at > price_date is a restated/backfilled row
        #  and must NOT be treated as if known at price_date)
        av = _to_date(row.get("available_at"))
        if av is not None and av > pd:
            continue
        filtered.append(row)
    return filtered


def _compute_basket_return(
    company_ids: list[str],
    weights: dict[str, float],
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
    price_col: str = "adjusted_close",
) -> tuple[Optional[float], int, Optional[date], Optional[date]]:
    """Compute equal-weight or weighted basket forward return.

    Returns:
        (return_value, sample_size, start_date, end_date)
        return_value is None if insufficient data.

    Uses only price_date STRICTLY > as_of_date for entry,
    and price_date <= window_end for exit.
    Applies availability guard (available_at <= price_date).
    """
    # Filter prices to forward window
    forward_rows = _apply_leakage_filter(all_price_rows, as_of_date, as_of_date, window_end)

    # Group by company
    by_company: dict[str, list[dict]] = {}
    for row in forward_rows:
        cid = str(row.get("company_id") or "")
        if cid in company_ids:
            by_company.setdefault(cid, []).append(row)

    # Compute per-company return
    company_returns: list[tuple[str, float]] = []
    start_dates: list[date] = []
    end_dates: list[date] = []

    for cid in company_ids:
        rows_c = by_company.get(cid, [])
        if not rows_c:
            continue
        # Sort by price_date
        rows_sorted = sorted(rows_c, key=lambda r: _to_date(r["price_date"]))

        # Try adjusted_close first, fall back to close
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
        entry_date: Optional[date] = None
        exit_price: Optional[float] = None
        exit_date: Optional[date] = None

        for r in rows_sorted:
            pd = _to_date(r["price_date"])
            p = _price(r)
            if p is None:
                continue
            if entry_price is None:
                # Earliest price strictly after as_of_date
                entry_price = p
                entry_date = pd
            # Latest price within window
            exit_price = p
            exit_date = pd

        if entry_price is None or exit_price is None or entry_price == 0.0:
            continue

        ret = (exit_price - entry_price) / entry_price
        company_returns.append((cid, ret))
        if entry_date:
            start_dates.append(entry_date)
        if exit_date:
            end_dates.append(exit_date)

    if not company_returns:
        return None, 0, None, None

    # Weighted return
    total_weight = sum(weights.get(cid, 1.0) for cid, _ in company_returns)
    if total_weight == 0.0:
        return None, 0, None, None

    basket_return = sum(
        (weights.get(cid, 1.0) / total_weight) * ret
        for cid, ret in company_returns
    )
    start_date = min(start_dates) if start_dates else None
    end_date = max(end_dates) if end_dates else None
    return basket_return, len(company_returns), start_date, end_date


# ---------------------------------------------------------------------------
# Coverage gate (OI-7)
# ---------------------------------------------------------------------------


def _check_forward_coverage(
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
) -> bool:
    """Return True if max(price_date) >= window_end.

    Uses price_date (not available_at) per spec and test_leakage_gates.py ~line 81.
    """
    max_pd: Optional[date] = None
    for row in all_price_rows:
        pd = _to_date(row.get("price_date"))
        if pd is None:
            continue
        if max_pd is None or pd > max_pd:
            max_pd = pd
    if max_pd is None:
        return False
    return max_pd >= window_end


# ---------------------------------------------------------------------------
# Exposure / entity / snapshot readers
# ---------------------------------------------------------------------------


def _load_exposure(run_id: str) -> list[dict]:
    path = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR / "company_theme_exposure.parquet"
    if not path.exists():
        return []
    try:
        return pq.read_table(path).to_pylist()
    except Exception:
        return []


def _load_theme_snapshots(run_id: str) -> dict[str, dict]:
    """Returns {theme_snapshot_id: snapshot_dict}."""
    path = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR / "theme_snapshots.json"
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {s["theme_snapshot_id"]: s for s in doc.get("snapshots", [])}
    except Exception:
        return {}


def _load_communities(run_id: str) -> dict[str, dict]:
    """Returns {community_id: community_dict}."""
    path = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR / "communities.json"
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {c["community_id"]: c for c in doc.get("communities", [])}
    except Exception:
        return {}


def _load_entities(run_id: str) -> list[dict]:
    path = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR / "entities.parquet"
    if not path.exists():
        return []
    try:
        return pq.read_table(path).to_pylist()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Basket construction
# ---------------------------------------------------------------------------


def _build_theme_baskets(
    run_id: str,
    as_of_date: str,
    exposure_rows: list[dict],
    snapshots: dict[str, dict],
    basket_top_n: int,
) -> list[dict]:
    """Build per-theme baskets from company_theme_exposure.

    Selection rule: top-N companies by exposure_score per theme_snapshot_id.
    Weights: equal-weight within basket (1/N).
    Deterministic: sort by (exposure_score DESC, company_id ASC) for ties.

    Returns list of basket rows conforming to io_contracts §21.
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Group exposure by theme_snapshot_id
    by_theme: dict[str, list[dict]] = {}
    for row in exposure_rows:
        tid = str(row.get("theme_snapshot_id") or "")
        if not tid:
            continue
        by_theme.setdefault(tid, []).append(row)

    basket_rows: list[dict] = []
    for theme_snapshot_id, rows in sorted(by_theme.items()):
        # Sort: descending exposure_score, ascending company_id for ties
        sorted_rows = sorted(
            rows,
            key=lambda r: (-float(r.get("exposure_score") or 0.0), str(r.get("company_id") or "")),
        )
        top_rows = sorted_rows[:basket_top_n]

        if not top_rows:
            continue

        n = len(top_rows)
        equal_weight = round(1.0 / n, 8)
        snap = snapshots.get(theme_snapshot_id, {})
        community_id = str(snap.get("community_id") or top_rows[0].get("community_id") or "")

        # Stable basket_id: deterministic hash of run_id + theme_snapshot_id
        basket_id = "basket_" + hashlib.sha256(
            f"{run_id}:{theme_snapshot_id}".encode()
        ).hexdigest()[:12]

        for rank, row in enumerate(top_rows, start=1):
            company_id = str(row.get("company_id") or "")
            ticker = row.get("ticker")
            exposure_score = float(row.get("exposure_score") or 0.0)
            basket_rows.append({
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "as_of_date": as_of_date,
                "basket_id": basket_id,
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "portfolio_method": "equal_weight_top_n_exposure",
                "selection_rank": rank,
                "company_id": company_id,
                "ticker": ticker if ticker is not None else None,
                "exposure_score": exposure_score,
                "weight": equal_weight,
                "inclusion_reason": (
                    f"top_{basket_top_n}_by_exposure_score rank={rank}"
                ),
                "calculation_method": "exposure_v1_document_stated",
                "created_at": now_str,
            })

    return basket_rows


def _write_basket_parquet(basket_rows: list[dict], out_path: Path) -> None:
    """Write portfolio_baskets.parquet (io_contracts §21)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not basket_rows:
        schema = pa.schema([
            ("schema_version", pa.string()),
            ("run_id", pa.string()),
            ("as_of_date", pa.string()),
            ("basket_id", pa.string()),
            ("theme_snapshot_id", pa.string()),
            ("community_id", pa.string()),
            ("portfolio_method", pa.string()),
            ("selection_rank", pa.int64()),
            ("company_id", pa.string()),
            ("ticker", pa.string()),
            ("exposure_score", pa.float64()),
            ("weight", pa.float64()),
            ("inclusion_reason", pa.string()),
            ("calculation_method", pa.string()),
            ("created_at", pa.string()),
        ])
        empty = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    arrays: dict[str, pa.Array] = {}
    for col in BASKET_COLUMNS:
        values = [row.get(col) for row in basket_rows]
        if col in {"exposure_score", "weight"}:
            arrays[col] = pa.array(values, type=pa.float64())
        elif col == "selection_rank":
            arrays[col] = pa.array(values, type=pa.int64())
        else:
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )
    pq.write_table(pa.table(arrays), out_path)


# ---------------------------------------------------------------------------
# Benchmark computations
# ---------------------------------------------------------------------------


def _compute_equal_weight_universe_return(
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
    company_ids: list[str],
) -> tuple[Optional[float], int]:
    """Equal-weight universe: all company_ids from exposure."""
    if not company_ids:
        return None, 0
    weights = {cid: 1.0 for cid in company_ids}
    ret, n, _, _ = _compute_basket_return(company_ids, weights, all_price_rows, as_of_date, window_end)
    return ret, n


def _compute_sector_equal_weight_return(
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
    company_ids: list[str],
    entities: list[dict],
) -> tuple[Optional[float], int, str]:
    """Sector equal-weight: group companies by sector, weight equally within each sector.

    Returns (return, sample_size, caveat).
    """
    # Build company -> sector mapping from entities
    entity_sector: dict[str, Optional[str]] = {}
    for ent in entities:
        eid = str(ent.get("entity_id") or "")
        if ent.get("entity_type") == "Company" and eid in company_ids:
            entity_sector[eid] = ent.get("sector")

    has_sector = any(v for v in entity_sector.values() if v)
    if not has_sector:
        # No sector data; fall back to equal weight with caveat
        weights = {cid: 1.0 for cid in company_ids}
        ret, n, _, _ = _compute_basket_return(
            company_ids, weights, all_price_rows, as_of_date, window_end
        )
        caveat = "sector_equal_weight: no sector data available; used equal_weight_universe as fallback"
        return ret, n, caveat

    # Group by sector; intra-sector equal weight, then average across sectors
    by_sector: dict[str, list[str]] = {}
    for cid in company_ids:
        sector = entity_sector.get(cid) or "unknown"
        by_sector.setdefault(sector, []).append(cid)

    sector_returns: list[float] = []
    total_n = 0
    for sector, sector_cids in sorted(by_sector.items()):
        weights = {cid: 1.0 for cid in sector_cids}
        ret_s, n_s, _, _ = _compute_basket_return(
            sector_cids, weights, all_price_rows, as_of_date, window_end
        )
        if ret_s is not None:
            sector_returns.append(ret_s)
            total_n += n_s

    if not sector_returns:
        return None, 0, ""
    avg_ret = sum(sector_returns) / len(sector_returns)
    return avg_ret, total_n, ""


def _compute_random_community_baseline(
    all_price_rows: list[dict],
    as_of_date: date,
    window_end: date,
    all_company_ids: list[str],
    basket_top_n: int,
    random_seed: int,
) -> tuple[Optional[float], int]:
    """Random-community baseline: deterministic random sample of basket_top_n companies.

    Uses random_seed from config for reproducibility.
    """
    if not all_company_ids:
        return None, 0
    rng = random.Random(random_seed)
    n_sample = min(basket_top_n, len(all_company_ids))
    sampled = sorted(all_company_ids)  # Sort first for determinism
    rng.shuffle(sampled)
    sampled = sampled[:n_sample]
    weights = {cid: 1.0 for cid in sampled}
    ret, n, _, _ = _compute_basket_return(sampled, weights, all_price_rows, as_of_date, window_end)
    return ret, n


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------


def run_validation(run_id: str) -> dict:
    """Execute M6 freeze-gated forward-return validation.

    Returns a dict with keys:
      success, validation_status, backtest_status, artifacts,
      validated_themes, message, missing_ranges (on block), etc.

    Raises:
      PermissionError: freeze gate not passed.
      ValueError: artifact hash mismatch.
    """
    # --- PRECONDITION (OI-3): verify freeze gate ---
    # This raises PermissionError or ValueError if not frozen / hash mismatch
    manifest = runs.validate_ready_for_validation(run_id)

    as_of_date_str: str = manifest.as_of_date
    as_of_date: date = date.fromisoformat(as_of_date_str)

    # --- Load validation config ---
    config = _load_validation_config(manifest.validation_config)
    forward_windows_raw: list[str] = config.get("forward_windows", ["1M", "3M"])
    coverage_months: dict[str, int] = config.get("forward_coverage_months", {})
    basket_top_n: int = int(config.get("basket_top_n", 10))
    random_seed: int = int(config.get("random_seed", 42))
    benchmark_names: list[str] = config.get("benchmarks", ["equal_weight_universe"])

    # --- Load discovery artifacts (frozen) ---
    exposure_rows = _load_exposure(run_id)
    snapshots = _load_theme_snapshots(run_id)
    communities = _load_communities(run_id)
    entities = _load_entities(run_id)

    # --- Load market prices (future data, validation/ only) ---
    all_price_rows = _load_market_prices(run_id)

    # Build universe: all company_ids that appear in exposure
    universe_company_ids: list[str] = sorted(
        set(str(r.get("company_id") or "") for r in exposure_rows if r.get("company_id"))
    )

    # --- OI-7: Check forward coverage for each window ---
    blocked_windows: dict[str, dict] = {}
    for win_str in forward_windows_raw:
        try:
            win_months = _window_months(win_str)
        except ValueError:
            continue
        required_months = coverage_months.get(win_str, win_months)
        window_end = _add_months(as_of_date, required_months)
        has_coverage = _check_forward_coverage(all_price_rows, as_of_date, window_end)
        if not has_coverage:
            blocked_windows[win_str] = {
                "window": win_str,
                "as_of_date": as_of_date_str,
                "holding_window": win_str,
                "required_end": window_end.isoformat(),
                "missing_ranges": [
                    f"{as_of_date_str} to {window_end.isoformat()}"
                ],
            }

    # If ALL windows are blocked, return blocked status
    valid_windows = [w for w in forward_windows_raw if w not in blocked_windows]
    if not valid_windows:
        first_blocked = next(iter(blocked_windows.values())) if blocked_windows else {}
        return {
            "success": False,
            "validation_status": "blocked_insufficient_forward_data",
            "backtest_status": "disabled_not_enough_snapshots",
            "artifacts": [],
            "validated_themes": 0,
            "message": (
                "Forward coverage insufficient for all requested windows. "
                "Load market_prices.parquet with sufficient future price dates."
            ),
            "missing_ranges": first_blocked.get("missing_ranges", []),
            "as_of_date": as_of_date_str,
            "holding_window": first_blocked.get("holding_window", ""),
            "required_end": first_blocked.get("required_end", ""),
        }

    # --- Build theme baskets ---
    basket_rows = _build_theme_baskets(
        run_id=run_id,
        as_of_date=as_of_date_str,
        exposure_rows=exposure_rows,
        snapshots=snapshots,
        basket_top_n=basket_top_n,
    )

    # Write portfolio_baskets.parquet
    basket_path = runs.get_run_dir(run_id) / runs.VALIDATION_DIR / "portfolio_baskets.parquet"
    _write_basket_parquet(basket_rows, basket_path)

    # --- Compute forward returns and write validation.csv ---
    validation_rows: list[dict] = []
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Group baskets by (basket_id, theme_snapshot_id) for return computation
    basket_by_id: dict[str, list[dict]] = {}
    for row in basket_rows:
        bid = str(row.get("basket_id") or "")
        basket_by_id.setdefault(bid, []).append(row)

    # Identify all market_data_source values
    market_data_sources = list(
        set(str(r.get("source") or "unknown") for r in all_price_rows)
    )
    market_data_source_str = ",".join(sorted(market_data_sources)) if market_data_sources else "unknown"

    for win_str in valid_windows:
        try:
            win_months = _window_months(win_str)
        except ValueError:
            continue
        required_months = coverage_months.get(win_str, win_months)
        window_end = _add_months(as_of_date, required_months)

        # Compute benchmark returns for this window
        bm_returns: dict[str, tuple[Optional[float], int]] = {}
        bm_caveats: dict[str, str] = {}

        for bm_name in benchmark_names:
            if bm_name == "equal_weight_universe":
                bm_ret, bm_n = _compute_equal_weight_universe_return(
                    all_price_rows, as_of_date, window_end, universe_company_ids
                )
                bm_returns[bm_name] = (bm_ret, bm_n)

            elif bm_name == "sector_equal_weight":
                bm_ret, bm_n, bm_caveat = _compute_sector_equal_weight_return(
                    all_price_rows, as_of_date, window_end, universe_company_ids, entities
                )
                bm_returns[bm_name] = (bm_ret, bm_n)
                if bm_caveat:
                    bm_caveats[bm_name] = bm_caveat

            elif bm_name == "random_community_baseline":
                bm_ret, bm_n = _compute_random_community_baseline(
                    all_price_rows, as_of_date, window_end,
                    universe_company_ids, basket_top_n, random_seed
                )
                bm_returns[bm_name] = (bm_ret, bm_n)
            else:
                # Unknown benchmark: defer with explicit caveat
                bm_returns[bm_name] = (None, 0)
                bm_caveats[bm_name] = f"{bm_name}: not yet implemented; deferred"

        for basket_id, b_rows in sorted(basket_by_id.items()):
            if not b_rows:
                continue
            ref = b_rows[0]
            theme_snapshot_id = str(ref.get("theme_snapshot_id") or "")
            community_id = str(ref.get("community_id") or "")
            portfolio_method = str(ref.get("portfolio_method") or "")

            snap = snapshots.get(theme_snapshot_id, {})
            theme_name = str(snap.get("theme_name") or community_id)

            # Company ids and weights in this basket
            b_company_ids = [str(r.get("company_id") or "") for r in b_rows]
            b_weights = {str(r.get("company_id") or ""): float(r.get("weight") or 1.0)
                         for r in b_rows}

            # Compute theme basket return
            basket_ret, sample_n, start_dt, end_dt = _compute_basket_return(
                b_company_ids, b_weights, all_price_rows, as_of_date, window_end
            )

            start_date_str = start_dt.isoformat() if start_dt else ""
            end_date_str = end_dt.isoformat() if end_dt else ""

            # Emit one row per benchmark
            for bm_name in benchmark_names:
                bm_ret, bm_n = bm_returns.get(bm_name, (None, 0))

                excess = None
                if basket_ret is not None and bm_ret is not None:
                    excess = basket_ret - bm_ret

                caveats_parts = [_SINGLE_SNAPSHOT_CAVEAT]
                if bm_name in bm_caveats:
                    caveats_parts.append(bm_caveats[bm_name])
                # Also note coverage blocked windows
                if blocked_windows:
                    blocked_strs = ", ".join(sorted(blocked_windows.keys()))
                    caveats_parts.append(
                        f"Windows {blocked_strs} had insufficient forward coverage and were skipped."
                    )

                validation_rows.append({
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "as_of_date": as_of_date_str,
                    "basket_id": basket_id,
                    "theme_snapshot_id": theme_snapshot_id,
                    "community_id": community_id,
                    "theme_name": theme_name,
                    "forward_window": win_str,
                    "portfolio_method": portfolio_method,
                    "company_count": len(b_company_ids),
                    "start_date": start_date_str,
                    "end_date": end_date_str,
                    "theme_basket_return": (
                        f"{basket_ret:.6f}" if basket_ret is not None else ""
                    ),
                    "benchmark_name": bm_name,
                    "benchmark_return": (
                        f"{bm_ret:.6f}" if bm_ret is not None else ""
                    ),
                    "excess_return": (
                        f"{excess:.6f}" if excess is not None else ""
                    ),
                    "sample_size": sample_n,
                    "market_data_source": market_data_source_str,
                    "caveats": " | ".join(caveats_parts),
                })

    # Write validation.csv
    csv_path = runs.get_run_dir(run_id) / runs.VALIDATION_DIR / "validation.csv"
    _write_validation_csv(validation_rows, csv_path)

    # Count distinct validated themes (theme_snapshot_ids with at least one result)
    validated_theme_ids = {
        r["theme_snapshot_id"]
        for r in validation_rows
        if r.get("theme_basket_return", "") != ""
    }

    artifacts = [
        "validation/portfolio_baskets.parquet",
        "validation/validation.csv",
    ]

    return {
        "success": True,
        "validation_status": "completed",
        "backtest_status": "disabled_not_enough_snapshots",
        "artifacts": artifacts,
        "validated_themes": len(validated_theme_ids),
        "message": (
            f"Single-snapshot MVP validation complete. "
            f"Results are illustrative only; no statistical claim is made."
        ),
    }


def _write_validation_csv(rows: list[dict], out_path: Path) -> None:
    """Write validation.csv (io_contracts §22)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=VALIDATION_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in VALIDATION_CSV_COLUMNS})
