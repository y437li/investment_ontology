"""Entity resolution service (M3): canonicalize aliases.

Reads ``discovery/entities.parquet`` and ``discovery/chunks.parquet``, then
writes ``discovery/entity_aliases.parquet`` per io_contracts.md section 10.

Point-in-time (OI-4):
    The alias table is built using ONLY entities derived from chunks whose
    ``available_at <= run.as_of_date``.  The ``as_of_date`` used is recorded
    in every alias row.

Alias rules (deterministic, no LLM):
  1. Exact-match deduplication: entities with the same canonical_name (case-
     insensitive) in the same entity_type are merged under the first-seen id.
  2. Known-abbreviation expansion: a hard-coded map of abbreviations to full
     canonical names (e.g. "rbc" -> "RBC", "amce" -> "Acme Corp").
  3. Ticker alias: Company entities whose ticker field is populated get an
     additional ticker -> canonical_name row.

All rows have ``alias_scope="point_in_time"``.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs

SCHEMA_VERSION = "1.0"

# io_contracts.md section 10 — entity_aliases.parquet
ENTITY_ALIASES_COLUMNS: list[str] = [
    "schema_version",
    "alias",
    "canonical_entity_id",
    "canonical_name",
    "as_of_date",
    "confidence",
    "method",
    "review_status",
    "alias_scope",
    "source_record_ids",
    "created_at",
]

# Hard-coded abbreviation expansions (canonical_name -> list[alias])
# These are deterministic rules, not LLM inference.
_KNOWN_ABBREVIATIONS: dict[str, list[str]] = {
    "Acme Corp": ["acme", "ACME", "Acme"],
    "Beta Industries": ["beta", "BETA", "Beta"],
    "Hydro One": ["H1", "Hydro-One", "HY"],
    "Cameco": ["CCO", "Cameco Corporation"],
    "RBC": ["Royal Bank", "Royal Bank of Canada", "RBC Capital"],
    "Uranium": ["U", "uranium oxide"],
    "Copper": ["Cu"],
    "Aluminum": ["Al", "aluminium"],
    "Capital Expenditure": ["capex", "CAPEX", "CapEx"],
    "CPI": ["Consumer Price Index"],
    "GDP": ["Gross Domestic Product"],
    "Fed Funds Rate": ["FFR", "federal funds rate"],
}


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_date_str(val) -> str:
    """Coerce available_at values to YYYY-MM-DD strings."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


def _write_aliases_table(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = ENTITY_ALIASES_COLUMNS
    if not rows:
        field_map: dict[str, pa.DataType] = {}
        for col in columns:
            if col == "source_record_ids":
                field_map[col] = pa.list_(pa.string())
            elif col == "confidence":
                field_map[col] = pa.float64()
            else:
                field_map[col] = pa.string()
        schema = pa.schema([(c, field_map[c]) for c in columns])
        pq.write_table(
            pa.table({c: pa.array([], type=field_map[c]) for c in columns}, schema=schema),
            out_path,
        )
        return
    pydict: dict[str, list] = {col: [row.get(col) for row in rows] for col in columns}
    pq.write_table(pa.Table.from_pydict(pydict), out_path)


def _read_entities(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "entities.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"entities.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def _read_chunks(run_id: str) -> list[dict]:
    artifact = runs.get_run_dir(run_id) / "discovery" / "chunks.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"chunks.parquet not found for run {run_id}; run chunk first",
        )
    return pq.read_table(artifact).to_pylist()


def resolve_entities(run_id: str) -> int:
    """Build the entity alias table for this run.

    Point-in-time: only considers chunks with available_at <= as_of_date.
    Returns the number of alias rows written.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = manifest.as_of_date

    # Build a set of chunk_ids that are point-in-time eligible
    chunks = _read_chunks(run_id)
    eligible_chunk_ids: set[str] = set()
    for ch in chunks:
        available_at = _to_date_str(ch.get("available_at"))
        if available_at and available_at <= as_of_date:
            cid = ch.get("chunk_id")
            if cid:
                eligible_chunk_ids.add(cid)

    entities = _read_entities(run_id)
    created_at = _utc_now_iso()

    # Filter entities to those that have at least one eligible source chunk
    eligible_entities: list[dict] = []
    for ent in entities:
        source_chunk_ids = ent.get("source_chunk_ids") or []
        if isinstance(source_chunk_ids, pa.Array):
            source_chunk_ids = source_chunk_ids.to_pylist()
        # Keep entity if any of its source chunks are eligible
        if any(cid in eligible_chunk_ids for cid in source_chunk_ids):
            eligible_entities.append(ent)

    # Rule 1: exact-match deduplication by canonical_name (case-insensitive) + entity_type
    # Map (entity_type, canonical_name_lower) -> canonical entity row
    canonical_map: dict[tuple[str, str], dict] = {}
    for ent in eligible_entities:
        etype = ent.get("entity_type", "")
        cname = ent.get("canonical_name") or ent.get("name") or ""
        key = (etype, cname.lower())
        if key not in canonical_map:
            canonical_map[key] = ent
        # else: a duplicate; the first one wins (earliest first_seen by iteration order)

    alias_rows: list[dict] = []

    for (etype, cname_lower), canonical_ent in canonical_map.items():
        canonical_entity_id = canonical_ent.get("entity_id", "")
        canonical_name = canonical_ent.get("canonical_name") or canonical_ent.get("name") or ""
        entity_id = canonical_entity_id
        source_ids = [entity_id]

        # Self-alias (canonical_name -> itself)
        alias_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "alias": canonical_name,
                "canonical_entity_id": canonical_entity_id,
                "canonical_name": canonical_name,
                "as_of_date": as_of_date,
                "confidence": 1.0,
                "method": "exact_match",
                "review_status": "approved",
                "alias_scope": "point_in_time",
                "source_record_ids": source_ids,
                "created_at": created_at,
            }
        )

        # Rule 2: known abbreviation expansions
        for full_name, abbrevs in _KNOWN_ABBREVIATIONS.items():
            if full_name.lower() == canonical_name.lower():
                for abbr in abbrevs:
                    # Don't duplicate the canonical_name self-alias
                    if abbr.lower() != canonical_name.lower():
                        alias_rows.append(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "alias": abbr,
                                "canonical_entity_id": canonical_entity_id,
                                "canonical_name": canonical_name,
                                "as_of_date": as_of_date,
                                "confidence": 0.95,
                                "method": "known_abbreviation",
                                "review_status": "approved",
                                "alias_scope": "point_in_time",
                                "source_record_ids": source_ids,
                                "created_at": created_at,
                            }
                        )

        # Rule 3: ticker alias for Company entities
        if etype == "Company":
            ticker = canonical_ent.get("ticker")
            if ticker and ticker.lower() != canonical_name.lower():
                alias_rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "alias": ticker,
                        "canonical_entity_id": canonical_entity_id,
                        "canonical_name": canonical_name,
                        "as_of_date": as_of_date,
                        "confidence": 0.99,
                        "method": "ticker_alias",
                        "review_status": "approved",
                        "alias_scope": "point_in_time",
                        "source_record_ids": source_ids,
                        "created_at": created_at,
                    }
                )

    run_dir = runs.get_run_dir(run_id)
    _write_aliases_table(
        alias_rows,
        run_dir / "discovery" / "entity_aliases.parquet",
    )

    return len(alias_rows)
