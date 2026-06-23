import sys, json
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0,"app/backend")
from theme_engine import (runs, concept_resolution, macro_adapter, graph_build,
    themes, exposure, freeze, theme_hierarchy)
from theme_engine.models import RunCreateRequest
SRCS=[Path("data/runs/run_20260622_174023/discovery"),   # bank / cross-sector
      Path("data/runs/run_20260622_214929/discovery")]    # power / grid / macro
run=runs.create_run(RunCreateRequest(as_of_date="2024-06-30")); rid=run.run_id
dst=runs.get_run_dir(rid)/"discovery"; dst.mkdir(parents=True,exist_ok=True)
def merge(name, key):
    tabs=[pq.read_table(s/name) for s in SRCS if (s/name).exists()]
    base=tabs[0]; cols=base.column_names; seen={}
    for t in tabs:
        for r in t.to_pylist(): seen[r.get(key)]={c:r.get(c) for c in cols}
    pq.write_table(pa.Table.from_pylist(list(seen.values()), schema=base.schema), dst/name)
    return len(seen)
print(f"COMBINED-RUN {rid}", flush=True)
for nm,k in [("entities.parquet","entity_id"),("edges.parquet","edge_id"),
             ("edge_explanations.parquet","edge_id"),("chunks.parquet","chunk_id")]:
    print(f"  merged {nm}: {merge(nm,k)}", flush=True)
print("concept dedup:", concept_resolution.canonicalize_concepts(rid), flush=True)
print("macro:", macro_adapter.integrate_macro(rid), flush=True)
graph_build.build_graph(rid); n=themes.discover_themes(rid); exposure.compute_exposure(rid); freeze.freeze_discovery(rid)
print(f"communities={n}; building hierarchy...", flush=True)
h=theme_hierarchy.build_hierarchy(rid)
for m in h["main_themes"]: print(f"  ■ {m['name'][:58]} ({len(m['sub_theme_ids'])} sub)", flush=True)
print("DONE", rid, flush=True)
