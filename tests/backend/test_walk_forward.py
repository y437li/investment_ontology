import json, sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import walk_forward, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest

def test_trajectory_growth_and_emergence():
    run = runs.create_run(RunCreateRequest(as_of_date="2024-03-31"))
    d = Path(settings.run_output_dir)/run.run_id/"discovery"; d.mkdir(parents=True, exist_ok=True)
    # chunks across 3 months
    pq.write_table(pa.table({"chunk_id":["jan","feb","mar"],
        "available_at":["2024-01-10","2024-02-10","2024-03-10"]}), d/"chunks.parquet")
    # a triangle that grows: Jan a-b, Feb +c, Mar +d (all structural 'benefits')
    pq.write_table(pa.table({
        "edge_id":["e1","e2","e3","e4"],
        "source_entity_id":["a","b","c","a"],"target_entity_id":["b","c","d","d"],
        "edge_type":["benefits"]*4,
        "evidence_chunk_ids":[["jan"],["feb"],["mar"],["mar"]],
        "extraction_method":["document_stated"]*4,
        "first_seen_at":["2024-01-10","2024-02-10","2024-03-10","2024-03-10"]}), d/"edges.parquet")
    pq.write_table(pa.table({"entity_id":["a","b","c","d"],
        "canonical_name":["A","B","C","D"]}), d/"entities.parquet")
    (d/"communities.json").write_text(json.dumps({"communities":[{"community_id":"c1","node_ids":["a","b","c","d"],"theme_name":"T"}]}))
    out = walk_forward.theme_trajectories(run.run_id, min_size=2)
    assert out["months"] == ["2024-01-31","2024-02-29","2024-03-31"]
    assert out["themes"], "a trajectory should be produced"
    sizes = [t["size"] for t in out["themes"][0]["trajectory"]]
    assert sizes[-1] >= sizes[0]            # grows over time
    assert out["themes"][0]["momentum"] >= 0
