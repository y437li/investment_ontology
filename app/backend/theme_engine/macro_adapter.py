"""Macro-data adapter: integrate structured macro series (interest rates,
inflation, unemployment, ...) into a run's graph.

Each series becomes a MacroIndicator NODE carrying a point-in-time value + trend
at the run's as_of (the latest release available then — release lag respected).
Structural edges connect the macro node to companies in sensitive sectors
(config-driven factor sensitivities from configs/macro.yml), so macro clusters
into themes and propagates macro -> sector -> company. Edges are metadata_inferred
(structural, not document-stated) and labeled as such; the value is the evidence.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import graph_build, runs

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "configs"))


def _to_date(s: str) -> date | None:
    m = _DATE_RE.search(s or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


def _load_macro_config() -> dict:
    p = _config_dir() / "macro.yml"
    if not p.exists():
        return {}
    import yaml  # noqa: PLC0415
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _read_series(spec: dict) -> list[tuple[date, float]]:
    """Return [(date, value)] for a series, handling FRED and BoC-preamble CSVs."""
    path = Path(spec["csv"])
    if not path.exists():
        return []
    out: list[tuple[date, float]] = []
    date_col, value_col = spec.get("date_col"), spec.get("value_col")
    text = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    if date_col and value_col:  # well-formed FRED csv
        for row in csv.DictReader(text):
            d, v = _to_date(row.get(date_col, "")), row.get(value_col, "")
            try:
                if d and v not in ("", "."):
                    out.append((d, float(v)))
            except ValueError:
                continue
    else:  # BoC-style: skip preamble, take any "<date>,<number>" rows
        for line in text:
            cells = [c.strip().strip('"') for c in line.split(",")]
            if len(cells) >= 2 and (d := _to_date(cells[0])):
                try:
                    out.append((d, float(cells[1])))
                except ValueError:
                    continue
    out.sort()
    return out


def pit_snapshot(spec: dict, as_of: date, default_lag: int) -> dict | None:
    """Latest value available at as_of (release lag respected) + trend vs ~3mo prior."""
    series = _read_series(spec)
    if not series:
        return None
    lag = spec.get("lag_days", default_lag)
    cutoff = as_of - timedelta(days=lag)
    avail = [(d, v) for d, v in series if d <= cutoff]
    if not avail:
        return None
    obs_date, value = avail[-1]
    prior = [(d, v) for d, v in avail if d <= obs_date - timedelta(days=80)]
    prev = prior[-1][1] if prior else value
    delta = value - prev
    eps = abs(prev) * 0.01 if prev else 0.001
    trend = "rising" if delta > eps else ("falling" if delta < -eps else "flat")
    return {"value": value, "prev": prev, "trend": trend, "obs_date": obs_date.isoformat()}


def _eid(label: str) -> str:
    return "ent_macro_" + hashlib.sha256(label.encode()).hexdigest()[:12]


def integrate_macro(run_id: str, universe_path: str | None = None,
                    as_of_point: str | None = None) -> dict:
    """Append macro MacroIndicator nodes + macro->company structural edges to a run."""
    rd = runs.get_run_dir(run_id)
    dd = runs.discovery_point_dir(run_id, as_of_point, for_write=True)
    import json  # noqa: PLC0415
    pit_date = as_of_point if as_of_point is not None else json.loads((rd / "run_manifest.json").read_text())["as_of_date"]
    as_of = _to_date(pit_date)
    cfg = _load_macro_config()
    if not cfg.get("series"):
        return {"macro_nodes": 0, "macro_edges": 0}

    # universe: sector -> set of normalized company names
    import yaml  # noqa: PLC0415
    upath = Path(universe_path or os.environ.get("UNIVERSE_CONFIG", "configs/universe.tsx60.yml"))
    sector_companies: dict[str, set] = {}
    if upath.exists():
        for c in (yaml.safe_load(upath.read_text()) or {}).get("companies", []):
            sector_companies.setdefault(c.get("sector", ""), set()).add(_norm(c.get("name", "")))

    # run companies: normalized name -> entity_id
    ent_tbl = pq.read_table(dd / "entities.parquet")
    ent_cols = ent_tbl.column_names
    ents = ent_tbl.to_pylist()
    company_id = {_norm(e.get("canonical_name") or e.get("name")): e["entity_id"]
                  for e in ents if e.get("entity_type") == "Company"}
    schema_version = ents[0].get("schema_version") if ents else 1
    existing_ids = {e["entity_id"] for e in ents}

    new_ents, new_edges, new_expl = [], [], []
    default_lag = cfg.get("release_lag_days", 35)
    for spec in cfg["series"]:
        snap = pit_snapshot(spec, as_of, default_lag)
        if not snap:
            continue
        label, unit = spec["label"], spec.get("unit", "")
        mid = _eid(label)
        if mid not in existing_ids:
            new_ents.append({"schema_version": schema_version, "entity_id": mid,
                             "entity_type": "MacroIndicator", "name": label,
                             "canonical_name": label, "ticker": None})
            existing_ids.add(mid)
        val_txt = f"{snap['value']:g}{unit if unit != 'index' else ''}"
        for sens in spec.get("sensitivities", []):
            # Validate edge_type against the ontology-derived structural set (audit medium):
            # a bad config edge_type would silently drop the macro edge out of discovery.
            if sens["edge_type"] not in graph_build.STRUCTURAL_EDGE_TYPES:
                raise ValueError(
                    f"macro.yml series '{spec['id']}': edge_type '{sens['edge_type']}' is not a "
                    f"structural edge type {list(graph_build.STRUCTURAL_EDGE_TYPES)}")
            for cname in sector_companies.get(sens["sector"], set()):
                tid = company_id.get(cname)
                if not tid:
                    continue
                eid = "edge_macro_" + hashlib.sha256(f"{mid}{tid}{sens['edge_type']}".encode()).hexdigest()[:12]
                new_edges.append({
                    "schema_version": schema_version, "edge_id": eid,
                    "source_entity_id": mid, "target_entity_id": tid,
                    "edge_type": sens["edge_type"], "confidence": 0.6,
                    "evidence_chunk_ids": [], "first_seen_at": as_of.isoformat(),
                    "last_seen_at": as_of.isoformat(), "as_of_date": as_of.isoformat(),
                    "extraction_method": "metadata_inferred", "review_status": "auto",
                })
                new_expl.append({
                    "schema_version": schema_version, "edge_id": eid,
                    # io_contracts §11: metadata_inferred edges must carry source_record_id
                    # in the explanation context so audit remains reconstructable.
                    "explanation": (f"{label} = {val_txt} ({snap['trend']} vs ~3mo prior, "
                                    f"as of {snap['obs_date']}); {sens['rationale']}. "
                                    f"Source: macro series {spec['id']} "
                                    f"(point-in-time, vintage {as_of.isoformat()})."),
                    "evidence_chunk_ids": [], "confidence": 0.6,
                    "generated_by": "macro_adapter", "created_at": as_of.isoformat(),
                })

    _append(dd / "entities.parquet", ent_cols, new_ents)
    _append(dd / "edges.parquet", None, new_edges)
    _append(dd / "edge_explanations.parquet", None, new_expl)
    return {"macro_nodes": len(new_ents), "macro_edges": len(new_edges)}


def _append(path: Path, cols, rows: list[dict]) -> None:
    if not rows:
        return
    existing = pq.read_table(path)
    cols = cols or existing.column_names
    rows = [{c: r.get(c) for c in cols} for r in rows]
    combined = pa.concat_tables([existing, pa.Table.from_pylist(rows, schema=existing.schema)])
    pq.write_table(combined, path)
