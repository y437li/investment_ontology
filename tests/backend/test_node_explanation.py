"""Tests for the Node Explanation Framework (spec §13)."""

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import node_explanation, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.main import app  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402

client = TestClient(app)


def _seed_run() -> str:
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"entity_id": ["a", "b"], "entity_type": ["Commodity", "Company"],
                             "canonical_name": ["oil prices", "Suncor Energy"]}), d / "entities.parquet")
    pq.write_table(pa.table({"edge_id": ["e1"], "source_entity_id": ["a"], "target_entity_id": ["b"],
                             "edge_type": ["benefits"], "evidence_chunk_ids": [["chk1"]],
                             "first_seen_at": ["2024-06-01"]}), d / "edges.parquet")
    pq.write_table(pa.table({"edge_id": ["e1"], "explanation": ["Higher oil prices benefit Suncor."]}),
                   d / "edge_explanations.parquet")
    return run.run_id


def test_node_profile_what_why_matters():
    rid = _seed_run()
    p = node_explanation.node_profile(rid, "a")
    assert p["name"] == "oil prices"
    assert p["entity_type"] == "Commodity"
    assert p["level"] == "macro"                      # from ontology -> why it matters
    assert p["definition"]                             # what it is (from ontology)
    assert p["evidence_count"] == 1                    # §13 evidence count
    assert p["degree"] == 1
    assert p["why_present"][0]["edge_type"] == "benefits"   # why it's in the graph
    assert p["why_present"][0]["other"] == "Suncor Energy"
    assert "Suncor Energy" in p["related_entities"]


def test_node_profile_endpoint_and_404():
    rid = _seed_run()
    assert client.get(f"/api/themes/{rid}/nodes/a/profile").status_code == 200
    assert client.get(f"/api/themes/{rid}/nodes/nope/profile").status_code == 404


def test_explain_node_caches_with_fake_client():
    rid = _seed_run()

    class _Msg:
        content = "<think>x</think>Oil prices are a macro driver of Suncor's revenue."

    class _Client:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **_):
            return type("R", (), {"choices": [type("C", (), {"message": _Msg()})()]})()

    out = node_explanation.explain_node(rid, "a", client=_Client(), model="x")
    assert out["explanation"] == "Oil prices are a macro driver of Suncor's revenue."
    assert "<think>" not in out["explanation"]
    cache = Path(settings.run_output_dir) / rid / "discovery" / "node_explanations" / "a.json"
    assert cache.exists()
