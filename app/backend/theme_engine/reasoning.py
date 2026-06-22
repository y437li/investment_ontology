"""Connect-the-dots narrative reasoning (GraphRAG-style).

Given a discovered theme/community, gather its entities, relationships (each with
its extraction explanation + source evidence), and use an LLM to synthesize a
cited narrative that connects the dots across multiple hops. The LLM's chain of
thought (reasoning_chain) is captured for audit. Evidence-backed only — the
prompt forbids outside knowledge and investment advice.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Optional

import pyarrow.parquet as pq

from . import registry, runs

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _parse_ids(v) -> list[str]:
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        return ast.literal_eval(v) if isinstance(v, str) else list(v)
    except Exception:
        return []


def _load(run_id: str, name: str):
    p = runs.get_run_dir(run_id) / "discovery" / name
    if name.endswith(".json"):
        return json.loads(p.read_text())
    return pq.read_table(p).to_pylist()


def gather_dossier(run_id: str, community_id: str) -> dict:
    """Collect a community's relationships + evidence into a structured dossier."""
    comm = _load(run_id, "communities.json")
    communities = comm.get("communities", comm)
    c = next((x for x in communities if x.get("community_id") == community_id), None)
    if c is None:
        raise ValueError(f"community not found: {community_id}")

    ent = {e["entity_id"]: (e.get("canonical_name") or e.get("name")) for e in _load(run_id, "entities.parquet")}
    expl = {e["edge_id"]: e.get("explanation", "") for e in _load(run_id, "edge_explanations.parquet")}
    chunks = {ch["chunk_id"]: ch.get("text", "") for ch in _load(run_id, "chunks.parquet")}
    edge_ids = set(c.get("edge_ids", []))

    relationships: list[dict] = []
    for ed in _load(run_id, "edges.parquet"):
        if ed["edge_id"] not in edge_ids:
            continue
        ev_ids = _parse_ids(ed.get("evidence_chunk_ids"))
        evidence = [chunks.get(cid, "")[:300] for cid in ev_ids[:2] if chunks.get(cid)]
        relationships.append({
            "source": ent.get(ed["source_entity_id"], ed["source_entity_id"]),
            "source_id": ed["source_entity_id"],
            "edge_type": ed["edge_type"],
            "target": ent.get(ed["target_entity_id"], ed["target_entity_id"]),
            "target_id": ed["target_entity_id"],
            "explanation": expl.get(ed["edge_id"], ""),
            "evidence": evidence,
            "evidence_chunk_ids": ev_ids,
        })
    return {
        "community_id": community_id,
        "theme_name": c.get("theme_name"),
        "top_entities": c.get("top_entities", []),
        "top_companies": c.get("top_companies", []),
        "relationships": relationships,
    }


def _default_client_model():
    from openai import OpenAI  # noqa: PLC0415
    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"])
    return client, os.environ["LLM_MODEL_NAME"]


def synthesize_narrative(run_id: str, community_id: str, client=None, model: Optional[str] = None) -> dict:
    """Connect the dots for a community into a cited narrative + captured reasoning chain."""
    d = gather_dossier(run_id, community_id)
    if client is None:
        client, model = _default_client_model()

    facts = "\n".join(
        f"- {r['source']} --{r['edge_type']}--> {r['target']}. {r['explanation']} "
        f"Evidence: {' | '.join(r['evidence'])}"
        for r in d["relationships"]
    ) or "(no relationships)"

    system = registry.get_system_prompt("narrative_synthesis") or (
        "You are a financial research analyst. CONNECT THE DOTS: using ONLY the relationships and "
        "evidence below, write a concise narrative (4-7 sentences) explaining the emerging economic "
        "narrative this cluster represents. Trace how the entities connect across multiple hops and what "
        "it implies for the companies involved. Refer to the evidence. Use ONLY the provided facts; do NOT "
        "add outside or world knowledge; do NOT give investment advice."
    )
    user = (
        f"Theme entities: {d['top_entities']}\nCompanies: {d['top_companies']}\n"
        f"Relationships with evidence:\n{facts}"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    content = resp.choices[0].message.content or ""
    m = _THINK_RE.search(content)
    reasoning_chain = m.group(1).strip() if m else ""
    narrative = _THINK_RE.sub("", content).strip()

    return {
        "community_id": community_id,
        "theme_name": d["theme_name"],
        "narrative": narrative,
        "reasoning_chain": reasoning_chain,
        "relationships": d["relationships"],
    }


def get_or_synthesize(run_id: str, community_id: str, refresh: bool = False,
                      client=None, model: Optional[str] = None) -> dict:
    """Return a cached community narrative, synthesizing (and caching) on first request."""
    cache = runs.get_run_dir(run_id) / "discovery" / "narratives" / f"{community_id}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    result = synthesize_narrative(run_id, community_id, client=client, model=model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2))
    return result
