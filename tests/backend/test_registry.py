"""Tests for the governance tables: ontology.yml + agents.yml registry."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import registry  # noqa: E402


def test_ontology_types_loaded():
    ents = registry.entity_types()
    edges = registry.edge_types()
    assert "Company" in ents and "EconomicConcept" in ents and "Document" in ents
    assert "benefits" in edges and "mentioned_in" in edges
    assert registry.entity_level("Company") == "company"
    assert registry.entity_level("MacroIndicator") == "macro"


def test_structural_edges():
    structural = set(registry.structural_edge_types())
    assert {"causes", "benefits", "hurts", "exposed_to", "sensitive_to"} <= structural
    assert "mentioned_in" not in structural   # evidence-only, not structural
    assert "co_occurs_with" not in structural


def test_extraction_prompt_generated_from_ontology():
    prompt = registry.get_system_prompt("entity_extraction")
    assert prompt is not None
    assert "{entity_types}" not in prompt and "{edge_types}" not in prompt  # substituted
    assert "Company" in prompt and "benefits" in prompt                     # ontology injected


def test_theme_grouping_prompt_substitutes_max():
    prompt = registry.get_system_prompt("theme_grouping", max_main_themes=5)
    assert prompt is not None and "5" in prompt and "{max_main_themes}" not in prompt


def test_extraction_valid_types_derive_from_ontology():
    from theme_engine import extraction  # noqa: PLC0415
    assert "Company" in extraction.VALID_ENTITY_TYPES
    assert "Sector" in extraction.VALID_ENTITY_TYPES        # new ontology type flows through
    assert "benefits" in extraction.VALID_EDGE_TYPES


def test_unknown_agent_returns_none():
    assert registry.get_system_prompt("does_not_exist") is None
