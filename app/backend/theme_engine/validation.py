"""Validation runner implementation for milestone 6 preflight and artifact output.

The runner keeps the current behavior deterministic, while adding source-directed
validation loading when market/fundamentals directories are provided.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency guard
    yaml = None

from . import runs
from .config import REPO_ROOT


DEFAULT_FORWARD_WINDOWS: tuple[str, ...] = ("1M", "3M")
FUNDAMENTAL_DEFAULT_METRICS = ("revenue_growth", "eps_revision")


@dataclass(frozen=True)
class ValidationConfig:
    forward_windows: tuple[str, ...] = DEFAULT_FORWARD_WINDOWS
    forward_coverage_months: dict[str, int] = None
    optional_fundamentals: tuple[str, ...] = FUNDAMENTAL_DEFAULT_METRICS
    reject_insufficient_forward_data: bool = True
    reject_missing_available_at: bool = True
    walk_forward_as_of_dates: tuple[str, ...] = ()
    walk_forward_min_snapshots: int = 3
    walk_forward_require_coverage: bool = True
    walk_forward_min_snapshot_gap_days: int = 28
    field_mappings: dict[str, dict[str, tuple[str, ...]]] = None

    def __post_init__(self) -> None:
        if self.forward_coverage_months is None:
            object.__setattr__(self, "forward_coverage_months", {
                "1M": 1,
                "3M": 3,
            })
        if self.field_mappings is None:
            object.__setattr__(
                self,
                "field_mappings",
                {
                    "market": {k: tuple(v) for k, v in _MARKET_FIELD_ALIASES.items()},
                    "fundamentals": {
                        k: tuple(v) for k, v in _FUNDAMENTAL_FIELD_ALIASES.items()
                    },
                },
            )


@dataclass(frozen=True)
class ValidationArtifacts:
    artifacts: list[str]
    validated_themes: int
    validation_status: str = "validated"


_MARKET_FIELD_ALIASES = {
    "company_id": ("company_id", "company", "companyId", "ticker", "symbol"),
    "ticker": ("ticker", "symbol", "company_id"),
    "price_date": ("price_date", "date", "trade_date", "pricing_date"),
    "close": ("close", "close_price", "price"),
    "adjusted_close": (
        "adjusted_close",
        "adjusted_close_price",
        "adj_close",
        "adj_close_price",
    ),
    "currency": ("currency", "ccy", "currency_code"),
    "source": ("source", "source_name", "provider", "vendor"),
    "source_id": ("source_id", "provider", "provider_id", "source_name"),
    "available_at": (
        "available_at",
        "published_at",
        "published",
        "as_of",
        "as_of_date",
    ),
}


_FUNDAMENTAL_FIELD_ALIASES = {
    "company_id": ("company_id", "company", "companyId", "ticker", "symbol"),
    "ticker": ("ticker", "symbol", "company_id"),
    "period_end": ("period_end", "period", "fiscal_period_end", "quarter_end"),
    "metric_name": ("metric_name", "metric", "name"),
    "metric_value": ("metric_value", "value", "amount"),
    "unit": ("unit", "units", "currency_unit", "scale"),
    "currency": ("currency", "ccy", "currency_code"),
    "filing_date": ("filing_date", "filed", "report_date"),
    "available_at": (
        "available_at",
        "published_at",
        "published",
        "as_of",
        "as_of_date",
    ),
    "source": ("source", "source_name", "provider", "vendor"),
    "source_id": ("source_id", "provider", "provider_id", "source_name"),
}


SUPPORTED_SOURCE_SUFFIXES = {".parquet", ".pq", ".csv", ".json", ".jsonl"}



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_of_date_parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _add_months(value: datetime, months: int) -> datetime:
    year = value.year
    month = value.month + months
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    # clamp day to end-of-month
    from calendar import monthrange

    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _window_months(window: str) -> int:
    if window.endswith("M") and len(window) > 1 and window[:-1].isdigit():
        return int(window[:-1])
    return 1


def _coerce_window_value(raw: Any, default: int) -> int:
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        if value > 0:
            return value
    return default


def _normalize_forward_windows(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return DEFAULT_FORWARD_WINDOWS

    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
            if value:
                out.append(value)
    return tuple(out) if out else DEFAULT_FORWARD_WINDOWS


def _normalize_optional_fundamentals(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return FUNDAMENTAL_DEFAULT_METRICS

    metrics: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                metrics.append(name)
    return tuple(metrics) if metrics else FUNDAMENTAL_DEFAULT_METRICS


def _normalize_field_aliases(
    section: str,
    raw: Any,
    canonical_aliases: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {k: tuple(v) for k, v in canonical_aliases.items()}
    if not isinstance(raw, dict):
        raise ValueError(f"field_mappings[{section}] must be a mapping")

    normalized: dict[str, tuple[str, ...]] = {}
    for raw_key, value in raw.items():
        if not isinstance(raw_key, str):
            raise ValueError(f"field_mappings[{section}] keys must be string canonical names")
        key = raw_key.strip()
        if key not in canonical_aliases:
            raise ValueError(f"unknown {section} field mapping: {key}")

        if isinstance(value, str):
            alias_values = [value.strip()]
        elif isinstance(value, (list, tuple)):
            alias_values = [v.strip() for v in value if isinstance(v, str)]
        else:
            raise ValueError(
                f"field_mappings[{section}][{key}] must be a string or list of strings"
            )
        alias_values = [v for v in alias_values if v]
        if not alias_values:
            raise ValueError(
                f"field_mappings[{section}][{key}] must define at least one source column"
            )
        normalized[key] = tuple(dict.fromkeys(alias_values))

    merged = {k: tuple(v) for k, v in canonical_aliases.items()}
    merged.update(normalized)
    return merged


def _normalize_field_mappings(raw: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    if raw is None:
        return {
            "market": {k: tuple(v) for k, v in _MARKET_FIELD_ALIASES.items()},
            "fundamentals": {k: tuple(v) for k, v in _FUNDAMENTAL_FIELD_ALIASES.items()},
        }
    if not isinstance(raw, dict):
        raise ValueError("field_mappings must be a mapping")

    unknown_sections = [key for key in raw.keys() if key not in {"market", "fundamentals"}]
    if unknown_sections:
        raise ValueError(
            "field_mappings contains unsupported sections: "
            + ", ".join(sorted(unknown_sections))
        )

    return {
        "market": _normalize_field_aliases(
            "market",
            raw.get("market"),
            canonical_aliases=_MARKET_FIELD_ALIASES,
        ),
        "fundamentals": _normalize_field_aliases(
            "fundamentals",
            raw.get("fundamentals"),
            canonical_aliases=_FUNDAMENTAL_FIELD_ALIASES,
        ),
    }


def _normalize_walk_forward_dates(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()

    out: list[str] = []
    for item in raw:
        value = _coerce_str(item)
        if not value:
            continue
        try:
            _as_of_date_parse(value)
        except ValueError:
            continue
        out.append(value)
    # keep order but remove duplicates
    return tuple(dict.fromkeys(out))


def _coerce_positive_int(raw: Any, default: int) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        if value > 0:
            return value
    return default


def _read_validation_config(path_text: str | None) -> dict[str, Any]:
    if not path_text:
        return {}

    resolved = _resolve_path(path_text)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return {}

    if yaml is None:
        return {}

    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


def _load_validation_config(run_validation_config: str | None) -> ValidationConfig:
    payload = _read_validation_config(run_validation_config)

    forward_windows = _normalize_forward_windows(payload.get("forward_windows"))

    coverage_by_window = payload.get("forward_coverage_months")
    if not isinstance(coverage_by_window, dict) or not coverage_by_window:
        coverage_by_window = {"1M": 1, "3M": 3}
    coverage: dict[str, int] = {}
    for key, raw in coverage_by_window.items():
        if not isinstance(key, str):
            continue
        window = key.strip()
        if not window:
            continue
        months = _coerce_window_value(raw, _window_months(window))
        coverage[window] = months

    if not coverage:
        coverage = {"1M": 1, "3M": 3}

    for window in forward_windows:
        coverage.setdefault(window, _window_months(window))

    optional_fundamentals = _normalize_optional_fundamentals(payload.get("optional_fundamentals"))

    rules = payload.get("rules")
    reject_forward = True
    reject_available_at = True
    if isinstance(rules, dict):
        rv = rules.get("reject_insufficient_forward_data")
        if isinstance(rv, bool):
            reject_forward = rv
        rm = rules.get("reject_missing_available_at")
        if isinstance(rm, bool):
            reject_available_at = rm

    field_mappings = _normalize_field_mappings(payload.get("field_mappings"))

    walk_forward = payload.get("walk_forward")
    walk_forward_as_of_dates: tuple[str, ...] = ()
    walk_forward_min_snapshots = 3
    walk_forward_require_coverage = True
    walk_forward_min_snapshot_gap_days = 28

    if isinstance(walk_forward, dict):
        walk_forward_as_of_dates = _normalize_walk_forward_dates(
            walk_forward.get("as_of_dates")
        )
        walk_forward_min_snapshots = _coerce_positive_int(
            walk_forward.get("min_snapshots"),
            default=walk_forward_min_snapshots,
        )
        walk_forward_require_coverage = True
        rcv = walk_forward.get("require_coverage")
        if isinstance(rcv, bool):
            walk_forward_require_coverage = rcv
        walk_forward_min_snapshot_gap_days = _coerce_positive_int(
            walk_forward.get("min_snapshot_gap_days"),
            default=walk_forward_min_snapshot_gap_days,
        )

    return ValidationConfig(
        forward_windows=forward_windows,
        forward_coverage_months=coverage,
        optional_fundamentals=optional_fundamentals,
        reject_insufficient_forward_data=reject_forward,
        reject_missing_available_at=reject_available_at,
        walk_forward_as_of_dates=walk_forward_as_of_dates,
        walk_forward_min_snapshots=walk_forward_min_snapshots,
        walk_forward_require_coverage=walk_forward_require_coverage,
        walk_forward_min_snapshot_gap_days=walk_forward_min_snapshot_gap_days,
        field_mappings=field_mappings,
    )


def _walk_forward_backtest_status(config: ValidationConfig, manifest: runs.RunManifest) -> str:
    if manifest.sweep_parent_id is not None:
        return "validated"
    if config.walk_forward_as_of_dates and len(config.walk_forward_as_of_dates) < config.walk_forward_min_snapshots:
        return "disabled_not_enough_snapshots"
    return "disabled_not_enough_snapshots"



def _resolve_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    p = Path(path_text)
    return p if p.is_absolute() else (REPO_ROOT / p)



def _require_existing_path(path_text: str | None, *, label: str) -> Path | None:
    resolved = _resolve_path(path_text)
    if resolved is None:
        return None
    if not resolved.exists():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")
    return resolved



def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
    if value == "":
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)



def _coerce_date_like(value: object) -> str | None:
    text = _coerce_str(value)
    if text is None:
        return None
    if text[0].isdigit() and len(text) >= 10:
        # Keep date-or-timestamp as date-first string if present.
        prefix = text[:10]
        try:
            datetime.strptime(prefix, "%Y-%m-%d")
            return prefix
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return text


def _parse_datetime(value: object) -> datetime | None:
    text = _coerce_str(value)
    if text is None:
        return None
    candidate = text.strip()
    if not candidate:
        return None

    try:
        if len(candidate) >= 10 and candidate[:10][0].isdigit():
            return datetime.strptime(candidate[:10], "%Y-%m-%d")
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except Exception:
        return None



def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        t = value.strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None
    return None



def _pick_value(row: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None



def _as_discovery_company_ids(run_id: str) -> list[str]:
    # Try to recover stable company ids from available discovery artifacts.
    run_dir = runs.get_run_dir(run_id)
    graph_path = run_dir / "discovery" / "graph.json"
    if graph_path.exists():
        # Keep this permissive to avoid hard schema coupling before extraction
        # agent contracts are fully stabilized.
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
            for key in ("company_ids", "companies", "entities"):
                items = payload.get(key)
                if isinstance(items, list) and items:
                    return [str(x) for x in items][:5]
        except Exception:
            pass
    return ["DEMO_COMPANY_A", "DEMO_COMPANY_B"]



def _iter_table_rows(table: pa.Table) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    columns = table.column_names
    for i in range(table.num_rows):
        row: dict[str, object] = {}
        for name in columns:
            row[name] = table[name][i].as_py()
        rows.append(row)
    return rows



def _read_rows_from_file(path: Path) -> list[dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        table = pq.read_table(path)
        return _iter_table_rows(table)
    if suffix == ".csv":
        table = pa_csv.read_csv(path, read_options=pa_csv.ReadOptions(autogenerate_column_names=False))
        return _iter_table_rows(table)
    if suffix in {".json", ".jsonl"}:
        raw = path.read_text(encoding="utf-8")
        if suffix == ".jsonl":
            rows = []
            for line in raw.splitlines():
                text = line.strip()
                if not text:
                    continue
                rows.append(json.loads(text))
            return [dict(x) if isinstance(x, dict) else {} for x in rows]

        payload = json.loads(raw)
        if isinstance(payload, list):
            return [dict(x) if isinstance(x, dict) else {} for x in payload]
        if isinstance(payload, dict):
            items = payload.get("records")
            if isinstance(items, list):
                return [dict(x) if isinstance(x, dict) else {} for x in items]
        return []
    return []



def _read_rows_from_dir(source_dir: Path | None) -> list[dict[str, object]]:
    if source_dir is None:
        return []
    rows: list[dict[str, object]] = []
    for file in sorted(source_dir.rglob("*")):
        if not file.is_file():
            continue
        if file.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            continue
        try:
            rows.extend(_read_rows_from_file(file))
        except Exception:
            # Data-source loading remains strict for explicit inputs.
            raise ValueError(f"unable to read validation source file: {file}")
    return rows



def _normalize_market_rows(
    source_rows: list[dict[str, object]],
    run_id: str,
    companies: list[str],
    as_of_date: str,
    field_aliases: dict[str, tuple[str, ...]] | None = None,
    *,
    reject_missing_available_at: bool,
) -> list[dict[str, str | float | None]]:
    if not source_rows:
        return []

    aliases = field_aliases or _MARKET_FIELD_ALIASES
    allowed = {company for company in companies}
    rows: list[dict[str, str | float | None]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue

        company_id = _coerce_str(_pick_value(row, aliases["company_id"]))
        if not company_id:
            continue
        if allowed and company_id not in allowed:
            continue

        price_date = _coerce_date_like(_pick_value(row, aliases["price_date"]))
        if price_date is None:
            continue

        close = _coerce_float(_pick_value(row, aliases["close"]))
        adjusted_close = _coerce_float(_pick_value(row, aliases["adjusted_close"]))
        if close is None and adjusted_close is None:
            continue

        ticker = _coerce_str(_pick_value(row, aliases["ticker"])) or company_id[:4]
        available_at = _coerce_date_like(_pick_value(row, aliases["available_at"]))
        rows.append(
            {
                "schema_version": "v1",
                "run_id": run_id,
                "as_of_date": as_of_date,
                "company_id": company_id,
                "ticker": ticker,
                "price_date": price_date,
                "close": close,
                "adjusted_close": adjusted_close,
                "currency": _coerce_str(_pick_value(row, aliases["currency"]))
                or "USD",
                "source": _coerce_str(_pick_value(row, aliases["source"]))
                or "market_data",
                "source_id": _coerce_str(_pick_value(row, aliases["source_id"]))
                or "validation-market",
                "available_at": available_at,
                "created_at": _utc_now_iso(),
            }
        )
    return rows

def _normalize_fundamental_rows(
    source_rows: list[dict[str, object]],
    run_id: str,
    companies: list[str],
    as_of_date: str,
    metric_names: list[str],
    field_aliases: dict[str, tuple[str, ...]] | None = None,
    *,
    reject_missing_available_at: bool,
) -> list[dict[str, str | float | None]]:
    if not source_rows:
        return []

    aliases = field_aliases or _FUNDAMENTAL_FIELD_ALIASES
    allowed = {company for company in companies}
    rows: list[dict[str, str | float | None]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue

        company_id = _coerce_str(_pick_value(row, aliases["company_id"]))
        if not company_id:
            continue
        if allowed and company_id not in allowed:
            continue

        metric_name = _coerce_str(_pick_value(row, aliases["metric_name"]))
        if not metric_name:
            continue

        if metric_names and metric_name not in metric_names:
            continue

        available_at = _coerce_date_like(_pick_value(row, aliases["available_at"]))
        metric_value = _coerce_float(_pick_value(row, aliases["metric_value"]))
        if metric_value is None:
            continue

        rows.append(
            {
                "schema_version": "v1",
                "run_id": run_id,
                "as_of_date": as_of_date,
                "company_id": company_id,
                "ticker": _coerce_str(_pick_value(row, aliases["ticker"]))
                or company_id[:4],
                "period_end": _coerce_date_like(_pick_value(row, aliases["period_end"]))
                or as_of_date,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "unit": _coerce_str(_pick_value(row, aliases["unit"]))
                or "pct",
                "currency": _coerce_str(_pick_value(row, aliases["currency"]))
                or "USD",
                "filing_date": _coerce_date_like(_pick_value(row, aliases["filing_date"]))
                or as_of_date,
                "available_at": available_at,
                "source": _coerce_str(_pick_value(row, aliases["source"]))
                or "fundamentals_data",
                "source_id": _coerce_str(_pick_value(row, aliases["source_id"]))
                or "validation-fundamentals",
                "created_at": _utc_now_iso(),
            }
        )
    return rows


def _assert_no_future_available_dates(
    rows: list[dict[str, str | float | None]],
    as_of_date: str,
    *,
    row_type: str,
) -> None:
    as_of_dt = _as_of_date_parse(as_of_date)
    missing: list[str] = []
    for row in rows:
        company = row.get("company_id")
        available = row.get("available_at")
        if not isinstance(company, str):
            continue
        if available is None:
            missing.append(company)
            continue
        available_dt = _parse_datetime(available)
        if available_dt is None:
            continue
        if available_dt > as_of_dt:
            raise ValueError(
                f"{row_type} row for company {company} has available_at {available} after as_of_date {as_of_date}"
            )

    if missing:
        raise ValueError(
            f"missing available_at for {row_type} rows: companies {sorted(set(missing))}"
        )


def _assert_forward_coverage(
    rows: list[dict[str, str | float | None]],
    as_of_date: str,
    forward_windows: tuple[str, ...],
    forward_coverage_months: dict[str, int],
) -> None:
    if not rows:
        return

    as_of_dt = _as_of_date_parse(as_of_date)
    latest: dict[str, datetime] = {}

    for row in rows:
        company = row.get("company_id")
        if not isinstance(company, str):
            continue
        price_dt = _parse_datetime(row.get("price_date"))
        if price_dt is None:
            continue

        current = latest.get(company)
        if current is None or price_dt > current:
            latest[company] = price_dt

    if not latest:
        return

    for company, max_dt in latest.items():
        for window in forward_windows:
            required_months = forward_coverage_months.get(window, _window_months(window))
            required_end = _add_months(as_of_dt, required_months)
            if max_dt.date() < required_end.date():
                raise ValueError(
                    f"forward-coverage violated for run/company/window: run_id unknown, company={company}, "
                    f"holding_window={window}, last_available_date={max_dt.date()}, required_end_date={required_end.date()}"
                )



def _write_validation_market_prices(
    validation_dir: Path,
    run_id: str,
    as_of_date: str,
    companies: list[str],
    source_dir: Path | None = None,
    field_aliases: dict[str, tuple[str, ...]] | None = None,
    *,
    forward_windows: tuple[str, ...] = DEFAULT_FORWARD_WINDOWS,
    forward_coverage_months: dict[str, int] | None = None,
    reject_insufficient_forward_data: bool = True,
    reject_missing_available_at: bool = True,
) -> list[dict[str, str | float | None]]:
    input_rows = _read_rows_from_dir(source_dir)
    rows = _normalize_market_rows(
        input_rows,
        run_id,
        companies,
        as_of_date,
        field_aliases=field_aliases,
        reject_missing_available_at=reject_missing_available_at,
    )
    if not rows:
        raise ValueError("no market data rows found for validation input")
    if reject_missing_available_at:
        _assert_no_future_available_dates(
            rows,
            as_of_date,
            row_type="market",
        )
    if reject_insufficient_forward_data:
        _assert_forward_coverage(
            rows,
            as_of_date,
            forward_windows=forward_windows,
            forward_coverage_months=forward_coverage_months or {"1M": 1, "3M": 3},
        )

    out_path = validation_dir / "market_prices.parquet"
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_path)
    return rows



def _write_validation_fundamentals(
    validation_dir: Path,
    run_id: str,
    as_of_date: str,
    companies: list[str],
    include_fundamentals: bool,
    source_dir: Path | None = None,
    field_aliases: dict[str, tuple[str, ...]] | None = None,
    metric_names: list[str] | tuple[str, ...] | None = None,
    reject_missing_available_at: bool = True,
) -> None:
    out_path = validation_dir / "fundamentals.parquet"
    if not include_fundamentals:
        empty = pa.Table.from_pydict(
            {
                "schema_version": pa.array([], type=pa.string()),
                "run_id": pa.array([], type=pa.string()),
                "as_of_date": pa.array([], type=pa.string()),
                "company_id": pa.array([], type=pa.string()),
                "ticker": pa.array([], type=pa.string()),
                "period_end": pa.array([], type=pa.string()),
                "metric_name": pa.array([], type=pa.string()),
                "metric_value": pa.array([], type=pa.float64()),
                "unit": pa.array([], type=pa.string()),
                "currency": pa.array([], type=pa.string()),
                "filing_date": pa.array([], type=pa.string()),
                "available_at": pa.array([], type=pa.string()),
                "source": pa.array([], type=pa.string()),
                "source_id": pa.array([], type=pa.string()),
                "created_at": pa.array([], type=pa.string()),
            }
        )
        pq.write_table(empty, out_path)
        return

    metric_names = list(metric_names or FUNDAMENTAL_DEFAULT_METRICS)
    input_rows = _read_rows_from_dir(source_dir)
    rows = _normalize_fundamental_rows(
        input_rows,
        run_id,
        companies,
        as_of_date,
        metric_names,
        field_aliases=field_aliases,
        reject_missing_available_at=reject_missing_available_at,
    )
    if not rows:
        raise ValueError("no fundamental rows found for validation input")
    if reject_missing_available_at:
        _assert_no_future_available_dates(
            rows,
            as_of_date,
            row_type="fundamentals",
        )

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_path)



def _write_validation_portfolios(
    validation_dir: Path,
    run_id: str,
    as_of_date: str,
    companies: list[str],
) -> list[str]:
    now = _utc_now_iso()
    basket_id = "theme_snapshot_0001"
    rows: list[dict[str, str | int | float]] = []
    theme_snapshot_id = "theme_0001"
    community_id = "community_0001"
    total = len(companies)
    if total <= 0:
        total = 1
        companies = ["DEMO_COMPANY_A"]
    weight = 1.0 / total
    for rank, company in enumerate(companies, start=1):
        rows.append(
            {
                "schema_version": "v1",
                "run_id": run_id,
                "as_of_date": as_of_date,
                "basket_id": basket_id,
                "theme_snapshot_id": theme_snapshot_id,
                "community_id": community_id,
                "portfolio_method": "equal_weight",
                "selection_rank": rank,
                "company_id": company,
                "ticker": company[:4],
                "exposure_score": 1.0,
                "weight": weight,
                "inclusion_reason": "data_directed_preflight",
                "calculation_method": "equal_weight_preflight",
                "created_at": now,
            }
        )

    table = pa.Table.from_pylist(rows)
    out_path = validation_dir / "portfolio_baskets.parquet"
    pq.write_table(table, out_path)
    return [basket_id]



def _write_validation_report(
    validation_dir: Path,
    run_id: str,
    as_of_date: str,
    basket_ids: list[str],
    company_count: int,
    forward_windows: tuple[str, ...] = DEFAULT_FORWARD_WINDOWS,
    validation_status: str = "validated",
    market_data_source: str = "market_input",
) -> None:
    out_path = validation_dir / "validation.csv"
    as_of_dt = _as_of_date_parse(as_of_date)

    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
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
                "validation_status",
                "created_at",
            ],
        )
        writer.writeheader()
        for basket_id in basket_ids:
            for forward in forward_windows:
                caveats = (
                    "backtesting requires temporal panel and is not meaningful for single-snapshot inputs."
                    if validation_status == "disabled_not_enough_snapshots"
                    else "deterministic_preflight"
                )
                writer.writerow(
                    {
                        "schema_version": "v1",
                        "run_id": run_id,
                        "as_of_date": as_of_date,
                        "basket_id": basket_id,
                        "theme_snapshot_id": basket_id.replace("theme_snapshot", "theme"),
                        "community_id": "community_0001",
                        "theme_name": "theme_1",
                        "forward_window": forward,
                        "portfolio_method": "equal_weight",
                        "company_count": company_count,
                        "start_date": as_of_date,
                        "end_date": _add_months(as_of_dt, _window_months(forward)).strftime("%Y-%m-%d"),
                        "theme_basket_return": "0.0",
                        "benchmark_name": "equal_weight_universe",
                        "benchmark_return": "0.0",
                        "excess_return": "0.0",
                        "sample_size": str(company_count),
                        "market_data_source": market_data_source,
                        "caveats": caveats,
                        "validation_status": validation_status,
                        "created_at": _utc_now_iso(),
                    }
                )



def run_validation(
    run_id: str,
    *,
    market_data_dir: str | None = None,
    fundamentals_data_dir: str | None = None,
    include_fundamentals: bool = False,
) -> ValidationArtifacts:
    manifest = runs.validate_ready_for_validation(run_id)
    config = _load_validation_config(manifest.validation_config)
    validation_status = _walk_forward_backtest_status(config, manifest)

    run_dir = runs.get_run_dir(run_id)
    validation_dir = run_dir / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)

    companies = _as_discovery_company_ids(run_id)
    market_dir = _require_existing_path(market_data_dir, label="market_data_dir")
    if market_dir is None:
        raise ValueError("market_data_dir is required for validation")
    fundamentals_dir = _require_existing_path(fundamentals_data_dir, label="fundamentals_data_dir")

    if include_fundamentals and fundamentals_dir is None:
        raise ValueError("include_fundamentals is true but fundamentals_data_dir is not provided")

    _write_validation_market_prices(
        validation_dir,
        run_id,
        manifest.as_of_date,
        companies,
        source_dir=market_dir,
        field_aliases=config.field_mappings.get("market"),
        forward_windows=config.forward_windows,
        forward_coverage_months=config.forward_coverage_months,
        reject_insufficient_forward_data=config.reject_insufficient_forward_data,
        reject_missing_available_at=config.reject_missing_available_at,
    )
    _write_validation_fundamentals(
        validation_dir,
        run_id,
        manifest.as_of_date,
        companies,
        include_fundamentals=include_fundamentals,
        source_dir=fundamentals_dir,
        field_aliases=config.field_mappings.get("fundamentals"),
        metric_names=config.optional_fundamentals,
        reject_missing_available_at=config.reject_missing_available_at,
    )
    basket_ids = _write_validation_portfolios(
        validation_dir,
        run_id,
        manifest.as_of_date,
        companies,
    )
    _write_validation_report(
        validation_dir,
        run_id,
        manifest.as_of_date,
        basket_ids,
        len(companies),
        validation_status=validation_status,
        forward_windows=config.forward_windows,
        market_data_source="market_input",
    )

    return ValidationArtifacts(
        artifacts=[
            "validation/market_prices.parquet",
            "validation/fundamentals.parquet",
            "validation/portfolio_baskets.parquet",
            "validation/validation.csv",
        ],
        validated_themes=len(basket_ids),
        validation_status=validation_status,
    )
