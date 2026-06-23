import sys, shutil, json
from pathlib import Path
sys.path.insert(0,"app/backend")
from theme_engine import (runs, concept_resolution, macro_adapter, graph_build,
    themes, exposure, freeze, theme_hierarchy)
from theme_engine.models import RunCreateRequest
SRC=Path("data/runs/run_20260622_214929/discovery")
asof=json.loads((SRC.parent/"run_manifest.json").read_text())["as_of_date"]
run=runs.create_run(RunCreateRequest(as_of_date=asof)); rid=run.run_id
dst=runs.get_run_dir(rid)/"discovery"; dst.mkdir(parents=True,exist_ok=True)
for f in list(SRC.glob("*.parquet"))+list(SRC.glob("*.json")): shutil.copy(f,dst/f.name)
print(f"ENRICHED-RUN {rid} (as_of {asof})", flush=True)
print("concept dedup:", concept_resolution.canonicalize_concepts(rid), flush=True)
print("macro:", macro_adapter.integrate_macro(rid), flush=True)
graph_build.build_graph(rid); n=themes.discover_themes(rid); exposure.compute_exposure(rid); freeze.freeze_discovery(rid)
print(f"communities={n}; building hierarchy...", flush=True)
h=theme_hierarchy.build_hierarchy(rid)
for m in h["main_themes"]: print(f"  ■ {m['name'][:58]} ({len(m['sub_theme_ids'])} sub)", flush=True)
print("DONE", rid, flush=True)
