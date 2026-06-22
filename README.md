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

Implemented pipeline endpoints (M1–M2): `POST /api/runs/create`, `POST /api/data/import`, `POST /api/data/clean`, `POST /api/data/chunk`, `GET /api/runs/{id}/status`. Run artifacts are written as Parquet under `data/runs/<run_id>/discovery/` (point-in-time discovery inputs) and `validation/` (future market/fundamentals).

Maintenance rule:

- When adding, renaming, or materially changing a source document, config, agent, skill, or guide, update `INDEX.md`.
