"""Concept canonicalization: merge synonym/near-duplicate concept & event nodes
into one canonical node so themes stop being "synonym piles"
(e.g. air pollution / air pollution violations / Clean Air Act Violations /
Air Quality Monitoring -> one concept).

An LLM (the `concept_canonicalization` agent) groups ONLY true synonyms; distinct
concepts stay separate. Deterministic no-op fallback if no LLM is configured.
Runs on extraction output (entities/edges) before graph build.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

from . import registry, runs

_MERGEABLE_TYPES = {"EconomicConcept", "Event"}

_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_concept_groups",
        "description": "Group ONLY names that denote the SAME underlying concept (synonyms/near-duplicates).",
        "parameters": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "canonical_name": {"type": "string"},
                            "members": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["canonical_name", "members"],
                    },
                }
            },
            "required": ["groups"],
        },
    },
}


def _default_client_model():
    from openai import OpenAI  # noqa: PLC0415
    return OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"]), os.environ["LLM_MODEL_NAME"]


def group_synonyms(names: list[str], client, model: str) -> list[dict]:
    """Ask the LLM to group synonym concept names. Returns [{canonical_name, members}]."""
    system = registry.get_system_prompt("concept_canonicalization") or (
        "You are a financial ontology editor. Group ONLY the names below that denote the SAME "
        "underlying economic concept or event (synonyms / near-duplicates). Do NOT merge distinct "
        "concepts. Give each group a clear canonical name. Names not in any group stay separate. "
        "Call emit_concept_groups."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "Names:\n" + "\n".join(f"- {n}" for n in names)}]
    for _ in range(3):
        resp = client.chat.completions.create(model=model, messages=messages, tools=[_TOOL], temperature=0)
        tcs = getattr(resp.choices[0].message, "tool_calls", None) or []
        if tcs:
            try:
                return json.loads(tcs[0].function.arguments).get("groups", [])
            except Exception:
                pass
        messages.append({"role": "user", "content": "Call emit_concept_groups with valid JSON."})
    return []


def canonicalize_concepts(run_id: str, client=None, model: Optional[str] = None) -> dict:
    """Merge synonym concept/event nodes in a run's entities + remap edges."""
    rd = runs.get_run_dir(run_id)
    ent_tbl = pq.read_table(rd / "discovery" / "entities.parquet")
    ents = ent_tbl.to_pylist()
    name_to_id = {(e.get("canonical_name") or e.get("name")): e["entity_id"]
                  for e in ents if e.get("entity_type") in _MERGEABLE_TYPES}
    names = sorted(name_to_id)
    if len(names) < 3:
        return {"groups": 0, "merged": 0}

    if client is None:
        try:
            client, model = _default_client_model()
        except KeyError:
            return {"groups": 0, "merged": 0}   # no LLM -> deterministic no-op

    groups = group_synonyms(names, client, model)

    # Build merge map: member entity_id -> representative entity_id; rep gets canonical name.
    merge: dict[str, str] = {}
    rep_name: dict[str, str] = {}
    merged = 0
    for g in groups:
        members = [m for m in g.get("members", []) if m in name_to_id]
        if len(members) < 2:
            continue
        canon = g.get("canonical_name") or members[0]
        rep_id = name_to_id.get(canon, name_to_id[members[0]])
        rep_name[rep_id] = canon
        for m in members:
            mid = name_to_id[m]
            if mid != rep_id:
                merge[mid] = rep_id
                merged += 1
    if not merge:
        return {"groups": 0, "merged": 0}

    # Rewrite entities: drop merged-away ids; set canonical name on representatives.
    new_ents = []
    for e in ents:
        if e["entity_id"] in merge:
            continue
        if e["entity_id"] in rep_name:
            e = {**e, "canonical_name": rep_name[e["entity_id"]]}
        new_ents.append(e)
    pq.write_table(pa.Table.from_pylist(new_ents, schema=ent_tbl.schema), rd / "discovery" / "entities.parquet")

    # Remap edges: replace merged endpoints, drop self-loops, dedup.
    edge_tbl = pq.read_table(rd / "discovery" / "edges.parquet")
    seen, new_edges = set(), []
    for ed in edge_tbl.to_pylist():
        s = merge.get(ed["source_entity_id"], ed["source_entity_id"])
        t = merge.get(ed["target_entity_id"], ed["target_entity_id"])
        if s == t:
            continue
        key = (s, t, ed.get("edge_type"))
        if key in seen:
            continue
        seen.add(key)
        new_edges.append({**ed, "source_entity_id": s, "target_entity_id": t})
    pq.write_table(pa.Table.from_pylist(new_edges, schema=edge_tbl.schema), rd / "discovery" / "edges.parquet")

    return {"groups": sum(1 for g in groups if len([m for m in g.get("members", []) if m in name_to_id]) >= 2),
            "merged": merged, "entities_after": len(new_ents)}
