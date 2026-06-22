import json, sys
from pathlib import Path
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import subgraph, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest

def test_union_subgraph_filters_to_communities():
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"; d.mkdir(parents=True, exist_ok=True)
    (d/"communities.json").write_text(json.dumps({"communities":[
        {"community_id":"c1","node_ids":["a","b"]},
        {"community_id":"c2","node_ids":["z"]}]}))
    (d/"graph.json").write_text(json.dumps({"nodes":[
        {"entity_id":"a","entity_type":"MacroIndicator","label":"Rates"},
        {"entity_id":"b","entity_type":"Company","label":"RBC"},
        {"entity_id":"z","entity_type":"Company","label":"Other"}],
        "edges":[
        {"source_entity_id":"a","target_entity_id":"b","edge_type":"benefits"},
        {"source_entity_id":"a","target_entity_id":"z","edge_type":"benefits"},
        {"source_entity_id":"a","target_entity_id":"b","edge_type":"mentioned_in"}]}))
    sg = subgraph.community_subgraph(run.run_id, ["c1"])
    assert {n["id"] for n in sg["nodes"]} == {"a","b"}           # only c1 nodes
    assert sg["nodes"][0]["level"] in ("macro","company")        # levels attached
    assert sg["edge_count"] == 1                                  # a->b benefits; a->z excluded (z not in c1); mentioned_in excluded
    assert sg["edges"][0]["edge_type"] == "benefits"
