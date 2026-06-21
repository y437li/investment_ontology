# Input / Output Contracts

This document defines the canonical input and output formats for the MVP pipeline. `docs/data_schema.md` defines the layered data model; this file defines the exact artifact and API contracts. Every agent, skill, API, and code module should preserve these contracts unless `theme_discovery_engine_v1.md` is updated first.

## 1. Contract Principles

- Every stage declares input artifacts and output artifacts.
- Every table uses lowercase snake case columns.
- Every artifact belongs under `data/runs/<run_id>/`.
- Source inputs are external to run outputs and must be under `data/inputs/...` (non-structured corpus). Runs read source files via `source_manifest.csv` pointers only.
- Discovery artifacts are written under `data/runs/<run_id>/discovery/`.
- Validation artifacts are written under `data/runs/<run_id>/validation/`.
- Every run has exactly one `run_manifest.json`; each walk-forward sweep has one `sweep_manifest.json`.
- Discovery artifacts must be frozen before validation reads future returns or fundamentals.
- Missing required fields should fail early with a clear stage error.
- MiroFish implementation patterns may be reused, but MiroFish simulation route names and data models do not override these contracts.
- Raw unstructured inputs must pass through cleaned unstructured artifacts before structured extraction starts.

## 2. Run Manifest

Path:

```text
data/runs/<run_id>/run_manifest.json
```

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "run_name": "demo_ai_infra_asof_2024_06_30",
  "as_of_date": "2024-06-30",
  "created_at": "2026-06-21T12:00:00Z",
  "code_version": "local",
  "universe_config": "configs/universe.example.yml",
  "pipeline_config": "configs/pipeline.example.yml",
  "validation_config": "configs/validation.example.yml",
  "model_config": {
    "extraction_model": "gpt-4.1-mini",
    "theme_naming_model": "gpt-4.1-mini",
    "embedding_model": "text-embedding-3-small",
    "provider": "openai"
  },
  "input_hash": "sha256:...",
  "model_config_hash": "sha256:...",
  "sweep_id": null,
  "sweep_parent_id": null,
  "validation_mode": "single_snapshot",
  "sweep_position": null,
  "discovery_artifact_hashes": {
    "raw_documents.parquet": "sha256:...",
    "documents.parquet": "sha256:...",
    "chunks.parquet": "sha256:...",
    "entity_aliases.parquet": "sha256:...",
    "edges.parquet": "sha256:...",
    "graph.json": "sha256:..."
  },
  "discovery_frozen": false
}
```

Required fields:

- `schema_version`
- `run_id`
- `as_of_date`
- `created_at`
- `universe_config`
- `pipeline_config`
- `input_hash`
- `model_config` (optional, stored in manifest as provided)
- `model_config_hash` (optional, present when `model_config` is provided)
- `discovery_artifact_hashes` (required when `discovery_frozen=true`)
- `validation_mode`
- `discovery_frozen`
- `sweep_id` (nullable for single-snapshot runs)
- `sweep_parent_id` (nullable)
- `sweep_position` (nullable)

## 2a. Sweep Manifest (walk-forward)

Path:

```text
data/runs/<sweep_id>/sweep_manifest.json
```

Run a sweep when multiple `as_of_date` snapshots share one validation objective.

Format:

```json
{
  "schema_version": "1.0",
  "sweep_id": "sweep_202406",
  "sweep_name": "demo_ai_infra_3m_walkforward",
  "created_at": "2026-06-21T12:30:00Z",
  "validation_config": "configs/validation.example.yml",
  "as_of_dates": ["2024-03-31", "2024-06-30", "2024-09-30"],
  "child_runs": [
    {
      "run_id": "run_20240331_120000",
      "as_of_date": "2024-03-31",
      "run_manifest_path": "data/runs/run_20240331_120000/run_manifest.json"
    },
    {
      "run_id": "run_20240630_120000",
      "as_of_date": "2024-06-30",
      "run_manifest_path": "data/runs/run_20240630_120000/run_manifest.json"
    },
    {
      "run_id": "run_20240930_120000",
      "as_of_date": "2024-09-30",
      "run_manifest_path": "data/runs/run_20240930_120000/run_manifest.json"
    }
  ]
}
```

Required fields:

- `schema_version`
- `sweep_id`
- `created_at`
- `validation_config`
- `as_of_dates`
- `child_runs`
- `validation_mode` (defaults to `"walk_forward"`)

Each child run remains a single-as_of run and may reuse run-level frozen artifacts.

## 3. Stage Contract Summary

| Stage | Inputs | Outputs |
|---|---|---|
| Collect Sources | source collection spec (CSV with `source` metadata and `source_file` / `source_url`) | `data/inputs/documents` raw corpus + `source_manifest.csv` |
| Create Run | configs | `run_manifest.json` |
| Import Raw Documents | raw files, `source_manifest.csv` | `discovery/raw_documents.parquet` |
| Clean Documents | `discovery/raw_documents.parquet` | `discovery/documents.parquet`, `discovery/document_cleaning_log.parquet` |
| Chunk Documents | `discovery/documents.parquet` | `discovery/chunks.parquet` |
| Extract Entities | `discovery/chunks.parquet` | `discovery/entities.parquet`, `discovery/entity_aliases.parquet`, `discovery/entity_aliases_global.parquet` (optional) |
| Extract Edges | `discovery/chunks.parquet`, `discovery/entities.parquet` | `discovery/edges.parquet`, `discovery/edge_explanations.parquet` |
| Build Graph | `discovery/entities.parquet`, `discovery/edges.parquet` | `discovery/graph.json` |
| Discover Themes | `discovery/graph.json` | `discovery/communities.json`, `discovery/theme_snapshots.json`, `discovery/theme_lineage.json`, `discovery/theme_metrics.parquet` |
| Compute Document-Theme Affinity | `discovery/communities.json`, `discovery/graph.json`, `discovery/entities.parquet`, `discovery/edges.parquet`, `discovery/chunks.parquet`, `discovery/documents.parquet` | `discovery/document_theme_affinity.parquet` |
| Build News Package | `discovery/documents.parquet`, `discovery/chunks.parquet`, `discovery/document_theme_affinity.parquet` (optional) | `discovery/news_report_package.json` |
| Compute Exposure | `discovery/communities.json`, `discovery/graph.json`, `discovery/entities.parquet`, `discovery/edges.parquet` | `discovery/company_theme_exposure.parquet` |
| Freeze Discovery | all discovery artifacts | updated `run_manifest.json` |
| Load Market Data | market files or adapter output | `validation/market_prices.parquet` |
| Load Fundamentals | fundamentals files or adapter output | `validation/fundamentals.parquet` |
| Validate | frozen discovery artifacts, `discovery/company_theme_exposure.parquet`, `validation/market_prices.parquet`, optional `validation/fundamentals.parquet` | `validation/portfolio_baskets.parquet`, `validation/validation.csv` |
| Report | all artifacts | `report.md` |

### `POST /api/data/collect`

Collect raw sources into `data/inputs/documents` and regenerate `source_manifest.csv`.

Request:

```json
{
  "source_spec_path": "data/inputs/document_collection_spec.csv",
  "documents_dir": "data/inputs/documents",
  "source_manifest_path": "data/inputs/documents/source_manifest.csv",
  "append_manifest": false
}
```

Response:

```json
{
  "success": true,
  "sources_seen": 12,
  "sources_collected": 10,
  "sources_quarantined": 2,
  "source_manifest_path": "data/inputs/documents/source_manifest.csv",
  "report_path": "data/inputs/documents/data_collection_report.json",
  "quarantined": 2,
  "quarantine_reasons": ["row 4: missing required column value: source_id"]
}
```

## 4. Input `source_manifest.csv`

Path:

```text
data/inputs/documents/source_manifest.csv
```

One row per raw source file.

Required columns:

```text
source: string
source_id: string
raw_path: string
title: string
document_type: string
company_id: string | null
published_at: date | timestamp
available_at: date | timestamp
language: string | null
source_url: string | null
license: string | null
confidentiality: string | null
source_vintage: string | null
notes: string | null
```

Rules:

- `raw_path` must be relative to the document input root.
- `available_at` is mandatory; missing values must be rejected or quarantined.
- `source_vintage` must be preserved if available for replay and audit.
- This manifest is local input data and should not be committed unless it is a tiny synthetic fixture.

## 5. `raw_documents.parquet`

One row per raw source document after ingestion and text extraction.

Required columns:

```text
schema_version: string
run_id: string
raw_document_id: string
source: string
source_id: string
raw_path: string
raw_file_type: string
title: string
document_type: string
company_id: string | null
published_at: date | timestamp
available_at: date | timestamp
language: string | null
source_url: string | null
raw_content_hash: string
raw_byte_size: int | null
extracted_text_path: string | null
extraction_method: string
extraction_status: string
extraction_error: string | null
ingested_at: timestamp
included_in_discovery: bool
exclusion_reason: string | null
```

Rules:

- Raw files must not be overwritten.
- `raw_document_id` must be stable for the same `source`, `source_id`, and `raw_content_hash`.
- `included_in_discovery` must be false when `available_at > as_of_date`.
- Extraction failures must be explicit in `extraction_status` and `extraction_error`.

## 6. `documents.parquet`

One row per cleaned document ready for chunking.

Required columns:

```text
schema_version: string
run_id: string
document_id: string
raw_document_id: string
source: string
source_id: string
title: string
document_type: string
company_id: string | null
published_at: date | timestamp
available_at: date | timestamp
language: string | null
raw_path: string
clean_text_path: string
content_hash: string
raw_content_hash: string
clean_content_hash: string
cleaning_status: string
cleaning_version: string
cleaning_agent: string
ingested_at: timestamp
cleaned_at: timestamp
included_in_discovery: bool
exclusion_reason: string | null
```

Rules:

- `available_at` is mandatory.
- `included_in_discovery` must be false when `available_at > as_of_date`.
- `raw_document_id` links back to `raw_documents.parquet`.
- `content_hash` should equal `clean_content_hash` for the canonical cleaned text.
- Cleaning must not summarize, translate, or rewrite source meaning.

## 7. `document_cleaning_log.parquet`

One row per material cleaning action or quarantine decision.

Required columns:

```text
schema_version: string
run_id: string
raw_document_id: string
document_id: string | null
cleaning_step: string
action_type: string
rule_id: string
before_hash: string | null
after_hash: string | null
char_count_before: int | null
char_count_after: int | null
status: string
warning_code: string | null
warning_message: string | null
cleaned_by: string
created_at: timestamp
```

Rules:

- Quarantined records must have `status` and `warning_message`.
- Deterministic cleaning rules should have stable `rule_id` values.
- This artifact is required even when no material changes were made.

## 8. `chunks.parquet`

One row per text chunk.

Required columns:

```text
schema_version: string
run_id: string
chunk_id: string
document_id: string
raw_document_id: string
chunk_index: int
text: string
token_count: int | null
start_char: int | null
end_char: int | null
page_start: int | null
page_end: int | null
section_title: string | null
available_at: date | timestamp
content_hash: string
cleaning_version: string
```

Rules:

- `chunk_id` must be stable for the same document hash and chunking config.
- Chunks inherit `available_at` from documents.
- `text` must come from cleaned document text, not raw unnormalized files.

## 9. `entities.parquet`

One row per canonical or candidate entity.

Required columns:

```text
schema_version: string
entity_id: string
entity_type: string
name: string
canonical_name: string
ticker: string | null
exchange: string | null
sector: string | null
country: string | null
first_seen_at: date | timestamp
source_chunk_ids: list[string]
confidence: float
extraction_method: string
review_status: string
```

Allowed `entity_type` values:

- `Company`
- `EconomicConcept`
- `Commodity`
- `MacroIndicator`
- `Event`
- `Geography`
- `Document`

## 10. `entity_aliases.parquet`

One row per alias mapping.

Required columns:

```text
schema_version: string
alias: string
canonical_entity_id: string
canonical_name: string
as_of_date: date
confidence: float
method: string
review_status: string
alias_scope: string
source_record_ids: list[string]
created_at: timestamp
```

`alias_scope` values:

- `point_in_time` (run-scope alias table)
- `global` (cross-run diagnostic table)

Rules:

- `alias_scope=point_in_time` rows are for `discovery/entity_aliases.parquet` and require `as_of_date`.
- `alias_scope=global` rows are for `discovery/entity_aliases_global.parquet` and must not drive discovery joins.

## 10a. `entity_aliases_global.parquet`

Optional diagnostics artifact for non-temporal alias inspection.

Required columns:

```text
schema_version: string
alias: string
canonical_entity_id: string
canonical_name: string
confidence: float
method: string
review_status: string
source_record_ids: list[string]
created_at: timestamp
```

## 11. `edges.parquet`

One row per relationship.

Required columns:

```text
schema_version: string
edge_id: string
source_entity_id: string
target_entity_id: string
edge_type: string
confidence: float
evidence_chunk_ids: list[string]
first_seen_at: date | timestamp
last_seen_at: date | timestamp
as_of_date: date
extraction_method: string
review_status: string
```

Allowed `edge_type` values:

- `mentioned_in`
- `co_occurs_with`
- `exposed_to`
- `sensitive_to`
- `causes`
- `benefits`
- `hurts`
- `located_in`

Rules:

- Non-trivial edges must have at least one `evidence_chunk_ids` value.
- `first_seen_at <= as_of_date`.
- `extraction_method` must be one of: `document_stated`, `llm_inferred`, `metadata_inferred`.
- Exposure and community computation include only non-weak edges by default:
  - Include `document_stated` by default.
  - Exclude `llm_inferred` unless `include_weak_signals=true`.
  - Exclude `metadata_inferred` unless explicitly enabled.
- `metadata_inferred` edges must carry `source_record_id` in `explanation` context so audit remains reconstructable.

## 12. `edge_explanations.parquet`

One row per edge explanation.

Required columns:

```text
schema_version: string
edge_id: string
explanation: string
evidence_chunk_ids: list[string]
confidence: float
generated_by: string
created_at: timestamp
```

Rules:

- Explanation must be grounded in evidence chunks.
- Do not include unsupported LLM speculation.

## 13. `graph.json`

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "as_of_date": "2024-06-30",
  "projection": {
    "type": "entity_only",
    "node_types_in_structural_graph": [
      "Company",
      "MacroIndicator",
      "EconomicConcept",
      "Commodity",
      "Event",
      "Geography"
    ],
    "excluded_node_types": ["Document"]
  },
  "structural_edge_types": ["exposed_to", "sensitive_to", "causes", "benefits", "hurts", "located_in"],
  "evidence_edge_types": ["mentioned_in", "co_occurs_with"],
  "nodes": [
    {
      "entity_id": "ent_001",
      "entity_type": "Company",
      "label": "Hydro One",
      "attributes": {}
    }
  ],
  "edges": [
    {
      "edge_id": "edge_001",
      "source_entity_id": "ent_001",
      "target_entity_id": "ent_002",
      "edge_type": "exposed_to",
      "weight": 0.72,
      "evidence_chunk_ids": ["chunk_001"],
      "extraction_method": "document_stated"
    }
  ],
  "community_input_edges": ["edge_001"]
}
```

Rules:

- Structural community detection uses only `community_input_edges`.
- `community_input_edges` must only reference edges with non-Document endpoints and `edge_type in structural_edge_types`.
- `evidence_edge_types` are allowed for traceability and reporting, but are excluded from structure-based clustering by default.
- `run_id`, `as_of_date`, and `graph.json` content are immutable after freeze.

## 14. `communities.json`

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "as_of_date": "2024-06-30",
  "algorithm": "leiden",
  "communities": [
    {
      "community_id": "community_017",
      "node_ids": ["ent_001", "ent_002"],
      "edge_ids": ["edge_001"],
      "size": 12,
      "density": 0.34,
      "top_entities": ["Datacenter", "Electricity Demand"],
      "top_companies": ["Hydro One"],
      "theme_name": "Datacenter Power Demand",
      "theme_summary": "Evidence-backed summary.",
      "naming_model": "gpt-4.1-mini"
    }
  ]
}
```

Rule:

- `theme_name` is metadata only. The community is the research object.

## 15. `theme_snapshots.json`

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "as_of_date": "2024-06-30",
  "snapshots": [
    {
      "theme_snapshot_id": "theme_20240630_017",
      "community_id": "community_017",
      "theme_family_id": null,
      "state": "Emerging",
      "theme_name": "Datacenter Power Demand",
      "summary": "Evidence-backed summary.",
      "evidence_edge_ids": ["edge_001"]
    }
  ]
}
```

Allowed `state` values:

- `Emerging`
- `Expanding`
- `Mature`
- `Crowded`
- `Declining`
- `Dormant`
- `Revived`

## 16. `theme_lineage.json`

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "as_of_date": "2024-06-30",
  "lineage_mode": "single_snapshot",
  "lineages": [
    {
      "theme_family_id": "theme_family_004",
      "current_theme_snapshot_id": "theme_20240630_017",
      "prior_theme_snapshot_ids": ["theme_20240531_011"],
      "lifecycle_event": "expanding",
      "confidence": 0.81,
      "method": "community_overlap_v1"
    }
  ]
}
```

Rules:

- A single-as-of demo must still write this artifact with an empty `lineages` list and `lineage_mode="single_snapshot"`.
- Multi-snapshot runs must make split, merge, revive, and decline events explicit.
- Lineage is derived from graph/community continuity, not from future returns.

## 17. `theme_metrics.parquet`

One row per theme/community.

Required columns:

```text
schema_version: string
theme_snapshot_id: string
community_id: string
as_of_date: date
strength: float
momentum: float | null
birth_score: float | null
cohesion: float
novelty: float | null
saturation: float | null
macro_linkage: float | null
commodity_linkage: float | null
```

## 18. `company_theme_exposure.parquet`

One row per company-theme pair.

Required columns:

```text
schema_version: string
as_of_date: date
company_id: string
ticker: string | null
theme_snapshot_id: string
community_id: string
exposure_score: float
graph_distance: float | null
edge_confidence_sum: float
evidence_count: int
top_evidence_chunk_ids: list[string]
calculation_method: string
```

Rules:

- Exposure must be explainable from graph and evidence.
- Do not output top companies without `exposure_score`.
- Exposure must be computed and frozen before validation reads future market or fundamental outcomes.

## 19. `document_theme_affinity.parquet`

One row per document-community affinity pair.

Required columns:

```text
schema_version: string
run_id: string
as_of_date: date
document_id: string
raw_document_id: string
document_title: string
company_id: string
community_id: string
theme_snapshot_id: string
theme_name: string
document_community_rank: int
evidence_chunk_count: int
evidence_chunk_ids: list[string]
entity_signal_count: int
edge_signal_count: int
raw_score: float
normalized_affinity: float
method: string
created_at: timestamp
```

Rules:

- One document can map to multiple rows (multi-theme support).
- `document_community_rank` is 1-based within a document.
- `normalized_affinity` should be normalized across one document’s emitted pairs.
- `evidence_chunk_ids` must reference `discovery/chunks.parquet`.

## 20. `market_prices.parquet`

One row per company and price date needed for validation.

Required columns:

```text
schema_version: string
run_id: string
as_of_date: date
company_id: string
ticker: string | null
price_date: date
close: float
adjusted_close: float | null
currency: string
source: string
source_id: string | null
available_at: date | timestamp | null
created_at: timestamp
```

Rules:

- Discovery stages must not read this artifact.
- Validation may include prices after `as_of_date` only after `discovery_frozen=true`.
- Return calculations must state whether `close` or `adjusted_close` is used.

## 20. `fundamentals.parquet`

One row per company, period, and fundamental metric.

Required columns:

```text
schema_version: string
run_id: string
as_of_date: date
company_id: string
ticker: string | null
period_end: date
metric_name: string
metric_value: float | string | null
unit: string | null
currency: string | null
filing_date: date | null
available_at: date | timestamp | null
source: string
source_id: string | null
created_at: timestamp
```

Rules:

- Discovery stages must not read validation-only fundamentals.
- If fundamentals validation is disabled, write a schema-valid empty artifact.
- Fundamental metric names must come from config, not hardcoded strings inside validation code.

## 21. `portfolio_baskets.parquet`

One row per selected company inside a theme validation basket.

Required columns:

```text
schema_version: string
run_id: string
as_of_date: date
basket_id: string
theme_snapshot_id: string
community_id: string
portfolio_method: string
selection_rank: int
company_id: string
ticker: string | null
exposure_score: float
weight: float
inclusion_reason: string
calculation_method: string
created_at: timestamp
```

Rules:

- This artifact must be sufficient to reproduce `validation.csv`.
- Weights must be explicit and should sum to approximately 1.0 per `basket_id`.
- Selection rules must come from `configs/validation.example.yml`.

## 22. `validation.csv`

One row per theme and validation window.

Required columns:

```text
schema_version
run_id
as_of_date
basket_id
theme_snapshot_id
community_id
theme_name
forward_window
portfolio_method
company_count
start_date
end_date
theme_basket_return
benchmark_name
benchmark_return
excess_return
sample_size
market_data_source
caveats
```

Rules:

- Validation can run only after `discovery_frozen=true`.
- Benchmark must be explicit.
- Do not suppress negative results.
- Each row must link to a reproducible `basket_id` in `portfolio_baskets.parquet`.

## 23. `report.md`

Required sections:

```markdown
# Theme Discovery Report

## Run Metadata
## Data Coverage
## Emerging Themes
## Accelerating Themes
## Company Exposure
## Validation Results
## Evidence Notes
## Caveats
```

Rules:

- Every key claim must reference artifacts or evidence ids.
- Reports must distinguish source facts, LLM summaries, and analyst inference.
- Reports must not present automatic investment advice.

## 24. API I/O Contracts

### `POST /api/runs/create`

Input:

```json
{
  "run_name": "demo_ai_infra_asof_2024_06_30",
  "as_of_date": "2024-06-30",
  "universe_config": "configs/universe.example.yml",
  "pipeline_config": "configs/pipeline.example.yml",
  "validation_config": "configs/validation.example.yml",
  "model_config": {
    "extraction_model": "gpt-4.1-mini",
    "theme_naming_model": "gpt-4.1-mini",
    "embedding_model": "text-embedding-3-small",
    "provider": "openai"
  }
}
```

Notes:

- `model_config` is optional. When omitted, the run uses pipeline defaults.
- The manifest stores both `model_config` and its canonical `model_config_hash` so any
  model choice is replayable.

Output:

```json
{
  "success": true,
  "run_id": "run_20240630_120000",
  "manifest_path": "data/runs/run_20240630_120000/run_manifest.json"
}
```

### `POST /api/data/import`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "documents_dir": "data/inputs/documents",
  "source_manifest_path": "data/inputs/documents/source_manifest.csv"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["raw_documents.parquet"],
  "raw_documents": 45,
  "raw_documents_seen": 47,
  "raw_documents_in_discovery": 45,
  "future_excluded": 2,
  "quarantined": 2,
  "quarantine_reasons": [
    "row 1: raw_path missing"
  ]
}
```

### `POST /api/data/clean`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["documents.parquet", "document_cleaning_log.parquet"],
  "included_documents": 42,
  "quarantined_documents": 3
}
```

### `POST /api/data/chunk`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["chunks.parquet"],
  "chunk_count": 840
}
```

### `POST /api/extraction/run`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": [
    "entities.parquet",
    "entity_aliases.parquet",
    "edges.parquet",
    "edge_explanations.parquet"
  ],
  "entity_count": 128,
  "edge_count": 342
}
```

### `POST /api/graph/build`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["graph.json"],
  "node_count": 128,
  "edge_count": 342
}
```

### `POST /api/themes/discover`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["communities.json", "theme_snapshots.json", "theme_lineage.json", "theme_metrics.parquet"],
  "community_count": 12
}
```

### `POST /api/exposure/compute`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["company_theme_exposure.parquet"],
  "theme_count": 12,
  "company_theme_pair_count": 96
}
```

### `POST /api/themes/document-affinity`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "max_themes_per_document": 20
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["document_theme_affinity.parquet"],
  "mapped_documents": 28,
  "mapped_pairs": 64
}
```

### `POST /api/reporting/news-package`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "max_documents": 100,
  "max_chunks_per_document": 4,
  "max_chunk_chars": 1200,
  "include_document_types": ["news", "press_release"],
  "include_companies": ["SHOP.TO", "ENB.TO"],
  "include_macro": false,
  "include_affinity": true
}
```

Output:

```json
{
  "success": true,
  "artifact": "news_report_package.json",
  "artifact_path": "discovery/news_report_package.json",
  "package_version": "1.0",
  "total_documents": 36,
  "total_chunks": 144
}
```

### `POST /api/reporting/research-package`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "max_documents": 80,
  "max_chunks_per_document": 6,
  "max_chunk_chars": 1500,
  "include_companies": ["SHOP.TO", "ENB.TO"],
  "include_macro": false,
  "include_affinity": true
}
```

Notes:

- `include_document_types` defaults to `["research"]` when omitted.
- If you pass `include_document_types`, it is used as a normal filter.

Output:

```json
{
  "success": true,
  "artifact": "news_report_package.json",
  "artifact_path": "discovery/news_report_package.json",
  "package_version": "1.0",
  "total_documents": 24,
  "total_chunks": 144
}
```

### `POST /api/discovery/freeze`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "discovery_frozen": true,
  "manifest_path": "data/runs/run_20240630_120000/run_manifest.json"
}
```

### `GET /api/artifacts/{run_id}/{artifact_name}`

Path format:

```text
data/runs/<run_id>/<artifact_name>
```

`artifact_name` may include subdirectories such as `discovery/raw_documents.parquet`
or `validation/validation.csv`.

Behavior:

- Returns 200 with raw file body for existing artifact file.
- Returns 404 if the artifact path is invalid, missing, or traverses outside run root.

Security rules:

- `artifact_name` must be a relative path.
- Parent traversal (`..`) is rejected.
- Directory paths are invalid (only files are returned).

### `POST /api/validation/run`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "market_data_dir": "data/inputs/market",
  "fundamentals_data_dir": "data/inputs/fundamentals",
  "include_fundamentals": false
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["market_prices.parquet", "fundamentals.parquet", "portfolio_baskets.parquet", "validation.csv"],
  "validated_themes": 12
}
```

### `POST /api/report/generate`

Input:

```json
{
  "run_id": "run_20240630_120000"
}
```

Output:

```json
{
  "success": true,
  "artifact": "report.md",
  "report_path": "data/runs/run_20240630_120000/report.md"
}
```

## 25. Agent I/O Rule

Every agent handoff should state:

```text
Inputs read:
Outputs written:
Artifacts missing:
Acceptance checks passed:
Acceptance checks failed:
Next recommended step:
```

## 26. Skill I/O Rule

Every skill execution should state:

```text
Input artifacts:
Output artifacts:
Config values used:
Tests or checks run:
Known caveats:
```
