# Input / Output Contracts

This document defines the canonical input and output formats for the MVP pipeline. Every agent, skill, API, and code module should preserve these contracts unless `theme_discovery_engine_v1.md` is updated first.

## 1. Contract Principles

- Every stage declares input artifacts and output artifacts.
- Every table uses lowercase snake case columns.
- Every artifact belongs under `data/runs/<run_id>/`.
- Every run has exactly one `run_manifest.json`.
- Discovery artifacts must be frozen before validation reads future returns or fundamentals.
- Missing required fields should fail early with a clear stage error.

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
  "input_hash": "sha256:...",
  "model_config_hash": "sha256:...",
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
- `discovery_frozen`

## 3. Stage Contract Summary

| Stage | Inputs | Outputs |
|---|---|---|
| Create Run | configs | `run_manifest.json` |
| Import Documents | raw files, manifest | `documents.parquet` |
| Chunk Documents | `documents.parquet` | `chunks.parquet` |
| Extract Entities | `chunks.parquet` | `entities.parquet`, `entity_aliases.parquet` |
| Extract Edges | `chunks.parquet`, `entities.parquet` | `edges.parquet`, `edge_explanations.parquet` |
| Build Graph | `entities.parquet`, `edges.parquet` | `graph.json` |
| Discover Themes | `graph.json` | `communities.json`, `theme_snapshots.json`, `theme_metrics.parquet` |
| Compute Exposure | `communities.json`, `graph.json`, `entities.parquet`, `edges.parquet` | `company_theme_exposure.parquet` |
| Freeze Discovery | all discovery artifacts | updated `run_manifest.json` |
| Validate | frozen discovery artifacts, market/fundamental data | `validation.csv` |
| Report | all artifacts | `report.md` |

## 4. `documents.parquet`

One row per source document.

Required columns:

```text
schema_version: string
document_id: string
source: string
source_id: string
title: string
document_type: string
company_id: string | null
published_at: date | timestamp
available_at: date | timestamp
raw_path: string
content_hash: string
ingested_at: timestamp
included_in_discovery: bool
exclusion_reason: string | null
```

Rules:

- `available_at` is mandatory.
- `included_in_discovery` must be false when `available_at > as_of_date`.
- `content_hash` is used for duplicate detection.

## 5. `chunks.parquet`

One row per text chunk.

Required columns:

```text
schema_version: string
chunk_id: string
document_id: string
chunk_index: int
text: string
token_count: int | null
start_char: int | null
end_char: int | null
available_at: date | timestamp
content_hash: string
```

Rules:

- `chunk_id` must be stable for the same document hash and chunking config.
- Chunks inherit `available_at` from documents.

## 6. `entities.parquet`

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

## 7. `entity_aliases.parquet`

One row per alias mapping.

Required columns:

```text
schema_version: string
alias: string
canonical_entity_id: string
canonical_name: string
confidence: float
method: string
review_status: string
created_at: timestamp
```

## 8. `edges.parquet`

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

## 9. `edge_explanations.parquet`

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

## 10. `graph.json`

Format:

```json
{
  "schema_version": "1.0",
  "run_id": "run_20240630_120000",
  "as_of_date": "2024-06-30",
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
      "evidence_chunk_ids": ["chunk_001"]
    }
  ]
}
```

## 11. `communities.json`

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

## 12. `theme_snapshots.json`

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

## 13. `theme_metrics.parquet`

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

## 14. `company_theme_exposure.parquet`

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

## 15. `validation.csv`

One row per theme and validation window.

Required columns:

```text
schema_version
run_id
as_of_date
theme_snapshot_id
community_id
theme_name
forward_window
portfolio_method
company_count
theme_basket_return
benchmark_name
benchmark_return
excess_return
sample_size
caveats
```

Rules:

- Validation can run only after `discovery_frozen=true`.
- Benchmark must be explicit.
- Do not suppress negative results.

## 16. `report.md`

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

## 17. API I/O Contracts

### `POST /api/runs/create`

Input:

```json
{
  "run_name": "demo_ai_infra_asof_2024_06_30",
  "as_of_date": "2024-06-30",
  "universe_config": "configs/universe.example.yml",
  "pipeline_config": "configs/pipeline.example.yml",
  "validation_config": "configs/validation.example.yml"
}
```

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
  "documents_dir": "data/inputs/documents"
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["documents.parquet", "chunks.parquet"],
  "included_documents": 42,
  "excluded_documents": 3
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
  "artifacts": ["entities.parquet", "edges.parquet", "graph.json"],
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
  "artifacts": ["communities.json", "theme_snapshots.json", "theme_metrics.parquet"],
  "community_count": 12
}
```

### `POST /api/validation/run`

Input:

```json
{
  "run_id": "run_20240630_120000",
  "freeze_discovery": true
}
```

Output:

```json
{
  "success": true,
  "artifacts": ["company_theme_exposure.parquet", "validation.csv"],
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

## 18. Agent I/O Rule

Every agent handoff should state:

```text
Inputs read:
Outputs written:
Artifacts missing:
Acceptance checks passed:
Acceptance checks failed:
Next recommended step:
```

## 19. Skill I/O Rule

Every skill execution should state:

```text
Input artifacts:
Output artifacts:
Config values used:
Tests or checks run:
Known caveats:
```

