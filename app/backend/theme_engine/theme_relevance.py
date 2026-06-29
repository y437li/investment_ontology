"""Temporal relevance (spec §12 states, time-aware §1).

Not every theme is relevant at the as_of date. This scores each community by how
RECENT its supporting evidence is (using each evidence chunk's point-in-time
`available_at` vs as_of) and assigns a lifecycle state. The landing uses this to
surface what is LIVE at as_of instead of a flat dump. Deterministic (no LLM).
"""

from __future__ import annotations

import ast
import json
from datetime import date
from typing import Optional

from . import run_cache, runs

_DEFAULT_WINDOW_DAYS = 90


def _to_date(s: str) -> date:
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


def _score(dates: list[str], as_of: date, window_days: int) -> dict:
    """Recency score + lifecycle state from a theme's evidence dates."""
    # PIT: drop future-dated evidence (not knowable at as_of) so it cannot be scored
    # as maximally recent (audit medium: future evidence clamped to max-recency).
    dates = [d for d in dates if _to_date(d) <= as_of]
    if not dates:
        return {"relevance_score": 0.0, "state": "dormant", "recent_share": 0.0,
                "last_evidence_at": None, "evidence_count": 0}
    days = [(as_of - _to_date(d)).days for d in dates]
    recent_share = sum(1 for x in days if x <= window_days) / len(days)
    recency_score = sum(max(0.0, 1.0 - x / 365.0) for x in days) / len(days)
    if recent_share >= 0.5:
        state = "emerging"
    elif recent_share >= 0.2:
        state = "mature"
    elif recent_share > 0:
        state = "declining"
    else:
        state = "dormant"
    return {"relevance_score": round(recency_score, 4), "state": state,
            "recent_share": round(recent_share, 3), "last_evidence_at": max(dates),
            "evidence_count": len(dates)}


def compute_relevance(run_id: str, window_days: int = _DEFAULT_WINDOW_DAYS,
                      as_of: str | None = None) -> dict:
    """Per-community relevance at as_of (+ main-theme aggregation if a hierarchy exists)."""
    rd = runs.get_run_dir(run_id)
    dd = runs.discovery_point_dir(run_id, as_of)
    manifest = run_cache.load_json(rd / "run_manifest.json")
    # PIT cutoff: the selected point when given, else the manifest's as_of_date.
    pit_date = as_of if as_of is not None else manifest["as_of_date"]
    as_of_dt = _to_date(pit_date)

    chunk_dates = {c["chunk_id"]: c.get("available_at")
                   for c in run_cache.load_parquet_rows(dd / "chunks.parquet")}
    edge_ev = {ed["edge_id"]: _parse_ids(ed.get("evidence_chunk_ids"))
               for ed in run_cache.load_parquet_rows(dd / "edges.parquet")}

    comm_doc = run_cache.load_json(dd / "communities.json")
    communities = comm_doc.get("communities", comm_doc)

    by_id: dict[str, dict] = {}
    themes: list[dict] = []
    for c in communities:
        dates = [d for eid in c.get("edge_ids", []) for cid in edge_ev.get(eid, [])
                 if (d := chunk_dates.get(cid))]
        row = {"community_id": c["community_id"], **_score(dates, as_of_dt, window_days)}
        by_id[c["community_id"]] = row
        themes.append(row)
    themes.sort(key=lambda x: x["relevance_score"], reverse=True)

    # Aggregate to main themes when a hierarchy exists (max-relevance, latest evidence).
    main_themes = []
    hier_path = dd / "theme_hierarchy.json"
    if hier_path.exists():
        hier = run_cache.load_json(hier_path)
        for mt in hier.get("main_themes", []):
            subs = [by_id[s] for s in mt.get("sub_theme_ids", []) if s in by_id]
            if not subs:
                continue
            best = max(subs, key=lambda r: r["relevance_score"])
            main_themes.append({
                "name": mt.get("name"),
                "relevance_score": best["relevance_score"],
                "state": best["state"],
                "last_evidence_at": max((s["last_evidence_at"] for s in subs if s["last_evidence_at"]), default=None),
                "sub_theme_count": len(subs),
            })
        main_themes.sort(key=lambda x: x["relevance_score"], reverse=True)

    doc = {"run_id": run_id, "as_of_date": pit_date, "window_days": window_days,
           "themes": themes, "main_themes": main_themes}
    (dd / "theme_relevance.json").write_text(json.dumps(doc, indent=2))
    return doc
