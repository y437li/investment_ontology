import sys, csv, time, json
from pathlib import Path
ROOT=Path.cwd(); sys.path.insert(0,str(ROOT/"app"/"backend"))
from theme_engine import (runs, data_import, data_cleaning, chunking, extraction,
    entity_resolution, graph_build, themes, exposure, freeze, theme_hierarchy)
from theme_engine.models import RunCreateRequest
DOCS=ROOT/"data"/"inputs"/"documents"
rows=list(csv.DictReader(open(DOCS/"news"/"source_manifest.csv")))
man=DOCS/"_broad.csv"
with man.open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=data_import.REQUIRED_MANIFEST_COLUMNS); w.writeheader()
    [w.writerow({c:r.get(c,'') for c in data_import.REQUIRED_MANIFEST_COLUMNS}) for r in rows]
run=runs.create_run(RunCreateRequest(as_of_date="2024-06-30")); rid=run.run_id
print(f"RUN {rid}: {len(rows)} news docs", flush=True)
data_import.import_manifest(rid,str(ROOT),str(man)); data_cleaning.clean_documents(rid)
nch=chunking.chunk_documents(rid); print(f"{nch} chunks; extracting (MiniMax, ~40min)...", flush=True)
t=time.time(); ne,nd=extraction.run_extraction(rid)
print(f"extraction: {ne} entities {nd} edges ({time.time()-t:.0f}s)", flush=True)
entity_resolution.resolve_entities(rid); graph_build.build_graph(rid)
ncomm=themes.discover_themes(rid); exposure.compute_exposure(rid); freeze.freeze_discovery(rid)
print(f"communities: {ncomm}; building hierarchy...", flush=True)
h=theme_hierarchy.build_hierarchy(rid)
print(f"MAIN THEMES ({len(h['main_themes'])}):", flush=True)
for m in h["main_themes"]: print(f"  ■ {m['name']} ({len(m['sub_theme_ids'])} sub)", flush=True)
man.unlink(); print("DONE", rid, flush=True)
