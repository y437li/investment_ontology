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

# Tool for structured output: a narrative PLUS the ordered derivation (推演顺序).
_NARRATIVE_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_narrative",
        "description": "Emit the connected narrative and the ORDERED reasoning steps that derive it.",
        "parameters": {
            "type": "object",
            "properties": {
                "narrative": {"type": "string", "description": "4-7 sentence connected narrative"},
                "reasoning_steps": {
                    "type": "array",
                    "description": "ordered inference hops; each links a source entity to a target entity",
                    "items": {
                        "type": "object",
                        "properties": {
                            "order": {"type": "integer"},
                            "claim": {"type": "string", "description": "what this step infers"},
                            "source": {"type": "string", "description": "source entity name (from the relationships)"},
                            "target": {"type": "string", "description": "target entity name"},
                            "edge_type": {"type": "string"},
                        },
                        "required": ["order", "claim", "source", "target"],
                    },
                },
            },
            "required": ["narrative", "reasoning_steps"],
        },
    },
}


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
            "extraction_method": ed.get("extraction_method") or "document_stated",
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
        "evidence below, produce (1) a concise narrative (4-7 sentences) explaining the emerging economic "
        "narrative this cluster represents, and (2) an ORDERED reasoning chain (reasoning_steps) — each step "
        "is one inference hop linking a source entity to a target entity, in derivation order, so the dots "
        "connect into a sequence. Source/target names MUST come from the relationships. Use ONLY the provided "
        "facts; do NOT add outside knowledge; do NOT give investment advice. Always call emit_narrative."
    )
    user = (
        f"Theme entities: {d['top_entities']}\nCompanies: {d['top_companies']}\n"
        f"Relationships with evidence:\n{facts}"
    )
    # name -> entity_id (so each reasoning step can highlight the path on the graph)
    name_to_id: dict[str, str] = {}
    for r in d["relationships"]:
        name_to_id.setdefault(r["source"], r.get("source_id"))
        name_to_id.setdefault(r["target"], r.get("target_id"))

    import json as _json  # noqa: PLC0415
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    args: dict = {}
    content = ""
    for _ in range(3):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=[_NARRATIVE_TOOL], temperature=0.2,
        )
        msg = resp.choices[0].message
        content = msg.content or content
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            try:
                args = _json.loads(tool_calls[0].function.arguments)
                break
            except Exception:
                args = {}
        messages.append({"role": "user", "content": "Call emit_narrative with valid JSON arguments only."})

    m = _THINK_RE.search(content)
    reasoning_chain = m.group(1).strip() if m else ""
    narrative = args.get("narrative") or _THINK_RE.sub("", content).strip()

    # Provenance: a step is document_stated only if it matches a stated relationship
    # with evidence; otherwise it is the model's own inference (llm_inferred). Label
    # each step clearly so the PM knows what is evidence-backed vs inferred.
    rel_lookup = {(r["source"].lower(), r["target"].lower(), r["edge_type"]): r for r in d["relationships"]}
    steps = []
    for s in (args.get("reasoning_steps") or []):
        match = rel_lookup.get((s.get("source", "").lower(), s.get("target", "").lower(), s.get("edge_type", "")))
        method = (match.get("extraction_method") if match else None)
        provenance = method if method in ("document_stated", "llm_inferred") else "llm_inferred"
        steps.append({
            "order": s.get("order"),
            "claim": s.get("claim", ""),
            "source": s.get("source", ""), "source_id": name_to_id.get(s.get("source")),
            "target": s.get("target", ""), "target_id": name_to_id.get(s.get("target")),
            "edge_type": s.get("edge_type", ""),
            "provenance": provenance,
            "evidence": match.get("evidence") if match else [],
        })
    steps.sort(key=lambda x: (x["order"] is None, x["order"] or 0))

    return {
        "community_id": community_id,
        "theme_name": d["theme_name"],
        "narrative": narrative,
        "reasoning_steps": steps,
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
