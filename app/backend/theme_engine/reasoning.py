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


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _relevant_evidence(
    text: str,
    source: str,
    target: str,
    max_chars: int = 260,
    financial_fact: Optional[dict] = None,
) -> str:
    """Return the sentence(s) in an evidence chunk that actually mention the related
    entities, instead of a blind char-window slice — makes the cited evidence specific.

    EG-D: when ``financial_fact`` is provided (a FinancialMetric row from B2),
    prepend a concise quantified claim so the evidence is never just a vague
    sentence.  Falls back to the sentence-level snippet when no fact is present
    (no regression).
    """
    # EG-D: build a quantified prefix when we have an extracted fact
    fact_prefix = ""
    if financial_fact:
        parts: list[str] = []
        metric = financial_fact.get("metric_name") or ""
        period = financial_fact.get("period") or ""
        val = financial_fact.get("value")
        unit = financial_fact.get("unit") or ""
        direction = financial_fact.get("direction") or ""
        is_guidance = financial_fact.get("is_guidance") or False
        if metric:
            parts.append(metric)
        if period:
            parts.append(period)
        if val is not None:
            try:
                fval = float(val)
                val_str = f"{fval:,.2f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                val_str = str(val)
            if unit:
                parts.append(f"{val_str} {unit}")
            else:
                parts.append(val_str)
        label = ": ".join(parts[:2]) + (f": {parts[2]}" if len(parts) > 2 else "")
        qualifiers: list[str] = []
        if direction:
            qualifiers.append(direction)
        qualifiers.append("guidance" if is_guidance else "actual")
        if qualifiers:
            label += f" ({', '.join(qualifiers)})"
        if label:
            fact_prefix = f"[{label}] "

    if not text:
        return fact_prefix.strip() if fact_prefix else ""
    terms = [t.lower() for t in (source, target) if t and len(t) > 2]
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sents:
        snippet = text[:max_chars]
        return (fact_prefix + snippet)[:max_chars] if fact_prefix else snippet
    hits = [(sum(1 for t in terms if t in s.lower()), i, s) for i, s in enumerate(sents)]
    hits = [h for h in hits if h[0] > 0]
    hits.sort(key=lambda x: (-x[0], len(x[2]), x[1]))   # most entities, then shortest, then earliest
    chosen = [s for _, _, s in hits[:2]] or [sents[0]]
    snippet = (" … ".join(chosen))[:max_chars]
    if fact_prefix:
        # Allow a little extra room for the prefix; still cap total length
        return (fact_prefix + snippet)[: max_chars + len(fact_prefix)]
    return snippet


def _load_fm_by_chunk(run_id: str) -> dict[str, dict]:
    """Load financial_metrics.parquet and build chunk_id -> best fact index.

    Returns {} if the artifact is not yet present (graceful fallback).
    When multiple facts share a chunk_id, the highest-confidence one wins.
    """
    p = runs.get_run_dir(run_id) / "discovery" / "financial_metrics.parquet"
    if not p.exists():
        return {}
    rows = pq.read_table(p).to_pylist()
    index: dict[str, dict] = {}
    for fm in rows:
        cid = fm.get("evidence_chunk_id") or ""
        if not cid:
            continue
        existing = index.get(cid)
        if existing is None or float(fm.get("confidence") or 0) > float(existing.get("confidence") or 0):
            index[cid] = fm
    return index


def gather_dossier(run_id: str, community_id: str) -> dict:
    """Collect a community's relationships + evidence into a structured dossier."""
    comm = _load(run_id, "communities.json")
    communities = comm.get("communities", comm)
    c = next((x for x in communities if x.get("community_id") == community_id), None)
    if c is None:
        raise ValueError(f"community not found: {community_id}")

    _entities = _load(run_id, "entities.parquet")
    ent = {e["entity_id"]: (e.get("canonical_name") or e.get("name")) for e in _entities}
    ent_type = {e["entity_id"]: e.get("entity_type") for e in _entities}
    expl = {e["edge_id"]: e.get("explanation", "") for e in _load(run_id, "edge_explanations.parquet")}
    chunks = {ch["chunk_id"]: ch.get("text", "") for ch in _load(run_id, "chunks.parquet")}
    edge_ids = set(c.get("edge_ids", []))

    # EG-D: load extracted financial facts indexed by chunk_id (graceful — {} if absent)
    fm_by_chunk = _load_fm_by_chunk(run_id)

    relationships: list[dict] = []
    for ed in _load(run_id, "edges.parquet"):
        if ed["edge_id"] not in edge_ids:
            continue
        ev_ids = _parse_ids(ed.get("evidence_chunk_ids"))
        src = ent.get(ed["source_entity_id"], ed["source_entity_id"])
        tgt = ent.get(ed["target_entity_id"], ed["target_entity_id"])
        evidence = []
        for cid in ev_ids[:2]:
            # EG-D: pass extracted fact to _relevant_evidence when available
            financial_fact = fm_by_chunk.get(cid)
            ev = _relevant_evidence(chunks.get(cid, ""), src, tgt, financial_fact=financial_fact)
            if ev:
                evidence.append(ev)
        relationships.append({
            "source": src,
            "source_id": ed["source_entity_id"],
            "source_type": ent_type.get(ed["source_entity_id"]),
            "edge_type": ed["edge_type"],
            "target": tgt,
            "target_id": ed["target_entity_id"],
            "target_type": ent_type.get(ed["target_entity_id"]),
            "extraction_method": ed.get("extraction_method") or "llm_inferred",
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
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:
        # Treat missing openai package the same as missing env vars so the
        # endpoint handler can return 503 rather than 500.
        raise KeyError("openai package not installed") from exc
    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"])
    return client, os.environ["LLM_MODEL_NAME"]


def gather_main_dossier(run_id: str, community_ids: list[str]) -> dict:
    """Union the relationships across a main theme's sub-themes into one dossier."""
    rels: list[dict] = []
    seen: set = set()
    ents: set = set()
    comps: set = set()
    for cid in community_ids:
        try:
            d = gather_dossier(run_id, cid)
        except ValueError:
            continue
        for r in d["relationships"]:
            key = (r.get("source_id"), r.get("target_id"), r["edge_type"])
            if key in seen:
                continue
            seen.add(key)
            rels.append(r)
        ents.update(d.get("top_entities", []))
        comps.update(d.get("top_companies", []))
    return {"community_id": None, "theme_name": None,
            "top_entities": sorted(ents)[:12], "top_companies": sorted(comps)[:12],
            "relationships": rels}


def _synthesize_dossier(d: dict, client, model: str) -> dict:
    """Core connect-the-dots synthesis from a dossier: narrative + ordered, labeled steps."""
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
        "connect into a sequence. Source/target names MUST come from the relationships. Each step's claim must be DIRECTIONAL and MECHANISTIC (state which way each variable moves and the channel, e.g. 'rising inflation pushes the Canadian Dollar lower (depreciation)') — never a vague 'X is sensitive to Y'. The derivation MUST RESOLVE TO THE BOTTOM LINE — trace impact down to the companies' profitability / cash flow / revenue growth (terminate at financial outcomes when present). Use ONLY the provided "
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
            except Exception as exc:
                import logging  # noqa: PLC0415
                logging.getLogger(__name__).warning("emit_narrative tool-call parse failed: %s", exc)
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
        provenance = method if method in ("document_stated", "metadata_inferred", "llm_inferred") else "llm_inferred"
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
    return {"narrative": narrative, "reasoning_steps": steps, "reasoning_chain": reasoning_chain}


def synthesize_narrative(run_id: str, community_id: str, client=None, model: Optional[str] = None) -> dict:
    """Connect the dots for a single community into a cited narrative + reasoning chain."""
    d = gather_dossier(run_id, community_id)
    if client is None:
        client, model = _default_client_model()
    out = _synthesize_dossier(d, client, model)
    return {"community_id": community_id, "theme_name": d["theme_name"],
            **out, "relationships": d["relationships"]}


def synthesize_main_narrative(run_id: str, community_ids: list[str], client=None,
                              model: Optional[str] = None, refresh: bool = False) -> dict:
    """One STORY for a whole main theme (union of its sub-themes), cached."""
    import hashlib  # noqa: PLC0415
    key = hashlib.sha256(",".join(sorted(community_ids)).encode()).hexdigest()[:16]
    cache = runs.get_run_dir(run_id) / "discovery" / "main_narratives" / f"{key}.json"
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())
        cached["relationships"] = gather_main_dossier(run_id, community_ids)["relationships"]
        return cached
    d = gather_main_dossier(run_id, community_ids)
    if client is None:
        client, model = _default_client_model()
    out = _synthesize_dossier(d, client, model)
    result = {"community_ids": sorted(community_ids), "top_entities": d["top_entities"],
              "top_companies": d["top_companies"], **out, "relationships": d["relationships"]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2))
    return result


def get_or_synthesize(run_id: str, community_id: str, refresh: bool = False,
                      client=None, model: Optional[str] = None) -> dict:
    """Return a cached community narrative, synthesizing (and caching) on first request."""
    cache = runs.get_run_dir(run_id) / "discovery" / "narratives" / f"{community_id}.json"
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())
        # relationships + evidence are DETERMINISTIC; recompute them on every read so code
        # changes (node types, specific-sentence evidence) surface without an LLM re-run.
        cached["relationships"] = gather_dossier(run_id, community_id)["relationships"]
        return cached
    result = synthesize_narrative(run_id, community_id, client=client, model=model)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2))
    return result
