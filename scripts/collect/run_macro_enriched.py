import sys, shutil, json
from pathlib import Path
sys.path.insert(0,"app/backend")
from theme_engine import runs, macro_adapter, graph_build, themes, exposure, freeze, theme_hierarchy
from theme_engine.models import RunCreateRequest
SRC=Path("data/runs/run_20260622_174023/discovery")
run=runs.create_run(RunCreateRequest(as_of_date="2024-06-30")); rid=run.run_id
dst=runs.get_run_dir(rid)/"discovery"; dst.mkdir(parents=True,exist_ok=True)
for f in list(SRC.glob("*.parquet"))+list(SRC.glob("*.json")): shutil.copy(f,dst/f.name)
print(f"MACRO-RUN {rid}", flush=True)
print("macro:", macro_adapter.integrate_macro(rid), flush=True)
graph_build.build_graph(rid); n=themes.discover_themes(rid); exposure.compute_exposure(rid); freeze.freeze_discovery(rid)
print(f"communities={n}; building hierarchy (LLM)...", flush=True)
h=theme_hierarchy.build_hierarchy(rid)
for m in h["main_themes"]: print(f"  ■ {m['name'][:60]} ({len(m['sub_theme_ids'])} sub)", flush=True)
print("DONE", rid, flush=True)
