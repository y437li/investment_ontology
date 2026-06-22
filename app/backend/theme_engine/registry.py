"""Loader for the managed governance tables.

- ``configs/ontology.yml`` — what the engine extracts/keeps (entity + edge types,
  exclusions, rules). The single source of truth for extraction scope.
- ``configs/agents.yml`` — the processing-agent registry: every LLM step, what it
  handles, and its MAINTAINED prompt.

Prompts and the ontology are edited in these tables, NOT in code. Loading is
tolerant: if a table or pyyaml is missing, callers fall back to built-in defaults.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "configs"))


def _load_yaml(name: str) -> dict:
    p = _CONFIG_DIR / name
    if not p.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


@functools.lru_cache(maxsize=1)
def load_ontology() -> dict:
    return _load_yaml("ontology.yml")


@functools.lru_cache(maxsize=1)
def _load_agents() -> dict:
    return (_load_yaml("agents.yml") or {}).get("agents", {}) or {}


def entity_types() -> list[str]:
    return list((load_ontology().get("entity_types") or {}).keys())


def edge_types() -> list[str]:
    return list((load_ontology().get("edge_types") or {}).keys())


def structural_edge_types() -> list[str]:
    et = load_ontology().get("edge_types") or {}
    return [k for k, v in et.items() if (v or {}).get("structural")]


def entity_level(entity_type: str) -> str | None:
    """Factor-hierarchy level for an entity type (macro/industry/company/idiosyncratic/...)."""
    return ((load_ontology().get("entity_types") or {}).get(entity_type) or {}).get("level")


def _ontology_fields() -> dict:
    onto = load_ontology()
    ents = onto.get("entity_types") or {}
    edges = onto.get("edge_types") or {}
    excl = onto.get("exclude") or []
    return {
        "entity_types": "\n".join(f"- {k}: {(v or {}).get('definition', '')}" for k, v in ents.items()),
        "edge_types": "\n".join(f"- {k}: {(v or {}).get('definition', '')}" for k, v in edges.items()),
        "exclude": "\n".join(f"- {x}" for x in excl),
    }


def get_system_prompt(agent_id: str, **extra) -> str | None:
    """Return the maintained system prompt for an agent, with ontology fields and
    any ``extra`` placeholders substituted. None if the agent/table is unavailable."""
    agent = _load_agents().get(agent_id)
    if not agent or not agent.get("system_prompt"):
        return None
    fields = _ontology_fields()
    fields.update({k: str(v) for k, v in extra.items()})
    prompt = agent["system_prompt"]
    for key, value in fields.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt
