import json, sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import concept_resolution, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest

class _Msg:
    def __init__(self, tc): self.content=None; self.tool_calls=tc
class _TC:
    def __init__(self, a): self.function=type("F",(),{"arguments":a})()
class FakeClient:
    def __init__(self, a): self._a=a
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **_):
        return type("R",(),{"choices":[type("C",(),{"message":_Msg([_TC(self._a)])})()]})()

def test_merges_synonym_concepts_and_remaps_edges():
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir)/run.run_id/"discovery"; d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"schema_version":[1,1,1,1],
        "entity_id":["e1","e2","e3","co"],
        "entity_type":["EconomicConcept","EconomicConcept","Event","Company"],
        "name":["air pollution","air pollution violations","Clean Air Act Violations","Suncor Energy"],
        "canonical_name":["air pollution","air pollution violations","Clean Air Act Violations","Suncor Energy"],
        "ticker":[None,None,None,None]}), d/"entities.parquet")
    pq.write_table(pa.table({"schema_version":[1,1],"edge_id":["x","y"],
        "source_entity_id":["co","co"],"target_entity_id":["e1","e2"],
        "edge_type":["exposed_to","exposed_to"],"confidence":[0.7,0.7],
        "evidence_chunk_ids":[["c"],["c"]],"first_seen_at":["2024-06-01","2024-06-01"],
        "last_seen_at":["2024-06-01","2024-06-01"],"as_of_date":["2024-06-30","2024-06-30"],
        "extraction_method":["document_stated","document_stated"],"review_status":["auto","auto"]}),
        d/"edges.parquet")
    args = json.dumps({"groups":[{"canonical_name":"Air pollution / Clean Air Act",
        "members":["air pollution","air pollution violations","Clean Air Act Violations"]}]})
    res = concept_resolution.canonicalize_concepts(run.run_id, client=FakeClient(args), model="x")
    assert res["merged"] == 2          # 3 synonyms -> 1 (2 merged away)
    ents = pq.read_table(d/"entities.parquet").to_pylist()
    concepts = [e for e in ents if e["entity_type"] in ("EconomicConcept","Event")]
    assert len(concepts) == 1 and concepts[0]["canonical_name"] == "Air pollution / Clean Air Act"
    edges = pq.read_table(d/"edges.parquet").to_pylist()
    assert len(edges) == 1             # the two exposed_to edges collapsed to one (same endpoints now)
