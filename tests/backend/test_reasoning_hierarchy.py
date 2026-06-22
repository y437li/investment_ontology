"""Hermetic tests for connect-the-dots reasoning + theme hierarchy.

No network: a fake OpenAI-compatible client is injected. We write a minimal run's
discovery artifacts directly and exercise dossier gathering, narrative synthesis +
caching, hierarchy grouping + loading, and the LLM-not-configured endpoint guard.
"""

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import reasoning, theme_hierarchy, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.main import app  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402

client = TestClient(app)


# ── fake OpenAI-compatible client ────────────────────────────────────────────
class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]


class _ToolCall:
    def __init__(self, args):
        self.function = type("F", (), {"arguments": args})()


class FakeClient:
    def __init__(self, msg):
        self._msg = msg

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **_):
        return _Resp(self._msg)


def _seed_run() -> str:
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    (d / "communities.json").write_text(json.dumps({"communities": [{
        "community_id": "c1", "edge_ids": ["e1"], "size": 2,
        "top_entities": ["oil prices"], "top_companies": ["Suncor Energy"],
        "theme_name": "Energy", "theme_summary": ""}]}))
    pq.write_table(pa.table({"entity_id": ["a", "b"], "canonical_name": ["oil prices", "Suncor Energy"]}),
                   d / "entities.parquet")
    pq.write_table(pa.table({"edge_id": ["e1"], "source_entity_id": ["a"], "target_entity_id": ["b"],
                             "edge_type": ["benefits"], "evidence_chunk_ids": [["chk1"]]}),
                   d / "edges.parquet")
    pq.write_table(pa.table({"edge_id": ["e1"], "explanation": ["Text states higher oil prices benefit Suncor."]}),
                   d / "edge_explanations.parquet")
    pq.write_table(pa.table({"chunk_id": ["chk1"], "text": ["Higher oil prices benefit Suncor Energy."]}),
                   d / "chunks.parquet")
    return run.run_id


def test_dossier_and_narrative_synthesis():
    rid = _seed_run()
    dossier = reasoning.gather_dossier(rid, "c1")
    assert dossier["relationships"][0]["source"] == "oil prices"
    assert dossier["relationships"][0]["target"] == "Suncor Energy"
    assert dossier["relationships"][0]["evidence"]  # evidence text resolved

    fake = FakeClient(_Msg(content="<think>oil up -> Suncor benefits</think>Suncor benefits from higher oil prices."))
    out = reasoning.synthesize_narrative(rid, "c1", client=fake, model="x")
    assert "benefits from higher oil prices" in out["narrative"]
    assert "oil up -> Suncor benefits" in out["reasoning_chain"]  # CoT captured
    assert "<think>" not in out["narrative"]


def test_get_or_synthesize_caches():
    rid = _seed_run()
    fake = FakeClient(_Msg(content="Cached narrative."))
    reasoning.get_or_synthesize(rid, "c1", client=fake, model="x")
    cache = Path(settings.run_output_dir) / rid / "discovery" / "narratives" / "c1.json"
    assert cache.exists()
    # second call returns cached without a client (no network)
    again = reasoning.get_or_synthesize(rid, "c1")
    assert again["narrative"] == "Cached narrative."


def test_hierarchy_groups_and_loads():
    rid = _seed_run()
    fake = FakeClient(_Msg(tool_calls=[_ToolCall(json.dumps(
        {"main_themes": [{"name": "Energy markets", "summary": "oil", "community_ids": ["c1"]}]}))]))
    h = theme_hierarchy.build_hierarchy(rid, client=fake, model="x")
    assert h["main_themes"][0]["name"] == "Energy markets"
    assert "c1" in h["main_themes"][0]["sub_theme_ids"]
    assert theme_hierarchy.load_hierarchy(rid)["sub_theme_count"] == 1


def test_endpoints_guard_when_llm_absent(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME"):
        monkeypatch.delenv(k, raising=False)
    rid = _seed_run()
    # hierarchy not built yet -> 404
    assert client.get(f"/api/themes/{rid}/hierarchy").status_code == 404
    # narrative with no LLM configured -> 503
    assert client.get(f"/api/themes/{rid}/communities/c1/narrative").status_code == 503
