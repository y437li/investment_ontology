# investment_ontology

This workspace defines `investment_ontology`, the MVP architecture for a time-aware economic narrative discovery engine.

MiroFish is only an implementation reference for workflow shell, file upload, background task status, and report/interaction UX. The source of truth is this project's specification and I/O contracts.

Start with:

- `INDEX.md`: maintained navigation index.
- `theme_discovery_engine_v1.md`: source-of-truth project spec.
- `CODE_OF_CONDUCT.md`: collaboration, evidence, code, and agent conduct rules.
- `docs/folder_structure.md`: workspace layout.
- `docs/formatting_standards.md`: formatting rules for docs, configs, artifacts, agents, skills, and future code.
- `docs/code_style_standards.md`: CS136-inspired encapsulation, no-hardcoding, variables, comments, service boundaries, contracts, and test rules.
- `docs/data_schema.md`: layered data schema from raw unstructured inputs to cleaned documents, structured discovery artifacts, and validation artifacts.
- `docs/io_contracts.md`: canonical input and output formats for stages, artifacts, APIs, agents, and skills.
- `docs/team_roles.md`: team responsibilities, including Data Architect and Data Engineer ownership.
- `docs/mirofish_reference.md`: what to borrow from MiroFish and what to replace.
- `configs/*.yml`: example runtime configuration.
- `agents/*.md`: shared Codex/Claude role specs.
- `skills/*.md`: shared Codex/Claude workflow specs.

MVP goal:

```text
Raw Unstructured Sources -> Cleaned Documents/Chunks -> Structured Entities/Edges -> Graph(t) -> Communities -> Theme Snapshots -> Exposure -> Freeze -> Validation -> Report
```

The first demo should be local, reproducible, and evidence-backed.

## Quickstart (local dev)

```bash
./scripts/dev_setup.sh          # create .venv (py3.11), install deps, run tests + gate
source .venv/bin/activate
uvicorn theme_engine.main:app --app-dir app/backend --reload   # start the backend
python -m pytest tests/ -q                                     # run the suite
python scripts/ci/check_consistency.py                         # spec + leakage gate
```

Implemented pipeline endpoints (M1–M7): `POST /api/runs/create`, `POST /api/data/import`, `POST /api/data/clean`, `POST /api/data/chunk`, `GET /api/runs/{id}/status`, `POST /api/extraction/run`, `POST /api/extraction/resolve`, `POST /api/graph/build`, `POST /api/themes/discover`, `POST /api/exposure/compute`, `POST /api/discovery/freeze`, `POST /api/validation/run`, `POST /api/report/generate`. Run artifacts are written as Parquet/JSON/Markdown under `data/runs/<run_id>/discovery/` (point-in-time discovery inputs), `validation/` (future market/fundamentals), and `report.md` (research report).

## Demo: end-to-end run

This runbook walks through the full pipeline from create to report using the backend API. Start the server first:

```bash
uvicorn theme_engine.main:app --app-dir app/backend --reload
```

Then execute the following API calls in order. Replace `<RUN_ID>` with the `run_id` returned by `POST /api/runs/create`.

### Step 1: Create a run

```bash
curl -s -X POST http://localhost:8000/api/runs/create \
  -H 'Content-Type: application/json' \
  -d '{"as_of_date": "2024-06-30"}' | jq .
# -> { "run_id": "<RUN_ID>", "as_of_date": "2024-06-30", ... }
```

### Step 2: Import raw documents

```bash
curl -s -X POST http://localhost:8000/api/data/import \
  -H 'Content-Type: application/json' \
  -d '{
    "run_id": "<RUN_ID>",
    "documents_dir": "tests/fixtures/extraction",
    "source_manifest_path": "tests/fixtures/extraction/source_manifest.csv"
  }' | jq .
```

### Step 3: Clean documents

```bash
curl -s -X POST http://localhost:8000/api/data/clean \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>", "documents_dir": "tests/fixtures/extraction"}' | jq .
```

### Step 4: Chunk documents

```bash
curl -s -X POST http://localhost:8000/api/data/chunk \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 5: Extract entities and edges

```bash
curl -s -X POST http://localhost:8000/api/extraction/run \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 6: Resolve entities

```bash
curl -s -X POST http://localhost:8000/api/extraction/resolve \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 7: Build entity graph

```bash
curl -s -X POST http://localhost:8000/api/graph/build \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 8: Discover themes (community detection)

```bash
curl -s -X POST http://localhost:8000/api/themes/discover \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 9: Compute company-theme exposure

```bash
curl -s -X POST http://localhost:8000/api/exposure/compute \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 10: Freeze discovery artifacts

```bash
curl -s -X POST http://localhost:8000/api/discovery/freeze \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
# -> discovery_frozen: true; discovery_artifact_hashes populated
```

### Step 11: Run validation (optional — requires market_prices.parquet)

Validation requires `data/runs/<RUN_ID>/validation/market_prices.parquet` with
forward price data strictly after `as_of_date`. Without it, status will be
`blocked_insufficient_forward_data` (not an error; caveat will appear in report).

```bash
curl -s -X POST http://localhost:8000/api/validation/run \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
```

### Step 12: Generate the research report

```bash
curl -s -X POST http://localhost:8000/api/report/generate \
  -H 'Content-Type: application/json' \
  -d '{"run_id": "<RUN_ID>"}' | jq .
# -> { "success": true, "artifact": "report.md", "report_path": "data/runs/<RUN_ID>/report.md" }
```

The report is written to `data/runs/<RUN_ID>/report.md`. It contains all required
sections (io_contracts §23), references artifact IDs for traceability, and carries
the single-snapshot / illustrative caveat (spec §2 MVP Caveats). The same run always
produces identical report bytes (deterministic).

**Caveats (spec §2)**: This is a single-snapshot MVP. All validation results are
illustrative only. No alpha or causal claim is made. Multi-period walk-forward panel
is required for any statistical association claim.

Maintenance rule:

- When adding, renaming, or materially changing a source document, config, agent, skill, or guide, update `INDEX.md`.
