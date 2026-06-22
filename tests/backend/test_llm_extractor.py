"""Hermetic tests for the LLM (OpenAI-compatible) extractor parsing + denoise.

No network: the real MiniMax call is never made here. We test the pure
parsing (_to_result), denoise/alias-merge (_clean_result), and env-based
extractor selection.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import extraction  # noqa: E402
from theme_engine.extraction import (  # noqa: E402
    OpenAIExtractor, RuleBasedExtractor, build_default_extractor,
    _clean_result, ExtractionResult, EntityCandidate, EdgeCandidate,
)


def test_to_result_maps_tool_args_and_stated_flag():
    args = {
        "entities": [
            {"name": "Suncor Energy", "entity_type": "Company", "confidence": 0.9},
            {"name": "oil prices", "entity_type": "Commodity"},
            {"name": "bogus", "entity_type": "NotAType"},  # dropped: invalid type
        ],
        "edges": [
            {"source_name": "oil prices", "target_name": "Suncor Energy",
             "edge_type": "benefits", "stated_in_text": True},
            {"source_name": "X", "target_name": "Y", "edge_type": "causes",
             "stated_in_text": False},
        ],
    }
    r = OpenAIExtractor._to_result(args)
    assert {e.name for e in r.entities} == {"Suncor Energy", "oil prices"}
    methods = {(e.source_name, e.extraction_method) for e in r.edges}
    assert ("oil prices", "document_stated") in methods   # stated_in_text True
    assert ("X", "llm_inferred") in methods                # stated_in_text False


def test_clean_result_drops_noise_and_merges_aliases():
    res = ExtractionResult(
        entities=[
            EntityCandidate("SU", "Company", 0.8, "document_stated"),               # alias -> Suncor Energy
            EntityCandidate("Suncor Energy Inc", "Company", 0.8, "document_stated"),  # suffix -> Suncor Energy
            EntityCandidate("May 3, 2023", "Event", 0.8, "document_stated"),         # date -> dropped
            EntityCandidate("Paul M. Mendes", "Company", 0.8, "document_stated"),    # person -> dropped
            EntityCandidate("Annual Meeting", "EconomicConcept", 0.8, "document_stated"),  # boilerplate -> dropped
            EntityCandidate("Uranium", "Commodity", 0.8, "document_stated"),
        ],
        edges=[
            EdgeCandidate("SU", "Uranium", "exposed_to", 0.7, "document_stated", ""),
            EdgeCandidate("May 3, 2023", "Uranium", "co_occurs_with", 0.7, "document_stated", ""),  # endpoint dropped
        ],
    )
    cleaned = _clean_result(res)
    names = sorted(e.name for e in cleaned.entities)
    assert names == ["Suncor Energy", "Uranium"]            # aliases merged, noise gone
    assert len(cleaned.edges) == 1                           # date-endpoint edge dropped
    assert cleaned.edges[0].source_name == "Suncor Energy"  # endpoint canonicalized


def test_build_default_extractor_env_selection(monkeypatch):
    for k in ("EXTRACTOR", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(build_default_extractor(), RuleBasedExtractor)  # no env -> hermetic

    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://example/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "MiniMax-M3")
    assert isinstance(build_default_extractor(), OpenAIExtractor)     # configured -> LLM

    monkeypatch.setenv("EXTRACTOR", "rule_based")
    assert isinstance(build_default_extractor(), RuleBasedExtractor)  # forced override
