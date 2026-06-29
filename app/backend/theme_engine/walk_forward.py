"""Monthly walk-forward (spec §12 theme lifecycle).

Reuses a frozen run's extraction — no LLM re-run. For each month-end it filters
edges to those AVAILABLE by then (point-in-time, using evidence-chunk availability),
re-detects communities, and matches them across months by entity overlap to produce
each current theme's trajectory: when it emerged, its size over time, and momentum.
Deterministic.
"""

from __future__ import annotations

import ast
import json
from datetime import date

import networkx as nx

from . import graph_build, run_cache, runs

try:
    from networkx.algorithms.community import louvain_communities
except Exception:  # pragma: no cover
    louvain_communities = None

_MIN_SIZE = 3


def _to_date(s: str) -> date | None:
    if not s:
        return None
    y, m, d = s[:10].split("-")
    return date(int(y), int(m), int(d))


def _parse_ids(v) -> list[str]:
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        return ast.literal_eval(v) if isinstance(v, str) else list(v)
    except Exception:
        return []


def _month_ends(start: date, end: date) -> list[str]:
    """Month-end dates from the month of `start` through `end` (inclusive)."""
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        nm_y, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = date(nm_y, nm, 1).toordinal() - 1
        me = date.fromordinal(last)
        out.append((me if me <= end else end).isoformat())
        y, m = nm_y, nm
    return out


def _monthly_snapshots(run_id: str, as_of: str | None = None) -> tuple[list[str], list[list[set]]]:
    rd = runs.get_run_dir(run_id)
    dd = runs.discovery_point_dir(run_id, as_of)
    chunk_dates = {c["chunk_id"]: c.get("available_at")
                   for c in run_cache.load_parquet_rows(dd / "chunks.parquet")}
    manifest = run_cache.load_json(rd / "run_manifest.json")
    pit_date = as_of if as_of is not None else manifest["as_of_date"]
    as_of = _to_date(pit_date)

    timed_edges: list[tuple[date, str, str]] = []
    for e in run_cache.load_parquet_rows(dd / "edges.parquet"):
        if e["edge_type"] not in graph_build.STRUCTURAL_EDGE_TYPES:
            continue
        if (e.get("extraction_method") or "document_stated") not in graph_build.COMMUNITY_INPUT_METHODS:
            continue
        ev = [chunk_dates.get(c) for c in _parse_ids(e.get("evidence_chunk_ids"))]
        ev = [d for d in ev if d]
        av = _to_date(min(ev)) if ev else _to_date(e.get("first_seen_at"))
        if av:
            timed_edges.append((av, e["source_entity_id"], e["target_entity_id"]))
    if not timed_edges:
        return [], []

    months = _month_ends(min(av for av, _, _ in timed_edges), as_of)
    snapshots: list[list[set]] = []
    for m in months:
        md = _to_date(m)
        g = nx.Graph()
        for av, s, t in timed_edges:
            if av <= md and s != t:
                g.add_edge(s, t)
        comms = ([set(c) for c in louvain_communities(g, seed=42)]
                 if (louvain_communities and g.number_of_edges()) else [])
        snapshots.append(comms)
    return months, snapshots


def theme_trajectories(run_id: str, min_size: int = _MIN_SIZE,
                       as_of: str | None = None) -> dict:
    """Each current (final-month) theme's size trajectory + emergence month + momentum."""
    months, snapshots = _monthly_snapshots(run_id, as_of=as_of)
    if not months:
        return {"months": [], "themes": []}

    # name the final communities from the frozen communities.json (by entity overlap)
    dd = runs.discovery_point_dir(run_id, as_of)
    ent_name = {e["entity_id"]: (e.get("canonical_name") or e.get("name"))
                for e in run_cache.load_parquet_rows(dd / "entities.parquet")}
    frozen = run_cache.load_json(dd / "communities.json")
    frozen = frozen.get("communities", frozen)

    final = snapshots[-1]
    themes = []
    for fc in final:
        if len(fc) < min_size:
            continue
        trajectory = []
        for comms in snapshots:
            best = 0
            for c in comms:
                ov = len(fc & c)
                if ov > best:
                    best = ov
            matched = max((c for c in comms if len(fc & c) == best and best > 0), key=len, default=set())
            trajectory.append({"size": len(matched), "overlap": best})
        for t, mo in zip(trajectory, months):
            t["month"] = mo
        emerged = next((t["month"] for t in trajectory if t["size"] >= min_size), months[-1])
        sizes = [t["size"] for t in trajectory]
        momentum = sizes[-1] - (sizes[-2] if len(sizes) > 1 else 0)
        fname = max(frozen, key=lambda c: len(fc & set(c.get("node_ids", []))), default={})
        themes.append({
            "community_id": fname.get("community_id"),
            "theme_name": fname.get("theme_name") or ", ".join(
                sorted((ent_name.get(n, "") for n in fc), key=len, reverse=True)[:3]),
            "size": len(fc),
            "emerged_month": emerged,
            "momentum": momentum,
            "trajectory": trajectory,
        })
    themes.sort(key=lambda x: (-x["momentum"], -x["size"]))
    doc = {"months": months, "themes": themes}
    (dd / "theme_trajectories.json").write_text(json.dumps(doc, indent=2))
    return doc
