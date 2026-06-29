"""FI-C: projected_impacts artifact writer (GitHub #106).

Selects data-driven triggers from the PIT graph, calls propagation.propagate()
for each trigger, and persists results as ``projected_impacts.parquet``.

Workstream P-C per docs/design_forward_inference.md.
Depends on FI-B (propagation.propagate) and FI-A (graph.json with polarity +
propagation_weight).

Trigger selection (v1 — data-driven)
--------------------------------------
Triggers are **Event nodes** present in graph.json.  Event is a first-class
structural node type (STRUCTURAL_NODE_TYPES in graph_build.py includes "Event").
Events represent discrete occurrences (e.g. "Trade war tariffs 2025",
"Fed rate hike March 2024") that naturally serve as forward-inference trigger
points: an event occurs and its effects propagate through causal / directional
edges (``causes``, ``benefits``, ``hurts``, ``exposed_to``, ``sensitive_to``)
to Company nodes.

Defensibility:
- Event nodes are already in the PIT-filtered graph (first_seen_at <= as_of_date).
- They require no user input — triggers are whatever Event entities the extraction
  pipeline found in the source corpus.
- Their outgoing edges carry polarity + propagation_weight (FI-A substrate) so
  FI-B propagation is directly usable.
- Unlike MacroIndicator or EconomicConcept nodes, Events are episodic; selecting
  them as triggers models "what happens when THIS event activates?" which is the
  natural FI framing.

Shock convention
----------------
v1 uses shock = +1.0 for all Event triggers (the event "occurs" / activates).
Consumers interpret direction: +1 means the event is positive for the company,
-1 means negative.  This is provisional until issue #110 adds per-instance edge
direction, which will make the sign reliable for causes/exposed_to/sensitive_to
edges.

Artifact
--------
Path: ``data/runs/<run_id>/discovery/projected_impacts.parquet``
io_contracts §FI-C.

PIT-clean by construction
--------------------------
graph.json is built by graph_build.py with first_seen_at <= as_of_date enforced
(fail-closed).  propagate() trusts this guarantee and does NOT re-filter edges.
The as_of_date field is inherited from the run manifest and recorded on every row.

Derived / regenerable
----------------------
projected_impacts.parquet is rebuilt every time compute_projected_impacts() is
called with the same run.  It is never "restated" — historical snapshots are
captured by separate run_ids, each with their own as_of_date.

Known limitation — #110 sign-blind causal / exposure edges
-----------------------------------------------------------
``causes``, ``exposed_to``, and ``sensitive_to`` edges currently have
base_polarity = +1 unconditionally.  Impacts derived SOLELY from those edge
types have provisional direction.  See propagation.py module docstring for the
full caveat.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from . import runs
from .propagation import propagate

SCHEMA_VERSION = "1.0"

# v1 default shock: Event occurs / activates with positive magnitude
_DEFAULT_SHOCK: float = 1.0

# Trigger entity type for v1
_TRIGGER_ENTITY_TYPE: str = "Event"

# Calculation method tag for this version
_METHOD: str = "propagation_v1_event_trigger"

# io_contracts §FI-C columns (exact order)
PROJECTED_IMPACTS_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "as_of_date",
    "trigger_id",
    "trigger_kind",
    "company_id",
    "direction",
    "strength",
    "path",
    "contributing_edge_ids",
    "evidence_chunk_ids",
    "confidence",
    "method",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_date_str(val: Any) -> str:
    """Coerce a value to YYYY-MM-DD string."""
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


def _read_graph(run_id: str, as_of: str | None = None) -> dict:
    artifact = runs.discovery_point_dir(run_id, as_of) / "graph.json"
    if not artifact.exists():
        raise HTTPException(
            status_code=404,
            detail=f"graph.json not found for run {run_id}; run graph/build first",
        )
    return json.loads(artifact.read_text(encoding="utf-8"))


def _build_edge_index(graph: dict) -> dict[str, dict]:
    """Build edge_id -> edge dict lookup for evidence resolution."""
    return {
        edge["edge_id"]: edge
        for edge in graph.get("edges", [])
        if edge.get("edge_id")
    }


def _resolve_evidence_chunk_ids(
    contributing_edge_ids: list[str],
    edge_index: dict[str, dict],
) -> list[str]:
    """Collect unique evidence_chunk_ids from all contributing edges.

    Preserves insertion order; deduplicates across edges.  Resolvable via
    source.py's chunk_source(run_id, chunk_id) chain.
    """
    seen: set[str] = set()
    result: list[str] = []
    for eid in contributing_edge_ids:
        edge = edge_index.get(eid)
        if edge is None:
            continue
        for chunk_id in edge.get("evidence_chunk_ids") or []:
            if chunk_id and chunk_id not in seen:
                seen.add(chunk_id)
                result.append(chunk_id)
    return result


def _mean_propagation_weight(
    path: list[str],
    edge_index: dict[str, dict],
) -> float:
    """Mean propagation_weight along the given path (list of edge_ids).

    Returns 0.0 for an empty path, 1.0 for an edge not found in the index.
    Gives an ordinal sense of causal signal fidelity for the primary path.
    """
    if not path:
        return 0.0
    weights = [
        float(edge_index.get(eid, {}).get("propagation_weight") or 1.0)
        for eid in path
    ]
    return sum(weights) / len(weights)


# ---------------------------------------------------------------------------
# Trigger selection
# ---------------------------------------------------------------------------


def select_triggers(graph: dict) -> list[dict]:
    """Return all Event nodes from the PIT graph as trigger candidates.

    Parameters
    ----------
    graph : dict
        Parsed graph.json dict.  Must already be PIT-filtered (graph_build.py
        guarantee: first_seen_at <= as_of_date for all nodes).

    Returns
    -------
    list[dict]
        Subset of ``graph["nodes"]`` where entity_type == "Event".
        Each element is the raw node dict (has at minimum ``entity_id`` and
        ``entity_type``).  Empty list if no Event nodes exist.
    """
    return [
        node
        for node in graph.get("nodes", [])
        if node.get("entity_type") == _TRIGGER_ENTITY_TYPE
        and node.get("entity_id")
    ]


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def compute_projected_impacts(
    run_id: str,
    shock: float = _DEFAULT_SHOCK,
    as_of: str | None = None,
) -> int:
    """Compute forward-inference projected impacts and write projected_impacts.parquet.

    For each Event node in graph.json, calls propagation.propagate() and
    collects per-company impacts.  Resolves contributing edge evidence_chunk_ids
    so every row is traceable via source.py's chunk_source() chain.

    Parameters
    ----------
    run_id : str
        The run to process.  graph.json must already exist (run graph/build first).
    shock : float
        Signed initial shock magnitude for all triggers.  Default +1.0 (event
        activates with positive magnitude; direction on each row reflects net
        causal sign from trigger to company).

    Returns
    -------
    int
        Number of projected_impact rows written.

    PIT guarantee
    -------------
    graph.json is already PIT-filtered by graph_build.py.  The as_of_date is
    taken from the run manifest and stamped on every row.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    as_of_date: str = as_of if as_of is not None else manifest.as_of_date

    graph = _read_graph(run_id, as_of)
    edge_index = _build_edge_index(graph)

    triggers = select_triggers(graph)

    rows: list[dict] = []

    for trigger_node in triggers:
        trigger_id: str = trigger_node["entity_id"]
        trigger_kind: str = trigger_node.get("entity_type", _TRIGGER_ENTITY_TYPE)

        impacts = propagate(
            graph,
            trigger_id=trigger_id,
            shock=shock,
            as_of_date=as_of_date,
        )

        for impact in impacts:
            company_id: str = impact["company_id"]
            direction: int = impact["direction"]
            strength: float = impact["strength"]
            all_paths: list[list[str]] = impact["paths"]

            # Primary path = first in the (already sorted, canonical) list
            primary_path: list[str] = all_paths[0] if all_paths else []

            # Flat union of all edge_ids across all paths (for contributing_edge_ids)
            seen_edges: set[str] = set()
            contributing_edge_ids: list[str] = []
            for path in all_paths:
                for eid in path:
                    if eid and eid not in seen_edges:
                        seen_edges.add(eid)
                        contributing_edge_ids.append(eid)

            # Resolve evidence chunk ids from contributing edges
            evidence_chunk_ids = _resolve_evidence_chunk_ids(
                contributing_edge_ids, edge_index
            )

            # Confidence: mean propagation_weight along the primary path
            confidence: float = _mean_propagation_weight(primary_path, edge_index)

            rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "as_of_date": as_of_date,
                    "trigger_id": trigger_id,
                    "trigger_kind": trigger_kind,
                    "company_id": company_id,
                    "direction": direction,
                    "strength": strength,
                    "path": primary_path,
                    "contributing_edge_ids": contributing_edge_ids,
                    "evidence_chunk_ids": evidence_chunk_ids,
                    "confidence": confidence,
                    "method": _METHOD,
                }
            )

    # Sort deterministically by (trigger_id, company_id) for reproducibility
    rows.sort(key=lambda r: (r["trigger_id"], r["company_id"]))

    out_path = runs.discovery_point_dir(run_id, as_of, for_write=True) / "projected_impacts.parquet"
    _write_projected_impacts_table(rows, out_path)

    return len(rows)


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------


def _write_projected_impacts_table(rows: list[dict], out_path: Path) -> None:
    """Write projected_impacts.parquet with correct schema.

    Empty rows produce a schema-valid empty table so callers can always read
    the artifact regardless of whether any triggers reached company nodes.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        schema = _parquet_schema()
        empty: dict = {f.name: pa.array([], type=f.type) for f in schema}
        pq.write_table(pa.table(empty, schema=schema), out_path)
        return

    # Build typed arrays column by column
    arrays: dict[str, pa.Array] = {}
    for col in PROJECTED_IMPACTS_COLUMNS:
        values = [row.get(col) for row in rows]
        if col == "direction":
            arrays[col] = pa.array(values, type=pa.int32())
        elif col in {"strength", "confidence"}:
            arrays[col] = pa.array(values, type=pa.float64())
        elif col in {"path", "contributing_edge_ids", "evidence_chunk_ids"}:
            arrays[col] = pa.array(values, type=pa.list_(pa.string()))
        else:
            # string columns (schema_version, run_id, as_of_date, trigger_id,
            # trigger_kind, company_id, method)
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )

    table = pa.table(arrays)
    pq.write_table(table, out_path)


def _parquet_schema() -> pa.Schema:
    """Return the canonical PyArrow schema for projected_impacts.parquet."""
    return pa.schema([
        ("schema_version", pa.string()),
        ("run_id", pa.string()),
        ("as_of_date", pa.string()),
        ("trigger_id", pa.string()),
        ("trigger_kind", pa.string()),
        ("company_id", pa.string()),
        ("direction", pa.int32()),
        ("strength", pa.float64()),
        ("path", pa.list_(pa.string())),
        ("contributing_edge_ids", pa.list_(pa.string())),
        ("evidence_chunk_ids", pa.list_(pa.string())),
        ("confidence", pa.float64()),
        ("method", pa.string()),
    ])
