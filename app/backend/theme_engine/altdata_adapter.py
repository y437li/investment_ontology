"""Alt/structured-data adapter: integrate ANY numeric/structured series (power
demand, port throughput, datacenter capacity, rail traffic, commodity prices,
macro rates) into a run's graph as typed NODES + structural edges to companies
in sensitive sectors.

Generalizes macro_adapter.py. Each source (a row in configs/altdata.yml) becomes
one ontology NODE (MacroIndicator/Commodity/EconomicConcept/...) carrying a
point-in-time value + trend (+ optional regime) at the run as_of (release lag
respected -> zero leakage). Config-driven factor sensitivities emit structural
edges (benefits/hurts/exposed_to/sensitive_to/causes) so alt data joins Louvain
community discovery (driver -> sector -> company). Edges are metadata_inferred
(honest: structural, not document-stated); the series VALUE + vintage is the
evidence. Deterministic; NO LLM. macro_adapter.py is untouched.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from . import runs
from .macro_adapter import _config_dir, _to_date, _norm, _append  # reuse, single source of truth
from .graph_build import STRUCTURAL_EDGE_TYPES

_STRUCTURAL = set(STRUCTURAL_EDGE_TYPES)  # ontology-derived: {exposed_to, sensitive_to, causes, benefits, hurts}


# ---------- config ----------
def _load_altdata_config() -> dict:
    p = _config_dir() / "altdata.yml"
    if not p.exists():
        return {}
    import yaml  # noqa: PLC0415
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _keep_entity_types() -> set[str]:
    """Ontology entity_types with keep: true (the allowlist for node_type)."""
    p = _config_dir() / "ontology.yml"
    if not p.exists():
        return {"MacroIndicator", "Commodity", "EconomicConcept"}  # safe default
    import yaml  # noqa: PLC0415
    onto = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {k for k, v in (onto.get("entity_types") or {}).items()
            if (v or {}).get("keep")}


# ---------- readers (the ONLY place code changes to add a capability) ----------
def _read_fred_csv(spec) -> list[tuple[date, float]]:
    path = Path(spec["csv"])
    if not path.exists():
        return []
    out, dc, vc = [], spec.get("date_col"), spec.get("value_col")
    text = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    for row in csv.DictReader(text):
        d, v = _to_date(row.get(dc, "")), row.get(vc, "")
        try:
            if d and v not in ("", "."):
                out.append((d, float(v)))
        except ValueError:
            continue
    out.sort()
    return out


def _read_boc_csv(spec) -> list[tuple[date, float]]:
    path = Path(spec["csv"])
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        cells = [c.strip().strip('"') for c in line.split(",")]
        if len(cells) >= 2 and (d := _to_date(cells[0])):
            try:
                out.append((d, float(cells[1])))
            except ValueError:
                continue
    out.sort()
    return out


def _read_wide_table(spec) -> list[tuple[date, float]]:
    path = Path(spec["csv"])
    if not path.exists():
        return []
    out, dc, sc = [], spec.get("date_col"), spec.get("series_col")
    text = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    for row in csv.DictReader(text):
        d, v = _to_date(row.get(dc, "")), row.get(sc, "")
        try:
            if d and v not in ("", "."):
                out.append((d, float(v)))
        except ValueError:
            continue
    out.sort()
    return out


_READERS = {"fred_csv": _read_fred_csv, "boc_csv": _read_boc_csv, "wide_table": _read_wide_table}


def _read_series(spec) -> list[tuple[date, float]]:
    reader = spec.get("reader")
    if not reader:  # macro-compat auto-detect: date_col+value_col -> fred, else boc
        reader = "fred_csv" if (spec.get("date_col") and spec.get("value_col")) else "boc_csv"
    return _READERS[reader](spec)


# ---------- PIT signal (superset of macro pit_snapshot) ----------
def _regime(value, avail, obs_date, spec) -> str | None:
    rc = spec.get("regime")
    if not rc:
        return None
    mode = rc.get("mode")
    if mode == "threshold":
        ths, labels = rc.get("thresholds", []), rc.get("labels", [])
        i = 0
        while i < len(ths) and value > ths[i]:
            i += 1
        return labels[i] if i < len(labels) else (labels[-1] if labels else None)
    if mode == "zscore":
        wd = rc.get("window_days", 365)
        win = [v for d, v in avail if d > obs_date - timedelta(days=wd)]
        if len(win) < 2:
            return "normal"
        mean = sum(win) / len(win)
        var = sum((x - mean) ** 2 for x in win) / len(win)
        std = var ** 0.5
        if std == 0:
            return "normal"
        z = (value - mean) / std
        return "elevated" if z > 1 else ("depressed" if z < -1 else "normal")
    return None


def pit_signal(spec: dict, as_of: date, default_lag: int) -> dict | None:
    """PIT latest value at as_of (release lag respected) + macro-semantics trend
    + optional regime. Returns None if no PIT-eligible obs (no leakage, no node)."""
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
    pct = (delta / prev * 100.0) if prev else 0.0
    return {"value": value, "prev": prev, "trend": trend,
            "regime": _regime(value, avail, obs_date, spec),
            "pct_change": pct, "obs_date": obs_date.isoformat()}


# ---------- id derivation (namespaced; no collision with macro_adapter) ----------
def _eid(node_type: str, label: str) -> str:
    return "ent_alt_" + hashlib.sha256(f"{node_type}|{label}".encode()).hexdigest()[:12]


def _edge_id(src, tgt, etype) -> str:
    return "edge_alt_" + hashlib.sha256(f"{src}{tgt}{etype}".encode()).hexdigest()[:12]


# ---------- integration ----------
def integrate_altdata(run_id: str, universe_path: str | None = None) -> dict:
    rd = runs.get_run_dir(run_id)
    as_of = _to_date(json.loads((rd / "run_manifest.json").read_text())["as_of_date"])
    cfg = _load_altdata_config()
    if not cfg.get("sources"):
        return {"altdata_nodes": 0, "altdata_edges": 0}

    import yaml  # noqa: PLC0415
    upath = Path(universe_path or os.environ.get("UNIVERSE_CONFIG", "configs/universe.tsx60.yml"))
    sector_companies: dict[str, set] = {}
    if upath.exists():
        for c in (yaml.safe_load(upath.read_text()) or {}).get("companies", []):
            sector_companies.setdefault(c.get("sector", ""), set()).add(_norm(c.get("name", "")))

    ent_tbl = pq.read_table(rd / "discovery" / "entities.parquet")
    ent_cols = ent_tbl.column_names
    ents = ent_tbl.to_pylist()
    company_id = {_norm(e.get("canonical_name") or e.get("name")): e["entity_id"]
                  for e in ents if e.get("entity_type") == "Company"}
    schema_version = ents[0].get("schema_version") if ents else 1
    existing_ids = {e["entity_id"] for e in ents}

    keep_types = _keep_entity_types()
    default_lag = cfg.get("release_lag_days", 35)
    new_ents, new_edges, new_expl = [], [], []

    for spec in cfg["sources"]:
        node_type = spec.get("node_type", "MacroIndicator")
        if node_type not in keep_types:        # honest skip, never crash the run
            continue
        for s in spec.get("sensitivities", []):
            if s["edge_type"] not in _STRUCTURAL:   # loud: bad config = dropped discovery edges
                raise ValueError(f"altdata source {spec.get('id')}: edge_type "
                                 f"{s['edge_type']!r} is not structural {sorted(_STRUCTURAL)}")
        snap = pit_signal(spec, as_of, default_lag)
        if not snap:                            # no PIT-knowable data -> no node, no leakage
            continue

        label, unit = spec["label"], spec.get("unit", "")
        conf = float(spec.get("confidence", 0.6))
        nid = _eid(node_type, label)
        if nid not in existing_ids:
            new_ents.append({"schema_version": schema_version, "entity_id": nid,
                             "entity_type": node_type, "name": label,
                             "canonical_name": label, "ticker": None})
            existing_ids.add(nid)

        val_txt = f"{snap['value']:g}{unit if unit not in ('index', '') else ''}"
        regime_txt = f", regime={snap['regime']}" if snap["regime"] else ""
        for sens in spec.get("sensitivities", []):
            if (wt := sens.get("when_trend")) and wt != snap["trend"]:
                continue
            if (wr := sens.get("when_regime")) and wr != snap["regime"]:
                continue
            targets = set(sens.get("companies") or [])
            tnames = {_norm(n) for n in targets} | sector_companies.get(sens.get("sector", ""), set())
            for cname in tnames:
                tid = company_id.get(cname)
                if not tid:
                    continue
                eid = _edge_id(nid, tid, sens["edge_type"])
                new_edges.append({
                    "schema_version": schema_version, "edge_id": eid,
                    "source_entity_id": nid, "target_entity_id": tid,
                    "edge_type": sens["edge_type"], "confidence": conf,
                    "evidence_chunk_ids": [], "first_seen_at": as_of.isoformat(),
                    "last_seen_at": as_of.isoformat(), "as_of_date": as_of.isoformat(),
                    "extraction_method": "metadata_inferred", "review_status": "auto"})
                tmpl = spec.get("explanation_template")
                if tmpl:
                    expl = tmpl.format(label=label, value=val_txt, unit=unit,
                                       trend=snap["trend"], regime=snap["regime"],
                                       obs_date=snap["obs_date"], rationale=sens["rationale"],
                                       as_of=as_of.isoformat(), id=spec["id"])
                else:
                    expl = (f"{label} = {val_txt} ({snap['trend']} vs ~3mo prior{regime_txt}, "
                            f"as of {snap['obs_date']}); {sens['rationale']}. "
                            f"Source: {spec.get('source_class', spec['id'])} "
                            f"(point-in-time, vintage {as_of.isoformat()}).")
                new_expl.append({
                    "schema_version": schema_version, "edge_id": eid, "explanation": expl,
                    "evidence_chunk_ids": [], "confidence": conf,
                    "generated_by": f"altdata_adapter:{spec['id']}", "created_at": as_of.isoformat()})

    _append(rd / "discovery" / "entities.parquet", ent_cols, new_ents)
    _append(rd / "discovery" / "edges.parquet", None, new_edges)
    _append(rd / "discovery" / "edge_explanations.parquet", None, new_expl)
    return {"altdata_nodes": len(new_ents), "altdata_edges": len(new_edges)}
