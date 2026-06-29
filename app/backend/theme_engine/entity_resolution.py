"""Entity resolution service (M3): canonicalize aliases.

Reads ``discovery/entities.parquet`` and ``discovery/chunks.parquet``, then
writes two alias artifacts per io_contracts.md section 10 / 10a:

Point-in-time table — ``discovery/entity_aliases.parquet`` (OI-4):
    Built using ONLY entities derived from chunks whose
    ``available_at <= run.as_of_date``.  The ``as_of_date`` used is recorded
    in every alias row.  alias_scope="point_in_time".
    THIS IS THE TABLE CONSUMED BY Graph(t) / exposure / community detection.

Global companion table — ``discovery/entity_aliases_global.parquet`` (OI-4):
    Built over the FULL corpus (ALL entities/chunks regardless of
    ``available_at`` — no PIT filter).  alias_scope="global",
    as_of_date="" (not applicable).  FOR NON-TEMPORAL INSPECTION ONLY.
    This table MUST NOT be read by graph_build, exposure, community
    detection, or any other discovery-stage computation.

Alias rules (deterministic, no LLM):
  1. Exact-match deduplication: entities with the same canonical_name (case-
     insensitive) in the same entity_type are merged under the first-seen id.
  2. Known-abbreviation expansion: a hard-coded map of abbreviations to full
     canonical names (e.g. "rbc" -> "RBC", "amce" -> "Acme Corp").
  3. Ticker alias: Company entities whose ticker field is populated get an
     additional ticker -> canonical_name row.
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

# Sentinel stored in entity_aliases_global.parquet as_of_date to make clear
# the table has no PIT scope.  Consumers must never treat this as a real date.
GLOBAL_AS_OF_SENTINEL: str = ""

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


def _read_entities(run_id: str, as_of: str | None = None) -> list[dict]:
    artifact = runs.discovery_point_dir(run_id, as_of) / "entities.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"entities.parquet not found for run {run_id}; run extraction first",
        )
    return pq.read_table(artifact).to_pylist()


def _read_chunks(run_id: str, as_of: str | None = None) -> list[dict]:
    artifact = runs.discovery_point_dir(run_id, as_of) / "chunks.parquet"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"chunks.parquet not found for run {run_id}; run chunk first",
        )
    return pq.read_table(artifact).to_pylist()


def _filter_pit_entities(
    entities: list[dict],
    chunks: list[dict],
    as_of_date: str,
) -> list[dict]:
    """Return entities that have at least one chunk with available_at <= as_of_date.

    This is the point-in-time gate: only entities whose evidence is available
    at or before as_of_date are included in Graph(t).
    """
    eligible_chunk_ids: set[str] = set()
    for ch in chunks:
        available_at = _to_date_str(ch.get("available_at"))
        if available_at and available_at <= as_of_date:
            cid = ch.get("chunk_id")
            if cid:
                eligible_chunk_ids.add(cid)

    eligible: list[dict] = []
    for ent in entities:
        source_chunk_ids = ent.get("source_chunk_ids") or []
        if isinstance(source_chunk_ids, pa.Array):
            source_chunk_ids = source_chunk_ids.to_pylist()
        if any(cid in eligible_chunk_ids for cid in source_chunk_ids):
            eligible.append(ent)
    return eligible


def _build_alias_rows(
    entities: list[dict],
    as_of_date: str,
    alias_scope: str,
    created_at: str,
) -> list[dict]:
    """Apply alias rules to *entities* and return alias rows.

    This is the shared alias-rule engine used for both the PIT table
    (alias_scope="point_in_time") and the global table (alias_scope="global").

    Rules applied (deterministic, no LLM):
      1. Exact-match deduplication by (entity_type, canonical_name lower-case).
      2. Known-abbreviation expansion (direction-agnostic; audit HIGH #75).
      3. Ticker alias for Company entities.

    Args:
        entities:   Pre-filtered entity rows (already PIT-gated or full-corpus).
        as_of_date: String stored in the as_of_date column of every row.
                    Use run.as_of_date for PIT rows; GLOBAL_AS_OF_SENTINEL for global.
        alias_scope: "point_in_time" or "global".
        created_at: ISO-8601 UTC timestamp string.

    Returns:
        List of alias row dicts matching ENTITY_ALIASES_COLUMNS.
    """
    # Rule 1: exact-match deduplication by canonical_name (case-insensitive) + entity_type
    # Map (entity_type, canonical_name_lower) -> canonical entity row
    canonical_map: dict[tuple[str, str], dict] = {}
    for ent in entities:
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
        source_ids = [canonical_entity_id]

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
                "alias_scope": alias_scope,
                "source_record_ids": source_ids,
                "created_at": created_at,
            }
        )

        # Rule 2: known abbreviation expansions. DIRECTION-AGNOSTIC (audit HIGH #75):
        # the entity's canonical_name may be EITHER the long form (extraction
        # canonicalizes RBC -> "Royal Bank of Canada") OR the short key, so match
        # against the whole group (key + aliases) and emit every OTHER member as an
        # alias. This guarantees the alias rows are emitted regardless of which form
        # the extractor chose as canonical.
        cn_lower = canonical_name.lower()
        for full_name, abbrevs in _KNOWN_ABBREVIATIONS.items():
            group = [full_name, *abbrevs]
            if any(member.lower() == cn_lower for member in group):
                for member in group:
                    if member.lower() != cn_lower:
                        alias_rows.append(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "alias": member,
                                "canonical_entity_id": canonical_entity_id,
                                "canonical_name": canonical_name,
                                "as_of_date": as_of_date,
                                "confidence": 0.95,
                                "method": "known_abbreviation",
                                "review_status": "approved",
                                "alias_scope": alias_scope,
                                "source_record_ids": source_ids,
                                "created_at": created_at,
                            }
                        )
                break  # one abbreviation group per entity

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
                        "alias_scope": alias_scope,
                        "source_record_ids": source_ids,
                        "created_at": created_at,
                    }
                )

    return alias_rows


def resolve_entities(run_id: str, as_of: str | None = None) -> int:
    """Build the entity alias tables for this run.

    Writes TWO artifacts (OI-4 discipline):

    1. ``discovery/entity_aliases.parquet`` — point-in-time (PIT) table.
       Uses ONLY entities derived from chunks with available_at <= as_of_date.
       alias_scope="point_in_time", as_of_date=run.as_of_date.
       THIS IS THE TABLE CONSUMED BY Graph(t) / exposure / community detection.

    2. ``discovery/entity_aliases_global.parquet`` — global companion table.
       Uses ALL entities/chunks regardless of available_at (full corpus).
       alias_scope="global", as_of_date="" (GLOBAL_AS_OF_SENTINEL).
       FOR NON-TEMPORAL INSPECTION ONLY — must never be read by discovery
       computation (graph_build, exposure, themes, community detection).

    The PIT table behavior is UNCHANGED from pre-OI-4: the same rules, the
    same available_at <= as_of_date gate, the same output columns.

    Returns the number of PIT alias rows written.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = as_of if as_of is not None else manifest.as_of_date

    chunks = _read_chunks(run_id, as_of)
    entities = _read_entities(run_id, as_of)
    created_at = _utc_now_iso()

    # --- PIT table: filter entities to those with available_at <= as_of_date ---
    pit_entities = _filter_pit_entities(entities, chunks, as_of_date)
    pit_rows = _build_alias_rows(pit_entities, as_of_date, "point_in_time", created_at)

    discovery_dir = runs.discovery_point_dir(run_id, as_of, for_write=True)

    _write_aliases_table(pit_rows, discovery_dir / "entity_aliases.parquet")

    # --- Global companion table: full corpus, no PIT filter ---
    global_rows = _build_alias_rows(
        entities, GLOBAL_AS_OF_SENTINEL, "global", created_at
    )
    _write_aliases_table(global_rows, discovery_dir / "entity_aliases_global.parquet")

    return len(pit_rows)
