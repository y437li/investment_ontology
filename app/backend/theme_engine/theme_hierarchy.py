"""Theme hierarchy: group fine-grained communities (sub-themes) into a small set
of MAIN themes that a PM sees first. Main themes EMERGE from the sub-themes (the
LLM groups them) — they are not predefined. Drilling into a main theme reveals
its sub-themes. Output: discovery/theme_hierarchy.json.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import config, registry, runs


def _load_communities(run_id: str, as_of: str | None = None) -> list[dict]:
    p = runs.discovery_point_dir(run_id, as_of) / "communities.json"
    doc = json.loads(p.read_text())
    return doc.get("communities", doc)


def _default_client_model():
    from openai import OpenAI  # noqa: PLC0415
    return OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"]), config.model_for("theme_naming")


_TOOL = {
    "type": "function",
    "function": {
        "name": "group_main_themes",
        "description": "Group the sub-themes into a small set of higher-level MAIN themes.",
        "parameters": {
            "type": "object",
            "properties": {
                "main_themes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "short main-theme name"},
                            "summary": {"type": "string", "description": "one sentence"},
                            "community_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "community_ids"],
                    },
                }
            },
            "required": ["main_themes"],
        },
    },
}


def build_hierarchy(run_id: str, client=None, model: Optional[str] = None,
                    max_main_themes: int = 7, as_of: str | None = None) -> dict:
    """LLM-group communities into <= max_main_themes main themes; write artifact."""
    communities = _load_communities(run_id, as_of)
    # Filter out non-substantive (0-metric / tiny / evidence-less) communities so the
    # LLM groups only real narratives; fall back to all if the filter would empty it.
    from . import theme_levels  # noqa: PLC0415 (avoid import cycle at module load)
    keep = theme_levels.substantive_ids(run_id, as_of)
    if keep:
        communities = [c for c in communities if c["community_id"] in keep] or communities
    if client is None:
        client, model = _default_client_model()

    catalog = "\n".join(
        f"- {c['community_id']}: companies={(c.get('top_companies') or [])[:3]} "
        f"entities={(c.get('top_entities') or [])[:5]}"
        for c in communities
    )
    system = registry.get_system_prompt("theme_grouping", max_main_themes=max_main_themes) or (
        f"You organize discovered sub-themes into at most {max_main_themes} higher-level MAIN themes. "
        "A main theme is a broad economic narrative (e.g. 'Oil sands operations & ESG risk'); sub-themes "
        "are its facets. Every community_id must be assigned to exactly one main theme. Names must come "
        "from the sub-themes' content — do not invent themes not supported by them. Call group_main_themes."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"Sub-themes:\n{catalog}"}]
    args: dict = {}
    import json as _json  # noqa: PLC0415
    for _ in range(3):
        resp = client.chat.completions.create(model=model, messages=messages, tools=[_TOOL], temperature=0)
        tcs = getattr(resp.choices[0].message, "tool_calls", None) or []
        if tcs:
            try:
                args = _json.loads(tcs[0].function.arguments)
                break
            except Exception as exc:
                import logging  # noqa: PLC0415
                logging.getLogger(__name__).warning("group_main_themes tool-call parse failed: %s", exc)
                args = {}
        messages.append({"role": "user", "content": "Call group_main_themes with valid JSON."})

    valid_ids = {c["community_id"] for c in communities}
    sizes = {c["community_id"]: c.get("size", 0) for c in communities}
    main_themes = []
    assigned: set[str] = set()
    for mt in args.get("main_themes", []):
        ids = [cid for cid in mt.get("community_ids", []) if cid in valid_ids and cid not in assigned]
        if not ids:
            continue
        assigned.update(ids)
        main_themes.append({
            "name": mt.get("name", "Untitled theme"),
            "summary": mt.get("summary", ""),
            "sub_theme_ids": sorted(ids, key=lambda i: -sizes.get(i, 0)),
            "size": sum(sizes.get(i, 0) for i in ids),
        })
    # Any unassigned communities -> an "Other" bucket so nothing is lost.
    leftover = sorted(valid_ids - assigned, key=lambda i: -sizes.get(i, 0))
    if leftover:
        main_themes.append({"name": "Other narratives", "summary": "",
                            "sub_theme_ids": leftover, "size": sum(sizes.get(i, 0) for i in leftover)})
    main_themes.sort(key=lambda m: -m["size"])

    out = {"run_id": run_id, "main_themes": main_themes, "sub_theme_count": len(valid_ids)}
    (runs.discovery_point_dir(run_id, as_of, for_write=True) / "theme_hierarchy.json").write_text(json.dumps(out, indent=2))
    return out


def load_hierarchy(run_id: str, as_of: str | None = None) -> Optional[dict]:
    """Return the cached theme hierarchy, or None if not built yet."""
    p = runs.discovery_point_dir(run_id, as_of) / "theme_hierarchy.json"
    return json.loads(p.read_text()) if p.exists() else None
