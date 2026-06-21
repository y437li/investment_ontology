# Theme Discovery Engine v1

## Time-Aware Economic Narrative Discovery Platform

Project slug:

```text
investment_ontology
```

This document is the core project specification. It uses the original design as the source of truth and only borrows selected implementation ideas from MiroFish:

- Keep the workflow shell: import data -> build graph -> inspect result -> generate report -> interact.
- Reuse the idea of project/run state and asynchronous tasks.
- Reuse file upload, text extraction, chunking, LLM structured extraction, and report UX patterns.
- Do not reuse MiroFish's Twitter/Reddit/OASIS simulation path as the core investment logic.

Source-of-truth order:

1. `theme_discovery_engine_v1.md` defines the product logic, research boundary, milestones, and team shape.
2. `docs/io_contracts.md` defines the canonical input/output contracts for artifacts and APIs.
3. `docs/mirofish_reference.md` is only an implementation reference for workflow shell, page flow, background tasks, and file-backed run state.

The platform is not a stock prediction engine. It is a time-aware economic narrative discovery engine.

---

# 1. Product Goal

Goal:

- Discover emerging economic narratives from historical information available at the time.
- Track narrative evolution through time.
- Identify potential beneficiaries through graph propagation.
- Validate whether discovered communities are associated with future fundamental and market outcomes.

Core principle:

> Theme is output, not input.

Themes emerge from:

```text
Raw Unstructured Sources -> Cleaned Documents/Chunks -> Structured Entities/Edges -> Graph(t) -> Communities -> Theme Snapshots -> Exposure -> Freeze -> Validation
```

The system should answer:

1. What narratives are forming?
2. What narratives are accelerating?
3. What narratives are splitting or merging?
4. Which companies are exposed?
5. Which narratives later become economically important?

---

# 2. MVP Boundary

The v1 MVP must be a small but extensible vertical slice. It should be able to run locally and produce inspectable artifacts.

## In Scope

- 20-60 companies in one defined universe.
- 12-24 months of historical material for the first demo.
- Point-in-time document ingestion with `available_at`.
- Entity and relationship extraction from filings, transcripts, press releases, research notes, or news.
- Temporal graph snapshots.
- Louvain or Leiden community discovery.
- Theme naming and summarization as interpretation only.
- Company-theme exposure scoring.
- Minimal validation using 1M and 3M forward returns, plus optional fundamental metrics.
- A research dashboard with evidence drilldown.
- Exportable markdown report.

## Out of Scope for MVP

- Full-market data lake.
- Bloomberg/FactSet/Refinitiv integration.
- Production permissions and multi-tenant user management.
- Kubernetes or distributed graph processing.
- High-frequency updates.
- Trading automation.
- Social-media agent simulation as investment evidence.
- Claims such as "predict anything".

## MVP Caveats / Known Limitations

These limits are intentional. They keep the MVP self-consistent and honest. Implementers must not silently exceed them.

- Single-snapshot metric gap: a single `as_of_date` demo has no prior window, so `Momentum`, `Birth Score`, `Novelty`, and `Acceleration` are undefined and must not be produced. Only single-snapshot metrics (`Strength`, `Cohesion`, `Saturation` as coverage) are valid. See section 20.
- Winning Zone is not computable in a single-snapshot MVP because it depends on Birth Score, Momentum, and Novelty. See section 21.
- Validation is illustrative only in the single-snapshot MVP. One `as_of_date` over 20-60 companies yields a single cross-sectional draw, which cannot support any statistical claim that themes are associated with future outcomes. MVP validation demonstrates pipeline connectivity, basket reproducibility, and return traceability, not statistical significance. Statistical association requires the multi-period walk-forward in section 22.
- A meaningful research claim requires at least a minimal walk-forward (3+ time points). Until then, no excess-return claim may be stated as a finding.
- Required behavior in single-snapshot mode:
  - API/UI must explicitly label all temporal metrics as unavailable.
  - Discovery outputs must never fabricate `0`, `"N/A"`, or fake confidence intervals for temporal fields.
  - Validation outputs must clearly indicate pipeline-only interpretation (no causal or alpha claim).

---

# 3. MiroFish Reference Boundary

MiroFish is useful as an implementation reference, not as the research methodology.

Hard boundary:

- Use this project's nouns in implementation: run, artifact, document, chunk, entity, edge, graph, community, theme, exposure, validation, report.
- Treat MiroFish names such as simulation, OASIS, Twitter, Reddit, and social agents as migration hints only.
- If MiroFish workflow and this spec conflict, this spec and `docs/io_contracts.md` win.

## Borrow

MiroFish-like workflow:

```text
Home
-> Data Import
-> Graph Build
-> Theme Discovery
-> Validation
-> Report / Interaction
```

Implementation ideas to reuse:

- Vue + Vite frontend structure.
- Flask backend if modifying MiroFish directly.
- File upload and allowed file handling.
- Project/run state object.
- Background task status and progress messages.
- Text extraction and chunking pipeline.
- LLM structured extraction and report generation.

## Replace

MiroFish parts to replace:

- `SimulationView`, `SimulationRunView`, OASIS profile generation, and Twitter/Reddit actions should be replaced by theme discovery and validation pages.
- Zep may be used for a demo graph memory, but v1 should persist canonical research artifacts locally.
- ReportAgent must not create unsupported investment conclusions; it can only summarize evidence and validation artifacts.

## New Route Shape

Frontend route target:

```text
/                               Home
/runs/:runId/import              Data Import
/runs/:runId/graph               Graph Build
/runs/:runId/themes              Theme Discovery
/runs/:runId/validation          Validation
/runs/:runId/report              Report
/runs/:runId/interaction         Evidence Q&A
```

Backend API target:

```text
POST /api/runs/create
POST /api/data/import
POST /api/data/clean
POST /api/data/chunk
GET  /api/runs/:run_id/status
POST /api/extraction/run
POST /api/extraction/resolve
POST /api/graph/build
POST /api/themes/discover
GET  /api/themes/:run_id
POST /api/exposure/compute
POST /api/discovery/freeze
POST /api/validation/run
POST /api/report/generate
GET  /api/artifacts/:run_id/:artifact_name
```

---

# 4. Technical Stack

## MVP Stack

Backend:

- Python 3.11 or 3.12.
- Flask if adapting MiroFish; FastAPI only if starting fresh.
- Pydantic for structured schemas.
- DuckDB for local research queries.
- Parquet/JSON for run artifacts.
- pandas and pyarrow for data handling.
- networkx plus igraph/leidenalg for graph and community detection.
- scikit-learn for simple similarity and validation baselines.
- OpenAI-compatible SDK for LLM calls.
- PyMuPDF for PDF extraction.
- pytest for smoke tests.

Frontend:

- Vue + Vite.
- axios for API calls.
- ECharts or Plotly for metrics.
- Cytoscape.js or Sigma.js for graph visualization.
- Markdown rendering for reports.

Storage:

- Local filesystem under `data/` for MVP.
- DuckDB database file under `data/db/theme_engine.duckdb`.
- Run artifacts under `data/runs/<run_id>/`.
- LLM and embedding cache under `data/cache/`.

Optional after MVP:

- PostgreSQL + pgvector.
- Redis + RQ or Celery.
- S3 or MinIO.
- Neo4j only if graph inspection and query needs exceed edge-table workflows.

## Environment Variables

Required:

```env
APP_ENV=local
DATA_DIR=./data
CONFIG_DIR=./configs
RUN_OUTPUT_DIR=./data/runs
DUCKDB_PATH=./data/db/theme_engine.duckdb

LLM_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=...
EMBEDDING_MODEL_NAME=...

LLM_CACHE_ENABLED=true
MAX_LLM_CONCURRENCY=3
```

Optional:

```env
OBJECT_STORE_MODE=local
OBJECT_STORE_PATH=./data/raw
POSTGRES_URL=
REDIS_URL=
```

---

# 5. Folder Format

The workspace should use this structure:

```text
investment_ontology/
  INDEX.md
  theme_discovery_engine_v1.md
  README.md
  CODE_OF_CONDUCT.md
  configs/
    universe.example.yml
    pipeline.example.yml
    validation.example.yml
  docs/
    folder_structure.md
    formatting_standards.md
    code_style_standards.md
    data_schema.md
    io_contracts.md
    team_roles.md
    mirofish_reference.md
    implementation_checklist.md
  agents/
    README.md
    orchestrator.md
    data_architect_agent.md
    data_engineering_agent.md
    data_ingestion_agent.md
    data_cleaning_agent.md
    extraction_agent.md
    graph_theme_agent.md
    validation_agent.md
    frontend_report_agent.md
  skills/
    README.md
    point_in_time_data.md
    unstructured_data_cleaning.md
    entity_relation_extraction.md
    temporal_graph_discovery.md
    validation_backtest.md
    evidence_report_generation.md
    backend_api_implementation.md
    pipeline_artifact_implementation.md
    frontend_workflow_implementation.md
    test_quality_gate.md
    maintainable_code_implementation.md
  data/
    inputs/
      documents/
      market/
      fundamentals/
    db/
    runs/
    cache/
  app/
    backend/
    frontend/
  scripts/
  tests/
```

`agents/` and `skills/` are tool-agnostic markdown files. Codex and Claude can both read and follow them.

`INDEX.md` is the maintained navigation index. Update it whenever source documents, configs, agent specs, skill specs, or implementation guides are added, renamed, or materially changed.

`docs/io_contracts.md` defines the canonical input and output formats for stages, artifacts, APIs, agents, and skills. Implementation work must preserve those contracts unless this source-of-truth document is updated first.

`docs/data_schema.md` defines the required data order: raw unstructured inputs, cleaned unstructured artifacts, structured discovery artifacts, and structured validation artifacts.

All tests live in one place: the top-level `tests/` directory. Test files must not be scattered next to implementation code under `app/backend/` or `app/frontend/`. Mirror the source layout inside `tests/` (for example `tests/backend/`, `tests/frontend/`, `tests/pipeline/`) so a single command can discover and run the full suite. This keeps the test surface and the leakage/quality gates reviewable in one location.

---

# 6. Data Sources

## Company Documents

- Annual reports.
- Quarterly reports.
- MD&A.
- Earnings call transcripts.
- Investor day presentations.
- Press releases.

## News

- Reuters.
- Financial Post.
- BNN.
- Globe and Mail.
- Other curated sources if `published_at` and `available_at` are known.

## Macro

- Bank of Canada.
- Federal Reserve.
- CPI.
- GDP.
- Employment.
- PMI.

## Commodities

- Oil.
- Natural gas.
- Uranium.
- Copper.
- Gold.

## Fundamentals

- Revenue.
- EBITDA.
- EPS.
- FCF.
- Capex.
- Debt.
- Valuation metrics.

## Prices

- Daily OHLCV.
- Total return series if available.

Minimum document fields:

```text
document_id
source
source_id
title
document_type
company_id
published_at
available_at
raw_path
content_hash
ingested_at
```

`available_at` is mandatory. Without it, the document cannot enter discovery.

---

# 7. Ontology

## Node Types

### Company

Examples:

- Cameco.
- Hydro One.
- RBC.

Required fields:

```text
entity_id
name
canonical_name
ticker
exchange
sector
country
first_seen_at
```

### EconomicConcept

Examples:

- Datacenter.
- Electricity Demand.
- Housing.
- Mortgage.
- Grid Investment.

### Commodity

Examples:

- Uranium.
- Oil.
- Copper.

### MacroIndicator

Examples:

- Fed Funds Rate.
- BoC Rate.
- CPI.
- GDP.

### Event

Examples:

- Rate Cut.
- Capex Increase.
- Production Outage.

### Geography

Examples:

- Ontario.
- Canada.
- Alberta.

### Document

Examples:

- Transcript.
- Filing.
- News.

## Edge Types

- `mentioned_in`
- `co_occurs_with`
- `exposed_to`
- `sensitive_to`
- `causes`
- `benefits`
- `hurts`
- `located_in`

Structural interpretation:

- Structural edges for community discovery: `causes`, `benefits`, `hurts`, `exposed_to`, `sensitive_to`.
- Evidence edges: `mentioned_in`.
- Optional context edges: `co_occurs_with` and `located_in` are non-structural unless explicitly projected into node features.

Minimum edge fields:

```text
edge_id
source_entity_id
target_entity_id
edge_type
confidence
extraction_method
evidence_chunk_ids
first_seen_at
last_seen_at
as_of_date
```

Method constraints:

- `extraction_method=document_stated`: explicit textual claim evidence required in `evidence_chunk_ids` (minimum one chunk).
- `extraction_method=llm_inferred`: relationship inferred by model; must include evidence rationale and confidence.
- `extraction_method=metadata_inferred`: deterministic metadata-based signal; must carry `source_record_id`.

Default exposure policy:

- Exposure and validation pipelines consume `document_stated` edges by default.
- Weak signals (`llm_inferred`, `metadata_inferred`) are excluded unless explicitly enabled by config flag `include_weak_signals`.

---

# 8. Artifact Design

Every run must write artifacts to:

```text
data/runs/<run_id>/
```

Required artifacts:

```text
run_manifest.json
raw_documents.parquet
documents.parquet
document_cleaning_log.parquet
chunks.parquet
entities.parquet
entity_aliases.parquet
edges.parquet
edge_explanations.parquet
graph.json
communities.json
theme_snapshots.json
theme_lineage.json
theme_metrics.parquet
company_theme_exposure.parquet
market_prices.parquet
fundamentals.parquet
portfolio_baskets.parquet
validation.csv
report.md
```

`theme_lineage.json` is required as a schema-valid artifact. A single-as-of demo may write an empty lineage list with `lineage_mode="single_snapshot"`.

`fundamentals.parquet` is required for schema consistency but may be empty when fundamentals validation is disabled in config.

Run vs sweep model:

- `run_manifest.json` is one `(run_id, as_of_date)` snapshot.
- Multi-period backtests are modeled as `sweep` execution that contains ordered child runs.
- Sweep metadata is written as `sweep_manifest.json` with:
  - `sweep_id`
  - `run_ids`
  - `as_of_dates`
  - `window_start`
  - `window_end`
  - `status` (`running`, `frozen`, `blocked`, `failed`)
- Child run manifests may include:
  - `sweep_parent_id` (optional, nullable)
  - `sweep_position` (optional integer)

`run_manifest.json` must contain:

```json
{
  "run_id": "run_YYYYMMDD_HHMMSS",
  "as_of_date": "2024-06-30",
  "universe_config": "configs/universe.example.yml",
  "pipeline_config": "configs/pipeline.example.yml",
  "validation_config": "configs/validation.example.yml",
  "created_at": "...",
  "code_version": "...",
  "input_hash": "..."
}
```

---

# 9. Agent Framework

Agents are work roles, not autonomous black boxes. Their outputs must be artifact-backed.

This section describes logical roles. Some roles share one agent file (see section 25 for the canonical file list):

- Entity Resolution (9.5) is a sub-stage of `extraction_agent.md`; it has no separate file.
- Exposure (9.7) is owned by `graph_theme_agent.md` for computation and handed to `validation_agent.md`; it has no separate file.
- `data_architect_agent.md` and `data_engineering_agent.md` are cross-cutting roles (schema and pipeline plumbing) that support all data-impacting stages and are not tied to one numbered stage below.
- `frontend_report_agent.md` covers the Report role (9.9) plus the dashboard pages in section 24.

## 9.1 Orchestrator

Owns the run plan, task ordering, acceptance checks, and cross-agent handoff.

Inputs:

- User goal.
- Current run manifest.
- Config files.

Outputs:

- Updated run plan.
- Acceptance checklist.
- Handoff notes.

## 9.2 Data Ingestion Agent

Loads raw source files and source manifests into point-in-time raw document records.

Outputs:

- `raw_documents.parquet`
- ingestion warnings

Hard rule:

- Reject or quarantine raw documents without `available_at`.
- Do not clean text or chunk documents in this agent.

## 9.3 Data Cleaning Agent

Cleans raw unstructured documents into extraction-ready documents and chunks.

Outputs:

- `documents.parquet`
- `document_cleaning_log.parquet`
- `chunks.parquet`

Hard rule:

- Cleaning must be deterministic and auditable.
- Do not summarize, translate, or rewrite source meaning.

## 9.4 Extraction Agent

Extracts candidate entities and relationships.

Input:

- cleaned `chunks.parquet`

Outputs:

- `entities.parquet`
- `edges.parquet`
- `edge_explanations.parquet`

Hard rule:

- Every non-trivial edge needs evidence chunk ids.
- Extraction must not read raw uncleaned files.

## 9.5 Entity Resolution Agent

Canonicalizes aliases.

Process:

1. Alias lookup.
2. Embedding similarity.
3. LLM verification.
4. Optional human review.

Outputs:

- `entity_aliases.parquet`
- updated `entities.parquet`

## 9.6 Graph Theme Agent

Builds `Graph(t)` and discovers communities.

Outputs:

- `graph.json`
- `communities.json`
- `theme_snapshots.json`
- `theme_lineage.json`
- `theme_metrics.parquet`

Hard rule:

- Theme ids are produced by graph/community logic. LLM names are metadata only.

## 9.7 Exposure Agent

Computes company-theme exposure.

Outputs:

- `company_theme_exposure.parquet`

Exposure inputs:

- Graph distance.
- Edge confidence.
- Evidence count.
- Recency.
- Company node centrality.

## 9.8 Validation Agent

Measures future outcomes.

Outputs:

- `validation.csv`

Hard rule:

- Validation data cannot be read before discovery artifacts are frozen.

## 9.9 Report Agent

Writes research notes from existing artifacts only.

Outputs:

- `report.md`

Hard rule:

- No unsupported prediction claims.
- Every key claim links to graph, exposure, validation, or evidence artifacts.

---

# 10. Entity Resolution

Purpose:

Merge aliases into canonical entities.

Example:

```text
AI data center
AI datacenter
Hyperscale compute

-> AI Datacenter
```

Resolution process:

1. Normalize case, punctuation, suffixes, and common abbreviations.
2. Match known aliases from `entity_aliases`.
3. Use embedding similarity for candidates.
4. Use LLM verification only for ambiguous pairs.
5. Mark low-confidence cases for human review.

Required output fields:

```text
alias
canonical_entity_id
canonical_name
confidence
method
review_status
```

---

# 11. Community Discovery

Themes are not predefined.

Discovery algorithms:

- Louvain for simple MVP.
- Leiden for better community quality.

Structural projection rule:

- Community discovery input graph MUST be entity-only.
- Document nodes and `mentioned_in` edges are allowed in `graph.json` for evidence traceability, but they are excluded from Louvain/Leiden inputs.
- Community discovery input edges must be filtered to structural edge types (`causes`, `benefits`, `hurts`, `exposed_to`, `sensitive_to`) and non-weak source (`document_stated` or explicit config-approved `metadata_inferred`).
- `communities.json` must record `edge_projection_mode` so lineage and audit can reconstruct excluded nodes/edges.

Output:

```text
Community_17
Community_42
Community_88
```

Community IDs are the research objects. Theme names are metadata only.

Community fields:

```text
community_id
as_of_date
node_ids
edge_ids
size
density
top_entities
top_companies
theme_name
theme_summary
naming_model
```

---

# 12. Theme Lifecycle

Theme family:

- Long-term lineage.

Theme snapshot:

- Point-in-time manifestation.

States:

- Emerging.
- Expanding.
- Mature.
- Crowded.
- Declining.
- Dormant.
- Revived.

Relationships:

- `evolves_into`
- `splits_into`
- `merges_into`
- `renamed_as`
- `revived_from`

MVP rule:

- Single `as_of_date` demo only needs snapshots.
- Multi-month demo should add lineage by matching communities across adjacent months.

---

# 13. Node Explanation Framework

Every node should answer:

1. What is it?
2. Why does it exist in the graph?
3. Why does it matter economically?

Node profile:

- Definition.
- First seen date.
- Importance.
- Evidence count.
- Related entities.
- Economic role.

---

# 14. Edge Explanation Framework

Every edge requires:

- Evidence.
- Confidence.
- First seen date.
- Last updated date.

Example:

```text
Datacenter
causes
Electricity Demand
```

Supported by:

- Utility transcript.
- Grid forecast.
- Corporate disclosure.

MVP rule:

- If an edge has no evidence chunk, it must not appear in the report.

---

# 15. Temporal Graph Design

`Graph(t)` uses only:

- Documents with `available_at <= t`.
- Events with `available_at <= t`.
- Entities first observed by `t`.
- Edges supported by evidence available by `t`.

Historical graphs are immutable.

MVP implementation:

- Single `as_of_date` first.
- Then monthly snapshots.

---

# 16. Leakage Prevention

## Strong Leakage: Must Be Zero

- Future documents.
- Future fundamentals.
- Future returns.
- Future communities.
- Future revised labels.

## Weak Leakage: Document and Monitor

- Ontology design.
- Entity normalization.
- Theme naming.
- Manual data curation.

Leakage test:

- Build discovery artifacts with `as_of_date`.
- Only after artifacts are frozen, run validation.
- Freeze gate:
  - `data/runs/<run_id>/discovery/` and `data/runs/<run_id>/validation/` must be physically separated.
  - `validation_agent` must verify `run_manifest.json` artifact hashes before reading validation inputs.
- pytest requirements for CI gates:
  - Every evidence chunk entering `Graph(t)` has `available_at <= as_of_date`.
  - Every validation row read (`market_prices`, `fundamentals`) has `as_of_date + holding_period <= row_date`.

---

# 17. LLM Governance

## Discovery Layer

No LLM reasoning for theme discovery.

Allowed:

- Structured extraction from text.
- Entity disambiguation assistance.

Not allowed:

- Inventing themes.
- Ranking themes as investments.
- Generating backtest features from future data.

## Interpretation Layer

LLM allowed for:

- Theme naming.
- Summaries.
- Node explanations.
- Edge explanations from evidence.
- Research notes.

LLM not allowed for:

- Discovery.
- Feature generation.
- Backtest logic.
- Validation conclusions without artifacts.

---

# 18. Discovery vs Validation

## Discovery Dataset

Contains:

- Documents.
- Chunks.
- Entities.
- Edges.
- Communities.
- Theme metrics computed from discovery data.

Cannot access:

- Future returns.
- Future fundamentals.
- Future revisions.

## Validation Dataset

Contains:

- Future returns.
- Future revisions.
- Future fundamentals.

Used only after discovery artifacts are frozen.

---

# 19. Fundamental Validation Layer

Theme:

```text
Theme -> Fundamental Change -> Market Outcome
```

Metrics:

- Revenue growth.
- EBITDA growth.
- Margin change.
- EPS revision.
- Capex growth.
- FCF growth.

MVP validation:

- 1M and 3M forward return.
- Optional revenue growth or EPS revision if data is available.

---

# 20. Theme Metrics

Metrics are classified by how many snapshots they require. A single `as_of_date` run must only compute single-snapshot metrics. Temporal metrics require lineage across at least two adjacent snapshots and must be skipped otherwise.

Single-snapshot metrics (valid with one `as_of_date`):

- Strength: weighted evidence and edge count.
- Cohesion: graph density or modularity-related score.
- Saturation: breadth of coverage (crowding proxies only if available).

Temporal metrics (require >= 2 snapshots; skip in single-snapshot MVP):

- Momentum: change in mentions or edge weight versus prior window.
- Birth Score: new high-confidence entities and edges versus prior window.
- Novelty: share of new entities/edges versus prior window.
- Acceleration: change in momentum across windows.

Config gate:

- A `metrics.require_lineage` flag controls temporal metrics. When `metrics.require_lineage=false` (lineage absent, `lineage_mode="single_snapshot"`), temporal metrics are omitted rather than emitted as degenerate values.
- If a caller requests temporal metrics while `lineage_mode="single_snapshot"`, return a typed validation error and suggest walk-forward execution.

Temporal metric provenance fields:

- `as_of_date`
- `lineage_window_start` (null in single-snapshot mode)
- `lineage_mode` (`single_snapshot` | `temporal`)
- `lineage_gap_count`

`theme_metrics.parquet` validation:

- `single_snapshot` mode: contains only single-snapshot fields (`strength`, `cohesion`, `coverage`) plus `metric_mode` and `lineage_mode`.
- `lineage` mode: may additionally contain `momentum`, `birth_score`, `novelty`, `acceleration`.
- `theme_lineage.json` is required for lineage runs and may be empty (`[]`) for single-snapshot runs.

Later metrics:

- Macro linkage.
- Commodity linkage.
- Fundamental confirmation.

---

# 21. Winning Zone Framework

Goal:

Identify the most profitable phase of a narrative.

Winning zone:

```text
High Birth Score
+ High Momentum
+ High Novelty
+ Low Saturation
+ Fundamental Confirmation
```

MVP note:

- This is a research hypothesis, not an investment claim until validated.
Winning Zone depends on Birth Score, Momentum, and Novelty, which are temporal metrics. It is therefore not computable in a single-snapshot MVP and is only produced once lineage exists across >= 2 snapshots. See section 20.

For `lineage_mode="single_snapshot"`, `winning_zone.json` is still a schema-valid artifact with:
- `status: "insufficient_lineage"`
- `as_of_date`
- `ready_metrics: ["strength","cohesion","coverage"]`
- `missing_metrics: ["birth_score","momentum","novelty","acceleration","winning_zone_score"]`

---

# 22. Backtesting Framework

Target framework:

Monthly walk-forward requires a minimum of 3 monthly points for any inferential claim.

Single-snapshot behavior:

- `backtest_status` in run artifacts must be `disabled_not_enough_snapshots`.
- Validation outputs should state: "backtesting requires temporal panel and is not meaningful for single-snapshot inputs."

Required minimum coverage:
- 1M metrics require at least one month of market coverage after `as_of_date` for each snappoint.
- 3M metrics require at least three months of market coverage after `as_of_date` for each snappoint.
- Backtest execution should fail-fast with a typed validation error if any snappoint lacks required coverage and list exact missing date ranges.

At time `t`:

1. Build `Graph(t)`.
2. Discover `Communities(t)`.
3. Compute `Theme Metrics(t)`.
4. Rank communities.
5. Build exposure baskets.
6. Evaluate forward returns.

MVP holding periods:

- 1M.
- 3M.

Later holding periods:

- 6M.
- 12M.

Benchmarks:

- Equal-weight universe.
- Sector equal-weight if sector data exists.
- Commodity momentum if relevant.
- Random communities as a sanity check.

---

# 23. Validation Suite

Compare:

- Birth Score.
- Momentum.
- Novelty.
- Saturation.
- Exposure score.

Against:

- Random communities.
- Sector momentum.
- Commodity momentum.
- Equal-weight universe.

Validation output must show:

- Theme id.
- Theme name.
- Top exposed companies.
- Forward window.
- Theme basket return.
- Benchmark return.
- Excess return.
- Sample size.
- Caveats.

---

# 24. Theme Radar Dashboard

Sections:

1. Emerging narratives.
2. Accelerating narratives.
3. Theme splits.
4. Theme merges.
5. Second-order beneficiaries.
6. Community registry.
7. Evidence drilldown.
8. Validation results.

MVP pages:

- Data Import.
- Graph Explorer.
- Theme Radar.
- Theme Detail.
- Validation.
- Report.

---

# 25. Codex / Claude Shared Agents

The agents under `agents/` are shared role specs. They can be used by Codex subagents or Claude projects.

Required agents:

1. `orchestrator.md`
2. `data_architect_agent.md`
3. `data_engineering_agent.md`
4. `data_ingestion_agent.md`
5. `data_cleaning_agent.md`
6. `extraction_agent.md`
7. `graph_theme_agent.md`
8. `validation_agent.md`
9. `frontend_report_agent.md`

Rule:

- Agents should read this document first.
- Data-impacting work should involve `data_architect_agent.md` and `data_engineering_agent.md`.
- Agents should write outputs to artifacts, not only chat summaries.
- Agents should avoid changing scope without updating configs and run manifest.

---

# 26. Codex / Claude Shared Skills

The skills under `skills/` define repeatable workflows.

Required skills:

1. `point_in_time_data.md`
2. `unstructured_data_cleaning.md`
3. `entity_relation_extraction.md`
4. `temporal_graph_discovery.md`
5. `validation_backtest.md`
6. `evidence_report_generation.md`
7. `backend_api_implementation.md`
8. `pipeline_artifact_implementation.md`
9. `frontend_workflow_implementation.md`
10. `test_quality_gate.md`
11. `maintainable_code_implementation.md`

Rule:

- Skills must be tool-agnostic.
- Skills must state input artifacts, output artifacts, acceptance checks, and common failure modes.
- Code-writing skills must define service boundaries, artifact contracts, tests, and verification expectations.
- Implementation code should follow `docs/code_style_standards.md`: use the CS136-inspired design recipe, encapsulate repeated logic, avoid hardcoded research values, use config/variables, and maintain useful comments.

---

# 27. Implementation Milestones

## Milestone 1: Workspace and MiroFish-Inspired Workflow Shell

Deliver:

- Folder structure.
- Config examples.
- MiroFish route mapping documented with this project's endpoint names.
- Empty workflow pages.
- Run creation.

Acceptance:

- A local run directory is created with `run_manifest.json`.

## Milestone 2: Data Import and Chunking

Deliver:

- PDF/MD/TXT import.
- `available_at` validation.
- `raw_documents.parquet`.
- `/api/data/clean`.
- `documents.parquet`.
- `document_cleaning_log.parquet`.
- `/api/data/chunk`.
- `chunks.parquet`.

Acceptance:

- Documents after `as_of_date` are excluded from discovery.
- Extraction reads cleaned chunks, not raw files.

## Milestone 3: Extraction and Evidence

Deliver:

- `/api/extraction/run`.
- Entities.
- Edges.
- Edge explanations.
- Evidence drilldown.

Acceptance:

- Sampled edges have supporting chunks.

## Milestone 4: Graph and Theme Discovery

Deliver:

- `graph.json`.
- `communities.json`.
- `theme_snapshots.json`.
- `theme_lineage.json`.
- Theme names and summaries.

Acceptance:

- Themes are produced by community detection, not manual labels.

## Milestone 5: Exposure and Freeze

Deliver:

- `/api/exposure/compute`.
- `company_theme_exposure.parquet`.
- `/api/discovery/freeze`.
- `run_manifest.json` with `discovery_frozen=true`.

Acceptance:

- Exposure is computed before validation reads future returns or fundamentals.

## Milestone 6: Validation

Deliver:

- `market_prices.parquet`.
- `fundamentals.parquet`.
- `portfolio_baskets.parquet`.
- `validation.csv`.
- 1M/3M validation.

Acceptance:

- Validation runs only after discovery artifacts are frozen.
- Validation can reproduce basket constituents, weights, data version, and benchmark.

## Milestone 7: Report and Demo

Deliver:

- `report.md`.
- Demo data.
- README runbook.

Acceptance:

- A new user can run the demo locally and reproduce the artifacts.

---

# 28. Acceptance Checklist

A demo is acceptable only if:

1. It has a fixed `as_of_date`.
2. It only uses documents with `available_at <= as_of_date`.
3. It writes all required artifacts.
4. Entities and edges are inspectable.
5. Important edges link to evidence chunks.
6. Communities are discovered by graph algorithm.
7. LLM theme names are metadata only.
8. Company exposure can be traced back to graph evidence.
9. Validation uses future data only after discovery artifacts are frozen.
10. Report claims link to artifacts.
11. The run can be reproduced.

---

# 29. Future Extensions

- Alternative data.
- Power demand.
- Rail traffic.
- Port throughput.
- Datacenter capacity.
- Estimate revision graphs.
- Global markets.
- PostgreSQL/pgvector migration.
- Multi-user review workflow.
- Production scheduler.

---

# 30. Research North Star

The platform studies:

```text
Information Structure
-> Narrative Formation
-> Theme Evolution
-> Fundamental Consequences
-> Market Outcomes
```

using only information available at the time.

The final system is a:

> Time-Aware Economic Narrative Discovery Engine.
