import json, sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import reasoning, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest

class _Msg:
    def __init__(self, c=None, tc=None): self.content=c; self.tool_calls=tc
class _TC:
    def __init__(self, a): self.function=type("F",(),{"arguments":a})()
class Fake:
    def __init__(self, a): self._a=a
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **_):
        return type("R",(),{"choices":[type("C",(),{"message":_Msg("<think>t</think>",[_TC(self._a)])})()]})()

def _seed():
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir)/run.run_id/"discovery"; d.mkdir(parents=True, exist_ok=True)
    d.joinpath("communities.json").write_text(json.dumps({"communities":[
        {"community_id":"c1","edge_ids":["e1"],"top_entities":["rates"],"top_companies":["RBC"]},
        {"community_id":"c2","edge_ids":["e2"],"top_entities":["inflation"],"top_companies":["Suncor"]}]}))
    pq.write_table(pa.table({"entity_id":["a","b","c","d2"],"canonical_name":["rates","RBC","inflation","Suncor"]}), d/"entities.parquet")
    pq.write_table(pa.table({"edge_id":["e1","e2"],"source_entity_id":["a","c"],"target_entity_id":["b","d2"],
        "edge_type":["benefits","benefits"],"evidence_chunk_ids":[["x"],["y"]],
        "extraction_method":["document_stated","document_stated"]}), d/"edges.parquet")
    pq.write_table(pa.table({"edge_id":["e1","e2"],"explanation":["rates help RBC","inflation helps Suncor"]}), d/"edge_explanations.parquet")
    pq.write_table(pa.table({"chunk_id":["x","y"],"text":["rates up","inflation up"]}), d/"chunks.parquet")
    return run.run_id

def test_main_narrative_unions_subthemes_and_caches():
    rid=_seed()
    args=json.dumps({"narrative":"Combined story.","reasoning_steps":[
        {"order":1,"claim":"rates help banks","source":"rates","target":"RBC","edge_type":"benefits"}]})
    r=reasoning.synthesize_main_narrative(rid, ["c1","c2"], client=Fake(args), model="x")
    assert r["narrative"]=="Combined story."
    # union dossier spans both sub-themes
    assert len(r["relationships"])==2
    assert r["community_ids"]==["c1","c2"]
    # cached on second call (no client)
    again=reasoning.synthesize_main_narrative(rid, ["c1","c2"])
    assert again["narrative"]=="Combined story."
