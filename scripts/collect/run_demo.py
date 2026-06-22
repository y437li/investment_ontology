#!/usr/bin/env python3
"""Drive the collected EDGAR batch through the real pipeline:
build manifest (filings adapter) -> create run -> import -> clean -> chunk.
Operator tool; writes a real run under data/runs/.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "backend"))

from theme_engine import runs, data_import, data_cleaning, chunking  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402
from theme_engine.adapters import filings  # noqa: E402

DOCS = ROOT / "data" / "inputs" / "documents"
VINTAGE = "2024-07-01T00:00:00Z"
AS_OF = "2024-06-30"

rows = []
for comp in sorted(p for p in DOCS.iterdir() if p.is_dir()):
    subs = comp / "submissions.json"
    if not subs.exists():
        continue
    for row in filings.build_source_manifest(subs, comp, VINTAGE):
        # raw_path must be repo-root-relative: the cleaning stage resolves it
        # from REPO_ROOT, so we pass documents_dir=REPO_ROOT below to match.
        row["raw_path"] = f"data/inputs/documents/{comp.name}/{row['raw_path']}"
        rows.append(row)

man = DOCS / "source_manifest.csv"
with man.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=data_import.REQUIRED_MANIFEST_COLUMNS)
    w.writeheader()
    for row in rows:
        w.writerow({c: row.get(c, "") for c in data_import.REQUIRED_MANIFEST_COLUMNS})
print(f"manifest rows: {len(rows)}")

run = runs.create_run(RunCreateRequest(as_of_date=AS_OF))
print(f"run_id: {run.run_id}")
print("import:", data_import.import_manifest(run.run_id, str(DOCS), str(man)))
print("clean: ", data_cleaning.clean_documents(run.run_id))
print("chunk: ", chunking.chunk_documents(run.run_id))
print("artifacts:", sorted(p.name for p in (ROOT / "data" / "runs" / run.run_id / "discovery").glob("*")))
