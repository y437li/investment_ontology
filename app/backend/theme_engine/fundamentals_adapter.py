"""As-reported fundamentals adapter: EDGAR XBRL ingestion (US-listed).

Reads a locally stored EDGAR *company facts* JSON (the payload returned by
``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``) and emits
as-reported financial metric rows into the discovery-time fundamentals
artifact ``discovery/fundamentals_asreported.parquet``.

THIS IS A DISCOVERY ARTIFACT — completely separate from the validation-only
``validation/fundamentals.parquet`` (io_contracts §20). Discovery stages
must never read or write the §20 artifact; that path is frozen-gated and
reserved for validation.

Design constraints (load-bearing; match macro_adapter / altdata_adapter):

- METRIC NAMES from config only. Every metric_name emitted is drawn from
  ``configs/fundamentals.yml``; none are hardcoded here.
- POINT-IN-TIME: available_at = filing_date (the date the document became
  public). Period end (reportDate) is NOT used as available_at.
- AS-REPORTED values only: the first-published XBRL value for each
  (company_id, period_end, metric_name) tuple. Later restatements are
  stored as new vintages but do not overwrite prior rows in the artifact.
- NO NETWORK: this module reads only local files. It imports no HTTP
  libraries. Callers download the company-facts JSON and pass its path.
- DETERMINISTIC: given the same input files and config, produces the same
  artifact (no wall-clock or RNG dependency).
- EMPTY-SAFE: when no XBRL file exists for a company, the adapter writes a
  schema-valid empty artifact (zero rows, correct columns).

Reconciliation note (shared B1/B2 contract):
  Key = (company_id, period_end, metric_name).
  For any as-reported overlap, B1 (XBRL) values win; B2 (LLM) owns
  guidance / forward-looking / narrative numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import runs

# ---------------------------------------------------------------------------
# Canonical schema for the discovery-time as-reported fundamentals artifact.
# This is the authoritative column list for fundamentals_asreported.parquet.
# ---------------------------------------------------------------------------

FUNDAMENTALS_COLUMNS: list[str] = [
    "company_id",
    "period_end",
    "metric_name",
    "metric_value",
    "unit",
    "currency",
    "filing_date",
    "available_at",
    "source",
    "source_id",
]

# Discovery artifact filename (never the §20 validation path).
FUNDAMENTALS_ARTIFACT = "fundamentals_asreported.parquet"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DATE_FMT = "%Y-%m-%d"


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "configs"))


def _load_fundamentals_config() -> dict:
    p = _config_dir() / "fundamentals.yml"
    if not p.exists():
        return {}
    import yaml  # noqa: PLC0415
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _metric_index(cfg: dict) -> dict[str, dict]:
    """Build {concept_name: metric_cfg} lookup from the config metric list.

    A concept may appear in multiple metrics (e.g. Revenues as denominator for
    margin computation). We resolve by direct-match first, then derived.
    """
    direct: dict[str, dict] = {}
    for m in cfg.get("metrics", []):
        for concept in m.get("xbrl_concepts") or []:
            if concept not in direct:
                direct[concept] = m
    return direct


def _derived_index(cfg: dict) -> list[dict]:
    """Return metrics that are computed (gross_margin, operating_margin, etc.)."""
    return [m for m in cfg.get("metrics", []) if m.get("derived_from")]


def _to_date_str(s: str) -> str | None:
    """Normalize a date string to YYYY-MM-DD, return None if invalid."""
    try:
        d = date.fromisoformat(s)
        return d.strftime(_DATE_FMT)
    except Exception:
        return None


def _source_id(company_id: str, accession: str) -> str:
    """Stable source_id from company + accession number."""
    return hashlib.sha256(f"{company_id}|{accession}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# EDGAR company-facts JSON parsing
# ---------------------------------------------------------------------------

def _parse_company_facts(
    facts_json: dict,
    company_id: str,
    as_of: date | None,
    metric_idx: dict[str, dict],
    derived: list[dict],
) -> list[dict]:
    """Parse an EDGAR company-facts JSON dict into as-reported rows.

    Parameters
    ----------
    facts_json:
        Parsed content of a CIK########.json company-facts file.
    company_id:
        Canonical company identifier (ticker or CIK-based).
    as_of:
        If provided, only rows with available_at <= as_of are returned
        (PIT filter). Pass None to return all rows (useful for tests).
    metric_idx:
        {concept_name: metric_cfg} from _metric_index().
    derived:
        List of metric configs that require computation across concepts.
    """
    us_gaap = (facts_json.get("facts") or {}).get("us-gaap") or {}
    rows: list[dict] = []

    # --- Step 1: direct concept -> metric_name mapping ---
    # Collect raw values by (concept, period_end, filing_date) first so we
    # can reuse them for margin computation.
    raw_by_concept: dict[str, dict[tuple[str, str], dict]] = {}

    for concept_name, concept_data in us_gaap.items():
        units_map = (concept_data.get("units") or {})
        for unit_str, observations in units_map.items():
            for obs in (observations or []):
                end = _to_date_str(obs.get("end") or "")
                filed = _to_date_str(obs.get("filed") or "")
                if not end or not filed:
                    continue
                val = obs.get("val")
                if val is None:
                    continue
                # PIT filter
                if as_of and date.fromisoformat(filed) > as_of:
                    continue

                accn = obs.get("accn") or ""
                form = obs.get("form") or ""

                # Only annual and quarterly periodical filings (10-K / 10-Q);
                # skip instant-point balance sheet observations (no start/end span
                # distinction here — EDGAR includes both; we take all with a filed date).
                bucket = raw_by_concept.setdefault(concept_name, {})
                key = (end, filed)
                # If we already have this (concept, period_end, filing_date),
                # keep the most recent filing (later filed date wins if same period).
                existing = bucket.get(key)
                if existing is None or filed > existing["filed"]:
                    bucket[key] = {
                        "val": val,
                        "unit": unit_str,
                        "filed": filed,
                        "accn": accn,
                        "form": form,
                    }

                # Emit direct metrics
                if concept_name in metric_idx:
                    m = metric_idx[concept_name]
                    currency = unit_str if unit_str.startswith("USD") or unit_str == "CAD" else None
                    rows.append({
                        "company_id": company_id,
                        "period_end": end,
                        "metric_name": m["metric_name"],
                        "metric_value": float(val),
                        "unit": m.get("unit") or unit_str,
                        "currency": currency,
                        "filing_date": filed,
                        "available_at": filed,
                        "source": "edgar_xbrl",
                        "source_id": _source_id(company_id, accn),
                    })

    # --- Step 2: derived / computed metrics ---
    for m in derived:
        df = m.get("derived_from") or {}
        num_concepts = df.get("numerator_concepts") or []
        den_concepts = df.get("denominator_concepts") or []

        # Collect all (period_end, filing_date) pairs where both numerator and
        # denominator are available.
        num_map = _best_values(raw_by_concept, num_concepts)
        den_map = _best_values(raw_by_concept, den_concepts)

        for (end, filed), num_obs in num_map.items():
            den_obs = den_map.get((end, filed))
            if den_obs is None or den_obs["val"] == 0:
                continue
            if as_of and date.fromisoformat(filed) > as_of:
                continue
            ratio = num_obs["val"] / den_obs["val"]
            rows.append({
                "company_id": company_id,
                "period_end": end,
                "metric_name": m["metric_name"],
                "metric_value": round(float(ratio), 6),
                "unit": "ratio",
                "currency": None,
                "filing_date": filed,
                "available_at": filed,
                "source": "edgar_xbrl",
                "source_id": _source_id(company_id, num_obs["accn"]),
            })

    # --- Step 3: deduplicate on (company_id, period_end, metric_name) ---
    # Keep the row with the EARLIEST filing_date per key (as-reported = first
    # published; later restatements are newer filings and are excluded).
    deduped: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (row["company_id"], row["period_end"], row["metric_name"])
        existing = deduped.get(key)
        if existing is None or row["filing_date"] < existing["filing_date"]:
            deduped[key] = row

    return list(deduped.values())


def _best_values(
    raw_by_concept: dict[str, dict[tuple[str, str], dict]],
    concept_list: list[str],
) -> dict[tuple[str, str], dict]:
    """Return the best available (period_end, filing_date) -> obs dict for a
    list of fallback concepts. The first concept with any data wins per period."""
    result: dict[tuple[str, str], dict] = {}
    for concept in concept_list:
        bucket = raw_by_concept.get(concept) or {}
        for key, obs in bucket.items():
            if key not in result:
                result[key] = obs
    return result


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _empty_table() -> pa.Table:
    """Return a schema-valid empty fundamentals_asreported table."""
    schema = pa.schema([
        pa.field("company_id", pa.string()),
        pa.field("period_end", pa.string()),
        pa.field("metric_name", pa.string()),
        pa.field("metric_value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("currency", pa.string()),
        pa.field("filing_date", pa.string()),
        pa.field("available_at", pa.string()),
        pa.field("source", pa.string()),
        pa.field("source_id", pa.string()),
    ])
    return pa.table({col: pa.array([], type=f.type)
                     for col, f in zip(schema.names, schema)},
                   schema=schema)


def _rows_to_table(rows: list[dict]) -> pa.Table:
    if not rows:
        return _empty_table()
    return pa.Table.from_pylist([
        {col: row.get(col) for col in FUNDAMENTALS_COLUMNS}
        for row in rows
    ])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_xbrl(
    run_id: str,
    company_id: str,
    facts_json_path: str | Path | None,
    *,
    config_path: str | Path | None = None,
) -> dict:
    """Ingest one company's EDGAR company-facts JSON into the run's discovery
    fundamentals artifact.

    Parameters
    ----------
    run_id:
        The pipeline run identifier.
    company_id:
        Canonical company identifier (e.g. ticker "AAPL" or "CIK320193").
    facts_json_path:
        Path to the local company-facts JSON file. Pass None to emit an
        empty-but-schema-valid artifact for companies with no XBRL.
    config_path:
        Optional override for the fundamentals config file path (for tests).

    Returns
    -------
    dict with keys: rows_written, metrics_found, periods_found.
    """
    # Load config
    if config_path is not None:
        import yaml  # noqa: PLC0415
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    else:
        cfg = _load_fundamentals_config()

    metric_idx = _metric_index(cfg)
    derived = _derived_index(cfg)

    # Load run manifest for as_of
    rd = runs.get_run_dir(run_id)
    manifest_path = rd / "run_manifest.json"
    as_of: date | None = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        as_of_str = manifest.get("as_of_date")
        if as_of_str:
            try:
                as_of = date.fromisoformat(as_of_str)
            except ValueError:
                as_of = None

    # Parse XBRL facts
    rows: list[dict] = []
    if facts_json_path is not None:
        p = Path(facts_json_path)
        if p.exists():
            facts_json = json.loads(p.read_text(encoding="utf-8"))
            rows = _parse_company_facts(facts_json, company_id, as_of, metric_idx, derived)

    # Write (or append to) the discovery artifact
    discovery_dir = rd / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = discovery_dir / FUNDAMENTALS_ARTIFACT

    new_table = _rows_to_table(rows)

    if artifact_path.exists():
        existing = pq.read_table(artifact_path)
        # Exclude any existing rows for this company (idempotent re-ingest).
        existing_rows = existing.to_pylist()
        keep = [r for r in existing_rows if r.get("company_id") != company_id]
        combined_rows = keep + rows
        combined_table = _rows_to_table(combined_rows)
        pq.write_table(combined_table, artifact_path)
    else:
        pq.write_table(new_table, artifact_path)

    periods = {r["period_end"] for r in rows}
    metrics = {r["metric_name"] for r in rows}
    return {
        "rows_written": len(rows),
        "metrics_found": sorted(metrics),
        "periods_found": sorted(periods),
    }


def read_fundamentals(run_id: str) -> list[dict]:
    """Return all rows from the run's discovery fundamentals artifact."""
    rd = runs.get_run_dir(run_id)
    artifact_path = rd / "discovery" / FUNDAMENTALS_ARTIFACT
    if not artifact_path.exists():
        return []
    return pq.read_table(artifact_path).to_pylist()


def read_company_fundamentals(
    run_id: str,
    company_id: str,
    as_of: date | None = None,
) -> list[dict]:
    """Return as-reported rows for one company, optionally filtered by as_of.

    Only rows with available_at <= as_of are returned (PIT discipline).
    """
    rows = read_fundamentals(run_id)
    result = [r for r in rows if r.get("company_id") == company_id]
    if as_of is not None:
        result = [
            r for r in result
            if r.get("available_at") and r["available_at"] <= as_of.strftime(_DATE_FMT)
        ]
    return result
