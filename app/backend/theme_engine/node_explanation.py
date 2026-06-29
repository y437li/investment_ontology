"""Node Explanation Framework (spec section 13).

Every node answers: (1) what is it, (2) why is it in the graph, (3) why does it
matter. The deterministic profile is ALWAYS available — built from the ontology
(type + definition + factor level) and the node's graph context (its relationships,
evidence count, neighbours). An optional LLM enrichment (the `node_explanation`
agent) adds a prose "why it matters", cached. Evidence-backed only.
"""

from __future__ import annotations

import ast
import json
from typing import Optional

import pyarrow.parquet as pq

from . import config, registry, runs


def _load(run_id: str, name: str, as_of: str | None = None):
    p = runs.discovery_point_dir(run_id, as_of) / name
    if name.endswith(".json"):
        return json.loads(p.read_text())
    return pq.read_table(p).to_pylist()


def _parse_ids(v) -> list[str]:
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        return ast.literal_eval(v) if isinstance(v, str) else list(v)
    except Exception:
        return []


def _name(ent: dict) -> str:
    return ent.get("canonical_name") or ent.get("name") or ent.get("entity_id", "")


def node_profile(run_id: str, entity_id: str, as_of: str | None = None) -> dict:
    """Deterministic node profile (no LLM): what it is + why it is in the graph."""
    entities = {e["entity_id"]: e for e in _load(run_id, "entities.parquet", as_of)}
    ent = entities.get(entity_id)
    if ent is None:
        raise ValueError(f"entity not found: {entity_id}")

    etype = ent.get("entity_type")
    expl = {x["edge_id"]: x.get("explanation", "") for x in _load(run_id, "edge_explanations.parquet", as_of)}

    relationships: list[dict] = []
    evidence_chunks: set[str] = set()
    neighbours: set[str] = set()
    first_seen: Optional[str] = None
    for ed in _load(run_id, "edges.parquet", as_of):
        s, t = ed["source_entity_id"], ed["target_entity_id"]
        if entity_id not in (s, t):
            continue
        other = t if s == entity_id else s
        neighbours.add(other)
        for cid in _parse_ids(ed.get("evidence_chunk_ids")):
            evidence_chunks.add(cid)
        fs = ed.get("first_seen_at")
        if fs and (first_seen is None or fs < first_seen):
            first_seen = fs
        relationships.append({
            "direction": "out" if s == entity_id else "in",
            "edge_type": ed["edge_type"],
            "other": _name(entities.get(other, {"entity_id": other})),
            "explanation": expl.get(ed["edge_id"], ""),
        })

    onto = (registry.load_ontology().get("entity_types") or {}).get(etype, {})
    return {
        "entity_id": entity_id,
        "name": _name(ent),
        "entity_type": etype,
        "level": onto.get("level"),                 # factor-hierarchy level (why it matters)
        "definition": onto.get("definition", ""),   # what is it (from ontology)
        "first_seen_at": first_seen,
        "evidence_count": len(evidence_chunks),      # section 13: evidence count
        "degree": len(neighbours),                   # section 13: importance
        "why_present": relationships,                # section 13: why it exists (its edges + evidence)
        "related_entities": sorted(_name(entities.get(n, {"entity_id": n})) for n in neighbours)[:12],
    }


def explain_node(run_id: str, entity_id: str, refresh: bool = False,
                 client=None, model: Optional[str] = None, as_of: str | None = None) -> dict:
    """Node profile + an optional cached LLM prose explanation of why it matters."""
    cache = runs.discovery_point_dir(run_id, as_of) / "node_explanations" / f"{entity_id}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())

    profile = node_profile(run_id, entity_id, as_of)
    if client is None:
        import os  # noqa: PLC0415
        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"])
        model = config.model_for("explanation")

    system = registry.get_system_prompt("node_explanation") or (
        "Explain this node using ONLY the provided relationships and evidence. Answer what it is, "
        "why it is in the graph, and why it matters economically. Be concise; no outside knowledge."
    )
    rels = "\n".join(f"- {r['direction']} {r['edge_type']} {r['other']}: {r['explanation']}"
                     for r in profile["why_present"]) or "(none)"
    user = (f"Node: {profile['name']} ({profile['entity_type']}, level={profile['level']})\n"
            f"Definition: {profile['definition']}\nRelationships:\n{rels}")
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    import re  # noqa: PLC0415
    content = resp.choices[0].message.content or ""
    profile["explanation"] = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(profile, indent=2))
    return profile
