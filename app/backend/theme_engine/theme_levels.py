"""Factor-level tagging + substance filter for themes.

For each community, classify its entities into factor-hierarchy levels
(macro / industry / company / idiosyncratic / contextual) using the ontology, so
themes can be FILTERED by level (spec §11 slicing dimensions). Also flags whether
a theme is SUBSTANTIVE (real narrative) vs a graph fragment with no structural
support — 0-metric / tiny / evidence-less communities are kept in the raw
artifacts for audit but marked non-substantive so the PM view can hide them.
Deterministic (no LLM).
"""

from __future__ import annotations

from . import registry, run_cache, runs

# A theme must be at least this big AND have non-zero strength to be "substantive".
_MIN_SIZE = 3
# Levels that count as the meaningful factor dimensions (contextual/evidence excluded).
_FACTOR_LEVELS = ("macro", "industry", "company", "idiosyncratic")
# Tie-break priority when picking a single dominant level.
_PRIORITY = {"macro": 0, "industry": 1, "company": 2, "idiosyncratic": 3}


def _strength_by_community(dd) -> dict:
    p = dd / "theme_metrics.parquet"
    if not p.exists():
        return {}
    out: dict[str, float] = {}
    for row in run_cache.load_parquet_rows(p):
        cid = row.get("community_id")
        if cid is None:
            continue
        out[cid] = max(out.get(cid, 0.0), float(row.get("strength") or 0.0))
    return out


def _dominant(level_counts: dict) -> str:
    factor = {lv: level_counts.get(lv, 0) for lv in _FACTOR_LEVELS}
    if not any(factor.values()):
        return "contextual"
    top = max(factor.values())
    # most frequent factor level; ties broken by priority macro>industry>company>idio
    return sorted((lv for lv, n in factor.items() if n == top), key=lambda lv: _PRIORITY[lv])[0]


def compute_levels(run_id: str, as_of: str | None = None) -> dict:
    """Per-community level composition + dominant level + substantive flag."""
    dd = runs.discovery_point_dir(run_id, as_of)
    ent_level = {e["entity_id"]: registry.entity_level(e.get("entity_type"))
                 for e in run_cache.load_parquet_rows(dd / "entities.parquet")}
    strength = _strength_by_community(dd)

    comm_doc = run_cache.load_json(dd / "communities.json")
    communities = comm_doc.get("communities", comm_doc)

    by_id: dict[str, dict] = {}
    themes: list[dict] = []
    for c in communities:
        cid = c["community_id"]
        counts: dict[str, int] = {}
        for nid in c.get("node_ids", []):
            lv = ent_level.get(nid)
            if lv:
                counts[lv] = counts.get(lv, 0) + 1
        size = c.get("size", len(c.get("node_ids", [])))
        s = strength.get(cid, 0.0)
        row = {
            "community_id": cid,
            "level_counts": counts,
            "dominant_level": _dominant(counts),
            "size": size,
            "strength": round(s, 4),
            "substantive": bool(size >= _MIN_SIZE and s > 0.0),
        }
        by_id[cid] = row
        themes.append(row)

    main_themes = []
    hier_path = dd / "theme_hierarchy.json"
    if hier_path.exists():
        hier = run_cache.load_json(hier_path)
        for mt in hier.get("main_themes", []):
            subs = [by_id[s] for s in mt.get("sub_theme_ids", []) if s in by_id]
            if not subs:
                continue
            agg: dict[str, int] = {}
            for sub in subs:
                for lv, n in sub["level_counts"].items():
                    agg[lv] = agg.get(lv, 0) + n
            main_themes.append({
                "name": mt.get("name"),
                "level_counts": agg,
                "dominant_level": _dominant(agg),
                "substantive_sub_count": sum(1 for sub in subs if sub["substantive"]),
            })

    return {"run_id": run_id, "factor_levels": list(_FACTOR_LEVELS),
            "themes": themes, "main_themes": main_themes}


def substantive_ids(run_id: str, as_of: str | None = None) -> set:
    """community_ids that are real narratives (used to filter PM view + hierarchy input)."""
    return {t["community_id"] for t in compute_levels(run_id, as_of)["themes"] if t["substantive"]}
