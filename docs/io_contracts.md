# Input / Output Contracts

This document defines the canonical input and output formats for the MVP pipeline. `docs/data_schema.md` defines the layered data model; this file defines the exact artifact and API contracts. Every agent, skill, API, and code module should preserve these contracts unless `theme_discovery_engine_v1.md` is updated first.

## 1. Contract Principles

- Every stage declares input artifacts and output artifacts.
- Every table uses lowercase snake case columns.
- Every artifact belongs under `data/runs/<run_id>/`.
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
| Create Run | configs | `run_manifest.json` |
| Import Raw Documents | raw files, `source_manifest.csv` | `discovery/raw_documents.parquet` |
| Clean Documents | `discovery/raw_documents.parquet` | `discovery/documents.parquet`, `discovery/document_cleaning_log.parquet` |
| Chunk Documents | `discovery/documents.parquet` | `discovery/chunks.parquet` |
| Extract Entities | `discovery/chunks.parquet` | `discovery/entities.parquet`, `discovery/entity_aliases.parquet`, `discovery/entity_aliases_global.parquet` (optional), `discovery/entity_chunk_provenance.parquet` (E1) |
| Extract Edges | `discovery/chunks.parquet`, `discovery/entities.parquet` | `discovery/edges.parquet`, `discovery/edge_explanations.parquet` |
| Build Graph | `discovery/entities.parquet`, `discovery/edges.parquet` | `discovery/graph.json` |
| Discover Themes | `discovery/graph.json` | `discovery/communities.json`, `discovery/theme_snapshots.json`, `discovery/theme_lineage.json`, `discovery/theme_metrics.parquet` |
| Compute Exposure | `discovery/communities.json`, `discovery/graph.json`, `discovery/entities.parquet`, `discovery/edges.parquet` | `discovery/company_theme_exposure.parquet` |
| Materialize Provenance (EG-E) | `discovery/communities.json`, `discovery/theme_snapshots.json`, `discovery/edges.parquet`, `discovery/chunks.parquet`, `discovery/company_theme_exposure.parquet` | `discovery/theme_document_evidence.parquet` (E2), `discovery/company_theme_document_evidence.parquet` (E3) |
| Freeze Discovery | all discovery artifacts | updated `run_manifest.json` |
| Load Market Data | market files or adapter output | `validation/market_prices.parquet` |
| Ingest XBRL Fundamentals (B1) | EDGAR company-facts JSON (local), `configs/fundamentals.yml` | `discovery/fundamentals_asreported.parquet` |
| Load Fundamentals | fundamentals files or adapter output | `validation/fundamentals.parquet` |
| Validate | frozen discovery artifacts, `discovery/company_theme_exposure.parquet`, `validation/market_prices.parquet`, optional `validation/fundamentals.parquet` | `validation/portfolio_baskets.parquet`, `validation/validation.csv` |
| Report | all artifacts | `report.md` |

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
block_type: string
table_data: string | null
available_at: date | timestamp
content_hash: string
cleaning_version: string
```

Allowed `block_type` values:

- `prose` — regular narrative text, sentence/paragraph-aware chunked.
- `table` — a structured cell grid; rows × cells recovered from HTML `<table>`
  or ASCII pipe-delimited tables.  `text` is the human-readable pipe-rendered
  form; `table_data` is a JSON string `{"rows": [["col1", ...], ...]}`.
- `heading` — reserved for future standalone heading chunks (not yet emitted).

Rules:

- `chunk_id` must be stable for the same document hash and chunking config.
- `block_type` must be present on EVERY chunk (no nulls allowed).
- `table_data` is a JSON string for `block_type="table"` chunks; null otherwise.
- `section_title` should be non-null for ≥80 % of chunks derived from
  structured filings (EDGAR/HTML) where section headings are detectable.
- Chunks inherit `available_at` from documents.
- `text` must come from cleaned document text, not raw unnormalized files.
- Table chunks are atomic: one table = one chunk (numbers stay with their labels).

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
direction: int8   — evidence-backed signed direction (#110; additive, backward-compatible)
```

`direction` field (#110 — additive column, default 0):

- Applies to `causes`, `exposed_to`, `sensitive_to` edges only.
- `+1` — relationship is beneficial / tailwind (source positively drives target).
- `-1` — relationship is adverse / headwind (source negatively impacts target).
- `0` — unknown / ambiguous. **Locked design decision**: unknown defaults to 0, NOT +1.
  An edge with direction=0 is EXCLUDED from signed propagation (polarity=0 in graph.json).
- `benefits` and `hurts` carry their sign via `edge_type` / `base_polarity`; no `direction` needed.
- `co_occurs_with`, `mentioned_in`, `located_in` always have direction=0 (and polarity=0).
- Old `edges.parquet` without the column load safely; missing values treated as 0.

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
- `direction` must be in `{-1, 0, 1}` when present; 0 is the safe default for all edge types.

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
      "extraction_method": "document_stated",
      "polarity": 1,
      "propagation_weight": 0.72
    }
  ],
  "community_input_edges": ["edge_001"]
}
```

Edge fields (FI-A additions, updated in #110):

- `polarity` — integer signed polarity for forward-inference propagation (FI-A/FI-B):
  `+1` (same-direction), `-1` (opposite-direction), `0` (undirected / excluded).
  Effective polarity rule (post-#110):
  - `causes` / `exposed_to` / `sensitive_to`: polarity = the extracted `direction` field
    from edges.parquet. `0` if direction is unknown/absent (EXCLUDED from signed propagation).
    Locked design decision: unknown -> 0, NOT +1.
  - `benefits`: polarity = +1 (from ontology base_polarity; unchanged).
  - `hurts`: polarity = -1 (from ontology base_polarity; unchanged).
  - All others (`co_occurs_with`, `mentioned_in`, `located_in`, …): polarity = 0.
  Present on ALL edges in the `edges` list (structural and evidence alike).
- `propagation_weight` — float in `(0, 1]` representing signal attenuation per hop.
  Formula: `max(confidence, 0.01)`. Evidence count and recency are optional future
  enhancements not yet implemented.

Rules:

- Structural community detection uses only `community_input_edges`.
- `community_input_edges` must only reference edges with non-Document endpoints and `edge_type in structural_edge_types`.
- `evidence_edge_types` are allowed for traceability and reporting, but are excluded from structure-based clustering by default.
- `run_id`, `as_of_date`, and `graph.json` content are immutable after freeze.
- `polarity` and `propagation_weight` are informational substrate fields for FI-B; they must NOT alter which edges appear in `community_input_edges`.

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

## 19. `market_prices.parquet`

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

## 20a. `fundamentals_asreported.parquet` (discovery-time, PIT)

**New in EG-B1.** One row per company, period, and as-reported financial metric,
produced by the XBRL ingestion adapter (`fundamentals_adapter.py`) during
**discovery**. This is **separate** from the validation-only §20 artifact below.

**Universe note:** This artifact serves the S&P/TSX 60 (Canadian companies;
currency CAD). Companies are identified by their `tsx_ticker`; cross-listed
filers are fetched by `sec_cik` from `configs/universe.tsx60.yml`. Companies
with `sec_cik=null` have no EDGAR data and receive an empty-but-schema-valid
artifact.

**Taxonomy note:** Canadian cross-filers use IFRS (`ifrs-full` namespace in
EDGAR company-facts JSON). The adapter searches `ifrs-full` first, then
`us-gaap` as fallback. IFRS concepts used: `Revenue`, `ProfitLoss`,
`BasicEarningsLossPerShare`, `CashFlowsFromUsedInOperatingActivities`,
`LongtermBorrowings`, `GrossProfit`, `ProfitFromOperatingActivities`.

Path: `data/runs/<run_id>/discovery/fundamentals_asreported.parquet`

Required columns:

```text
company_id:    string         — canonical company identifier (tsx_ticker, e.g. "RY.TO")
period_end:    string (date)  — fiscal period end date (YYYY-MM-DD)
metric_name:   string         — canonical metric name from configs/fundamentals.yml
metric_value:  float          — as-reported numeric value
unit:          string         — actual XBRL unit, e.g. "CAD", "CAD/shares", "ratio"
currency:      string | null  — ISO currency code extracted from unit (e.g. "CAD");
                                null for ratios and unitless metrics
filing_date:   string (date)  — date the filing became public (EDGAR filingDate)
available_at:  string (date)  — = filing_date (PIT: first public date; never period_end)
source:        string         — "edgar_xbrl"
source_id:     string         — stable hash of (company_id, accession_number)
```

Currency rules:
- `currency` is always read from the XBRL unit string, never assumed or hardcoded.
- `"CAD"` -> currency `"CAD"`. `"CAD/shares"` (EPS) -> currency `"CAD"`, unit `"CAD/shares"`.
- Ratio metrics (gross_margin, operating_margin, ebitda_margin) have `currency=null`.

Reconciliation key: `(company_id, period_end, metric_name)`.
For any as-reported overlap between B1 (XBRL) and B2 (LLM), **B1 wins**; B2
owns guidance / forward-looking / narrative numbers only.

Rules:

- `available_at` = `filing_date` (the date the document was first public). Never
  use `period_end` as `available_at` — that would create future leakage.
- Only rows with `available_at <= run.as_of_date` are surfaced in discovery.
- `metric_name` values must come from `configs/fundamentals.yml`; none are
  hardcoded inside adapter code.
- As-reported values only (first published); later restatements are new rows with
  a later `filing_date` but must not silently overwrite earlier rows.
- Companies with `sec_cik=null` in the universe config have no EDGAR data; write
  a schema-valid empty artifact (zero rows, same columns). Do not omit the artifact.
- Discovery stages may read this artifact; validation stages may not use it as a
  substitute for the §20 artifact.

## 20b. `financial_metrics.parquet` (discovery-time, EG-B2)

**New in EG-B2.** One row per quantified financial claim extracted by the LLM
fact-extraction pass (`run_fact_extraction`). Emits `FinancialMetric` nodes
into the graph.

Path: `data/runs/<run_id>/discovery/financial_metrics.parquet`

**PIT rule:** Only chunks with `available_at <= run.as_of_date` are processed.
Claims derived from future-dated chunks are silently dropped before any claim
is written.

**Reconciliation:** The B1 XBRL artifact (`fundamentals_asreported.parquet`)
is the authoritative source for as-reported values. When B1 covers a
`(company_id, period_end, metric_name)` triple, the corresponding B2 LLM
as-reported claim (`is_guidance=False`) is dropped. B2 LLM guidance claims
(`is_guidance=True`) are always kept regardless of B1 overlap.

Period matching: B1 stores `period_end` as ISO YYYY-MM-DD. B2 LLM periods are
free-text (e.g. "Q2 2024"). The reconciliation layer normalizes LLM periods to
their calendar quarter-end ISO date before the lookup:
- "Q1 YYYY" -> "YYYY-03-31", "Q2 YYYY" -> "YYYY-06-30"
- "Q3 YYYY" -> "YYYY-09-30", "Q4 YYYY" -> "YYYY-12-31"
- "FY YYYY" -> "YYYY-12-31"
- "H1 YYYY" -> "YYYY-06-30", "H2 YYYY" -> "YYYY-12-31"

Required columns:

```text
schema_version:    string        — "1.0"
metric_id:         string        — stable deterministic id (fm_<sha256[:16]>)
company_id:        string        — canonical company identifier (tsx_ticker)
metric_name:       string        — from configs/fundamentals.yml whitelist
value:             float         — extracted numeric value
unit:              string        — e.g. "CAD_billions", "percent", "CAD_per_share"
period:            string        — LLM free-text period (e.g. "Q2 2024", "FY 2024")
direction:         string        — "rose" | "fell" | "stable" | "" (empty = unknown)
is_guidance:       bool          — True = forward-looking / management guidance
confidence:        float         — 0.0–1.0 extractor confidence score
evidence_chunk_id: string        — REQUIRED: non-empty chunk_id of the source text
source:            string        — extractor name (e.g. "rule_based_fact_extractor_v1")
created_at:        string        — ISO UTC timestamp of row creation
```

Rules:

- Every row MUST have a non-empty `evidence_chunk_id`. Claims without evidence
  are dropped before writing.
- `metric_name` must be in the `configs/fundamentals.yml` whitelist.
- `metric_id` is stable: same `(company_id, metric_name, period, is_guidance)`
  always yields the same `metric_id`.
- B2 never overwrites B1 as-reported values; only guidance values are exclusive
  to B2.

## 20c. `financial_metric_edges.parquet` (discovery-time, EG-B2)

**New in EG-B2.** One row per edge connecting a Company entity to a
FinancialMetric node. Written alongside `financial_metrics.parquet` by
`run_fact_extraction`.

Path: `data/runs/<run_id>/discovery/financial_metric_edges.parquet`

Edge types:
- `reports` — Company reported this metric (is_guidance=False)
- `guides_to` — Company issued guidance for this metric (is_guidance=True)

Required columns:

```text
schema_version:    string        — "1.0"
edge_id:           string        — stable deterministic id (fme_<sha256[:16]>)
company_entity_id: string        — entity_id of the Company node (from entities.parquet)
metric_id:         string        — metric_id from financial_metrics.parquet
edge_type:         string        — "reports" | "guides_to"
evidence_chunk_ids: list[string] — all chunk_ids that contributed evidence for this edge
confidence:        float         — 0.0–1.0
created_at:        string        — ISO UTC timestamp of row creation
```

Rules:

- `edge_id` is stable: same `(company_entity_id, metric_id, edge_type)` always
  yields the same `edge_id`.
- `evidence_chunk_ids` must be non-empty (at least one chunk must provide the
  evidence that grounded the corresponding `financial_metrics` row).
- `edge_type` maps 1-to-1 from `is_guidance`: False -> "reports", True -> "guides_to".

## 20. `fundamentals.parquet`

**Validation-only** — this is the §20 artifact for walk-forward validation.
**Discovery stages must never read or write this file.**

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
  "extraction_failed": 2
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

---

## SENT-A: Chunk-Tone Artifact (Workstream S-A, GitHub #99)

### S-A.1 `chunk_tone.parquet`

**New in SENT-A.** One row per chunk. Contains the LM tone vector (token-normalised
counts per category), the matched-word list per category (for auditability), the
speaker role, and the token count used for normalisation.

Path: `data/runs/<run_id>/discovery/chunk_tone.parquet`

**Lexicon:** Loughran-McDonald (LM) Master Dictionary (Loughran & McDonald 2011,
Journal of Finance). The committed file `data/lexicons/loughran_mcdonald.csv` is a
curated representative subset; replace with the full LM Master Dictionary CSV for
production. No code change is needed — the loader reads whatever rows are present.

**Hermetic:** No network calls. All computation is local and deterministic.

**Finance-neutral terms:** Words like "liability", "cost", "depreciation" carry
NO negative flag in the LM lexicon (unlike generic word lists). Tests assert this.

**Config:** Category set and speaker-role attribution rules come from
`configs/sentiment.yml` — nothing is hardcoded in the scorer.

Required columns:

```text
chunk_id:             string       — references chunks.parquet
document_id:          string       — references documents.parquet
available_at:         string       — inherited from chunk (YYYY-MM-DD)
speaker_role:         string       — "management" | "analyst" | "media" | "unknown"
token_count:          int          — token count used for normalisation
tone_positive:        float        — positive word count / token_count
tone_negative:        float        — negative word count / token_count
tone_uncertainty:     float        — uncertainty word count / token_count
tone_litigious:       float        — litigious word count / token_count
tone_strong_modal:    float        — strong_modal word count / token_count
tone_weak_modal:      float        — weak_modal word count / token_count
matched_positive:     list[str]    — matched tokens in occurrence order
matched_negative:     list[str]    — matched tokens in occurrence order
matched_uncertainty:  list[str]    — matched tokens in occurrence order
matched_litigious:    list[str]    — matched tokens in occurrence order
matched_strong_modal: list[str]    — matched tokens in occurrence order
matched_weak_modal:   list[str]    — matched tokens in occurrence order
```

Rules:

- `available_at` is inherited from `chunks.parquet.available_at` (PIT-clean).
- Tone scores are token-normalised: `score = raw_count / max(token_count, 1)`.
- `matched_*` lists preserve order of occurrence in the text (duplicates retained);
  they enable exact auditability of every score.
- `speaker_role` is derived from `document_type` (from `documents.parquet`) and
  `section_title` (from `chunks.parquet`) via the attribution rules in
  `configs/sentiment.yml`.  No hardcoded rule logic.
- `chunk_id` is the join key to `chunks.parquet`.
- This artifact is a **discovery artifact** — never write to the validation path.
- SENT-B (company-level aggregation) and SENT-C (temporal trending) read this
  artifact; they must not modify it.

### S-A.2 Scorer Module

Python module: `app/backend/theme_engine/sentiment_lexicon.py`

Public API:

```python
load_lexicon(csv_path=None, config=None) -> dict[str, dict[str, int]]
score_chunk(text, token_count, *, lexicon, categories, ...) -> dict
tag_speaker_role(chunk, attribution_cfg, ...) -> str
score_chunks(chunks, *, lexicon, config, ...) -> list[dict]
```

Acceptance criteria (all covered by tests):
- Correct category counts and EXACT matched-word list on a committed MD&A fixture.
- An uncertainty-heavy passage scores high `tone_uncertainty` (token-normalised).
- "liability" is NOT in the negative matched-words list (LM vs. Harvard-GI proof).
- `speaker_role` correctly distinguishes MD&A/transcript (management) from news (media).
- No network access at any point (hermetic).
- Loader works on the curated subset and on a fuller CSV without code change.

---

## SENT-B: Management Sentiment Artifacts (Workstream S-B, GitHub #100)

### S-B.1 `management_sentiment.parquet`

**New in SENT-B.** One row per management-sentiment judgment extracted by the
LLM management-sentiment pass (`run_sentiment_extraction`). Emits `Sentiment`
nodes into the ontology attached to their Company via `expresses_sentiment` edges.

**Speaker gating:** Only management-attributable chunks (`speaker_role == "management"`
per SENT-A's `tag_speaker_role` tagger) are processed. Media/analyst/unknown chunks
are skipped for cost discipline.

**Lexicon grounding:** SENT-A's matched-word lists (LM lexicon hits per category)
are passed into the LLM prompt as evidence. The model judges from the full text
(negation and context override raw lexicon signals).

**PIT rule:** Only chunks with `available_at <= run.as_of_date` are processed.

**Evidence discipline:** Every row MUST have a non-empty `evidence_chunk_id`.
Records without evidence are dropped before writing.

**Discovery-evidence only:** Sentiment is NOT scored into exposure. It is kept
for auditability, SENT-C fusion (agree/hedged/conflict flag), and UI display.

Path: `data/runs/<run_id>/discovery/management_sentiment.parquet`

Required columns:

```text
schema_version:   string       — "1.0"
sentiment_id:     string       — stable deterministic id (sent_<sha256[:16]>)
company_id:       string       — canonical company identifier
speaker_role:     string       — always "management" in SENT-B output
direction:        string       — "positive" | "negative" | "neutral" | "mixed"
confidence_tone:  string       — "high" | "moderate" | "low" (assertiveness of language)
hedging:          bool         — True if significant hedge/uncertainty language present
forward_stance:   string       — "optimistic" | "cautious" | "neutral" | "negative"
confidence:       float        — 0.0–1.0 extractor confidence in this judgment
evidence_chunk_id: string      — REQUIRED: non-empty chunk_id of the source text
lexicon_hits:     string       — JSON: matched words per LM category (SENT-A grounding evidence)
created_at:       string       — ISO UTC timestamp
```

Rules:

- Every row MUST have a non-empty `evidence_chunk_id`. Records without evidence
  are dropped before writing.
- `direction` must be one of: "positive", "negative", "neutral", "mixed".
- `sentiment_id` is stable: same `(company_id, evidence_chunk_id, direction)`
  always yields the same `sentiment_id`.
- `lexicon_hits` is a JSON string encoding the SENT-A matched words that were
  passed to the LLM as grounding evidence. Always present (empty JSON `{}` when
  SENT-A hasn't run yet).
- Fusion with the lexicon (agree/hedged/conflict flag) is SENT-C, not SENT-B.
- No Management entity type is introduced. Attribution is via `speaker_role` field.

### S-B.2 `sentiment_edges.parquet`

**New in SENT-B.** One row per edge connecting a Company entity to a Sentiment
node. Written alongside `management_sentiment.parquet` by `run_sentiment_extraction`.

Edge type: `expresses_sentiment` (Company -> Sentiment, discovery-evidence only,
`base_polarity: 0`, excluded from structural community detection).

Path: `data/runs/<run_id>/discovery/sentiment_edges.parquet`

Required columns:

```text
schema_version:     string        — "1.0"
edge_id:            string        — stable deterministic id (sente_<sha256[:16]>)
company_entity_id:  string        — entity_id of the Company node (from entities.parquet)
sentiment_id:       string        — sentiment_id from management_sentiment.parquet
edge_type:          string        — always "expresses_sentiment"
speaker_role:       string        — attribution field (always "management" in SENT-B)
evidence_chunk_ids: list[string]  — chunk_ids that grounded this edge (non-empty)
confidence:         float         — 0.0–1.0
created_at:         string        — ISO UTC timestamp
```

Rules:

- `edge_id` is stable: same `(company_entity_id, sentiment_id)` always yields
  the same `edge_id`.
- `evidence_chunk_ids` must be non-empty.
- `edge_type` is always "expresses_sentiment" for SENT-B output.
- `expresses_sentiment` edges are structural=false; they never enter community
  detection or exposure scoring.

### S-B.3 Extractor Module

Python function: `app/backend/theme_engine/extraction.run_sentiment_extraction`

Public API additions in `extraction.py`:

```python
# Data structures
SentimentRecord      — dataclass: company_id, speaker_role, direction,
                        confidence_tone, hedging, forward_stance,
                        evidence_chunk_id, confidence, lexicon_hits
SentimentResult      — dataclass: records: list[SentimentRecord]

# Extractor protocol + implementations
SentimentExtractor           — ABC protocol
RuleBasedSentimentExtractor  — deterministic, negation-aware, no network (tests/CI)
OpenAISentimentExtractor     — LLM-backed, must be explicitly injected

# Column lists
MANAGEMENT_SENTIMENT_COLUMNS  — list[str] for management_sentiment.parquet
SENTIMENT_EDGES_COLUMNS       — list[str] for sentiment_edges.parquet

# Core function
run_sentiment_extraction(run_id, sentiment_extractor=None) -> int
```

Acceptance criteria (all covered by tests):

- On a committed management/transcript/MD&A fixture: >=1 sentiment record with
  a direction, a forward_stance, and a non-empty evidence_chunk_id.
- Hermetic: test injects a FakeSentimentExtractor (no network).
- No record without an evidence chunk (negative test).
- Negation: "we do NOT see strong demand" is judged negative despite "strong".
- Speaker gating: a news/media chunk is NOT sent to the sentiment pass.
- PIT: a chunk with available_at > as_of is not processed.
- Lexicon grounding: lexicon_hits is populated in the output record.

---

## SENT-C: Sentiment Fusion Artifact (Workstream S-C, GitHub #101)

### S-C.1 `management_sentiment_fused.parquet`

**New in SENT-C.** One fused record per `(company_id, evidence_chunk_id)` that
reconciles the SENT-A lexicon tone vector with the SENT-B LLM judgment.  The
result is a headline `fused_tone` and an `agreement` flag that surface the
"management hedging" signal directly.

**Decision:** sibling artifact (not an augmentation of management_sentiment.parquet).
This avoids mutating SENT-B's output and keeps the two upstream artifacts independent.

**Discovery-evidence only:** This artifact MUST NOT be read by exposure.py or any
scoring stage.  It is a one-way input to forward-inference stages only.

Path: `data/runs/<run_id>/discovery/management_sentiment_fused.parquet`

Required columns:

```text
schema_version:    string  — "1.0"
fusion_id:         string  — stable id: fusion_<sha256[:16]> of (company_id, evidence_chunk_id)
sentiment_id:      string  — references management_sentiment.parquet
company_id:        string  — canonical company identifier
speaker_role:      string  — always "management" for SENT-C input
direction:         string  — LLM direction from SENT-B: positive | negative | neutral | mixed
confidence_tone:   string  — LLM assertiveness: high | moderate | low
hedging:           bool    — LLM hedge flag from SENT-B
forward_stance:    string  — LLM stance: optimistic | cautious | neutral | negative
evidence_chunk_id: string  — REQUIRED: source chunk_id (resolves via source.py chain)
lexicon_hits:      string  — JSON: matched words per LM category (from SENT-B)
tone_positive:     float   — SENT-A token-normalised positive score
tone_negative:     float   — SENT-A token-normalised negative score
tone_uncertainty:  float   — SENT-A token-normalised uncertainty score
tone_litigious:    float   — SENT-A token-normalised litigious score
tone_strong_modal: float   — SENT-A token-normalised strong_modal score
tone_weak_modal:   float   — SENT-A token-normalised weak_modal score
lm_direction:      string  — derived LM direction: positive | negative | uncertainty | neutral
fused_tone:        string  — reconciled: positive | neutral | negative | hedged
agreement:         string  — agree | hedged | conflict
fused_confidence:  float   — LLM confidence after downgrade (×0.75 hedged, ×0.50 conflict)
available_at:      string  — YYYY-MM-DD from chunk_tone PIT gate (empty if SENT-A absent)
created_at:        string  — ISO UTC timestamp
```

Rules:

- `fusion_id` is stable: same `(company_id, evidence_chunk_id)` always yields the same id.
- `evidence_chunk_id` MUST be non-empty (same discipline as SENT-B).
- PIT gate: only rows whose chunk_tone `available_at <= run.as_of_date` are emitted.
- Fusion degrades gracefully when SENT-A is absent: tone scores default to 0.0,
  `lm_direction = "neutral"`, `available_at = ""`.

### S-C.2 Fusion Rules

**LM direction derivation:**

```text
if tone_uncertainty > max(tone_positive, tone_negative) and > TONE_MIN_THRESHOLD (0.005):
    lm_direction = "uncertainty"
elif tone_positive > tone_negative and > TONE_MIN_THRESHOLD:
    lm_direction = "positive"
elif tone_negative > 0 and > TONE_MIN_THRESHOLD:
    lm_direction = "negative"
else:
    lm_direction = "neutral"
```

**Agreement classification:**

```text
llm_direction=positive + lm_direction=positive          → agree,   fused_tone=positive
llm_direction=positive + lm_direction=uncertainty       → hedged,  fused_tone=hedged
llm_direction=positive + lm_direction=neutral           → hedged,  fused_tone=hedged
llm_direction=positive + lm_direction=negative          → conflict, fused_tone=negative
llm_direction=negative + lm_direction=negative          → agree,   fused_tone=negative
llm_direction=negative + lm_direction=uncertainty       → agree,   fused_tone=negative
llm_direction=negative + lm_direction=neutral           → hedged,  fused_tone=hedged
llm_direction=negative + lm_direction=positive          → conflict, fused_tone=negative
llm_direction=neutral  + lm_direction=neutral           → agree,   fused_tone=neutral
llm_direction=neutral  + lm_direction=anything_else     → hedged,  fused_tone=hedged
llm_direction=mixed    (any lm_direction)               → hedged,  fused_tone=hedged
```

Disagreement is a first-class signal (management hedging), not noise to average away.

**Confidence downgrade:**

```text
agree:   fused_confidence = original_confidence  (no discount)
hedged:  fused_confidence = original_confidence × 0.75
conflict: fused_confidence = original_confidence × 0.50
```

### S-C.3 Fusion Module

Python module: `app/backend/theme_engine/sentiment_fusion.py`

Public API:

```python
# Constants
MANAGEMENT_SENTIMENT_FUSED_COLUMNS   — list[str] column contract
HEDGED_DISCOUNT    = 0.75
CONFLICT_DISCOUNT  = 0.50
TONE_MIN_THRESHOLD = 0.005

# Pure functions (no I/O — useful for unit tests)
derive_lm_direction(tone_positive, tone_negative, tone_uncertainty) -> str
classify_agreement(llm_direction, lm_direction, tone_uncertainty) -> str
derive_fused_tone(agreement, llm_direction) -> str
apply_confidence_discount(confidence, agreement) -> float
fuse_records(sentiment_row, tone_row, as_of_date) -> dict | None

# Pipeline entry point (reads artifacts, writes fused artifact)
run_sentiment_fusion(run_id) -> int   # returns number of fused rows written
```

Acceptance criteria (covered by tests in `tests/test_sentiment_fusion.py`):

- LLM positive + LM uncertainty-dense → agreement=hedged, confidence reduced by ×0.75.
- LLM positive + LM positive-dense → agreement=agree, no confidence discount.
- LLM positive + LM negative-dense → agreement=conflict, fused_tone=negative, confidence ×0.50.
- PIT gate: chunk with available_at > as_of_date is dropped from output.
- Evidence discipline: row with empty evidence_chunk_id is dropped.
- Column contract: output matches MANAGEMENT_SENTIMENT_FUSED_COLUMNS exactly.
- NOT in exposure.py: grep-proven (test_fused_artifact_not_in_exposure).

---

## EG-E Provenance Artifacts (Workstream E)

These three artifacts eliminate multi-hop graph walks for provenance questions.
They are written by the Extract Entities stage (E1) and the Materialize Provenance
stage (E2, E3).

### E1. `entity_chunk_provenance.parquet`

One row per (entity_id, chunk_id) occurrence, preserving the originating document
and its subject company.

Required columns:

```text
schema_version: string
entity_id: string        — references entities.parquet
chunk_id: string         — references chunks.parquet
document_id: string      — originating document (references documents.parquet)
company_id: string | null — document.company_id (subject of the document), NOT the
                            extracted entity's own id. Nullable when document has no
                            company_id (e.g. macro/news articles).
available_at: string     — inherited from chunk (YYYY-MM-DD); always <= as_of_date
```

Rules:

- Written by `extraction.run_extraction` alongside entities.parquet.
- One row per entity per chunk (already deduplicated against entity_map).
- `company_id` is the DOCUMENT's subject company (documents.company_id), NOT the
  entity's own identity. A news article about CompanyX that mentions CompanyY has
  company_id=CompanyX for all entities extracted from it (including CompanyY).
- PIT-clean: all chunks already satisfy available_at <= as_of_date by the time
  extraction runs.

### E2. `theme_document_evidence.parquet`

One row per community (theme). Enables one-read lookup of a theme's source documents
without any client-side graph walk.

Required columns:

```text
schema_version: string
as_of_date: string       — run's as_of_date
community_id: string     — references communities.json
theme_snapshot_id: string — references theme_snapshots.json
chunk_ids: list[string]  — sorted, deduped chunk ids from all edges touching this community
document_ids: list[string] — deduped document ids resolved from chunk_ids
```

Rules:

- Written by `POST /api/provenance/materialize` after themes are discovered.
- chunk_ids includes evidence from ALL structural edges where at least one endpoint
  is in the community's node_ids (intra-community AND cross-community edges).
- document_ids are resolved by joining chunk_ids against chunks.parquet.document_id.
- PIT-clean: only edges with first_seen_at <= as_of_date contribute.
- One row per community_id; community_ids match communities.json exactly.

### E3. `company_theme_document_evidence.parquet`

One row per (company_id, theme_snapshot_id, community_id). Enables per-theme document
lookup for a company without a graph walk. Evidence groups are DISTINCT per theme.

Required columns:

```text
schema_version: string
as_of_date: string       — run's as_of_date
company_id: string       — Company ENTITY id from entities.parquet (entity_type=Company).
                           NOT document.company_id.
theme_snapshot_id: string — references theme_snapshots.json
community_id: string      — references communities.json
chunk_ids: list[string]   — company-specific top evidence chunk ids from exposure.parquet
document_ids: list[string] — deduped document ids resolved from chunk_ids
```

Rules:

- Written by `POST /api/provenance/materialize` after exposure is computed.
- company_id is the Company entity_id from company_theme_exposure.parquet.company_id —
  NEVER document.company_id. A document about CompanyX that mentions CompanyY only
  appears in CompanyY's evidence group (not CompanyX's), because attribution follows
  the structural edges where CompanyY is a node.
- chunk_ids are sourced from company_theme_exposure.parquet.top_evidence_chunk_ids
  per (company_id, theme_snapshot_id, community_id) row. These are already PIT-gated
  by the exposure computation.
- Evidence groups never bleed across themes: a company spanning N themes produces
  N rows with disjoint evidence sets (per the underlying exposure rows).
- document_ids are resolved by joining chunk_ids against chunks.parquet.document_id.

### New API Endpoints (EG-E)

#### `POST /api/provenance/materialize`

Input:

```json
{"run_id": "run_20240630_120000"}
```

Output:

```json
{
  "success": true,
  "run_id": "run_20240630_120000",
  "artifacts": [
    "discovery/theme_document_evidence.parquet",
    "discovery/company_theme_document_evidence.parquet"
  ],
  "theme_rows": 12,
  "company_theme_rows": 96
}
```

Preconditions: communities.json, theme_snapshots.json, edges.parquet, chunks.parquet,
and company_theme_exposure.parquet must exist (i.e. run after exposure/compute).

#### `GET /api/themes/{run_id}/communities/{community_id}/documents`

Returns E2 provenance record for a community (one read, no graph walk):

```json
{
  "community_id": "community_017",
  "theme_snapshot_id": "theme_20240630_017",
  "as_of_date": "2024-06-30",
  "chunk_ids": ["chunk_001", "chunk_002"],
  "document_ids": ["doc_001"]
}
```

#### `GET /api/themes/{run_id}/companies/{company_id}/documents`

Returns E3 provenance records for all themes a company is exposed to (list):

```json
[
  {
    "company_id": "ent_abc123",
    "theme_snapshot_id": "theme_20240630_017",
    "community_id": "community_017",
    "as_of_date": "2024-06-30",
    "chunk_ids": ["chunk_001"],
    "document_ids": ["doc_001"]
  },
  {
    "company_id": "ent_abc123",
    "theme_snapshot_id": "theme_20240630_025",
    "community_id": "community_025",
    "as_of_date": "2024-06-30",
    "chunk_ids": ["chunk_007"],
    "document_ids": ["doc_003"]
  }
]
```

`company_id` must be a Company entity_id (ent_...). Each item is a distinct
theme evidence group; there is no cross-theme bleed.

---

## FI-C: `projected_impacts.parquet` (Workstream P-C, GitHub #106)

**New in FI-C.** One row per `(trigger_id, company_id)` pair reached by forward-inference
propagation.  Persists the in-memory output of FI-B (`propagation.propagate()`) as a
regenerable, PIT-clean discovery artifact.

Path: `data/runs/<run_id>/discovery/projected_impacts.parquet`

**Trigger selection (v1 — data-driven):** Triggers are **Event nodes** present in
`graph.json`.  Event is a first-class structural node type; Events represent discrete
episodic occurrences (e.g. "Fed rate hike March 2024") that naturally model "what happens
when THIS event activates?"  Triggers are whatever Event entities the extraction pipeline
found in the source corpus — no user input required.  All triggers are already
PIT-filtered by `graph_build.py` (`first_seen_at <= as_of_date`, fail-closed).

**Shock convention (v1):** `shock = +1.0` for all Event triggers.  `direction` on each row
reflects the NET causal sign from trigger to company (product of edge polarities along the
path).

Required columns:

```text
schema_version:       string      — always "1.0"
run_id:               string      — references run_manifest.json
as_of_date:           string      — run's as_of_date (YYYY-MM-DD); inherited from manifest
trigger_id:           string      — entity_id of the triggering Event node
trigger_kind:         string      — entity_type of trigger ("Event" for v1)
company_id:           string      — entity_id of the impacted Company node
direction:            int32       — +1 (net positive impact) or -1 (net negative impact)
strength:             float64     — abs(aggregate); ORDINAL rank only — NOT a calibrated %
path:                 list[str]   — edge_id chain of the PRIMARY path (trigger → company)
contributing_edge_ids: list[str]  — flat union of ALL edge_ids across all paths to company
evidence_chunk_ids:   list[str]   — deduped chunk_ids from all contributing edges;
                                    resolvable via source.py chunk_source(run_id, chunk_id)
confidence:           float64     — mean propagation_weight along the primary path
method:               string      — always "propagation_v1_event_trigger"
```

Rules:

- `direction` is always `+1` or `-1` (sign of the sign-aware aggregate contribution sum).
- `strength` is `abs(aggregate)` and is purely ordinal.  It is NOT a probability or
  calibrated percentage.  Two rows may be compared ordinally but not interpreted as "X%
  impact".
- `path` holds the PRIMARY path edge_ids (the first path in the canonically sorted list
  returned by `propagate()`).  Its purpose is to let the UI display WHY a company was
  impacted.
- `contributing_edge_ids` is the UNION of all paths' edge_ids.  Used together with
  `evidence_chunk_ids` for full traceability.
- `evidence_chunk_ids` are resolved by looking up each edge in `contributing_edge_ids`
  against the `edges` list in `graph.json` (the same dict returned by `graph_build.py`).
  Each `chunk_id` is resolvable via `source.chunk_source(run_id, chunk_id)`.
- **PIT-clean by construction:** `graph.json` is already PIT-filtered by `graph_build.py`
  (`first_seen_at <= as_of_date`, fail-closed).  `propagate()` also excludes edges with
  `available_at > as_of_date` when that field is present (test fixtures).
- **Derived / regenerable:** the artifact is rebuilt every run.  It is never restated;
  historical snapshots are captured by separate `run_id`s with their own `as_of_date`.
- An empty artifact (schema-valid, zero rows) is written when no Event triggers exist or
  when no trigger reaches any Company node.

Known limitation (#110):

`causes`, `exposed_to`, and `sensitive_to` edges have `base_polarity = +1`
unconditionally.  `direction` for impacts derived SOLELY from those edge types is
provisional.  Only `hurts` (−1) and `benefits` (+1) carry reliable signs today.  See
`propagation.py` module docstring for the full caveat.

### FI-C Module

Python module: `app/backend/theme_engine/projected_impacts.py`

Public API:

```python
select_triggers(graph: dict) -> list[dict]
    # Returns Event nodes from the PIT graph as trigger candidates.

compute_projected_impacts(run_id: str, shock: float = 1.0) -> int
    # Orchestrates trigger selection + propagation + artifact write.
    # Returns number of rows written.
```

---

## FI-E: `projection_scores.parquet` (Workstream P-E, GitHub #108)

**New in FI-E.** Post-freeze projection-validation pass.  For each
`(trigger_id, company_id)` row in `projected_impacts.parquet`, compares the
projected `direction` (and ordinal `strength`) against the realized
forward-window return drawn from `validation/market_prices.parquet`.

Path: `data/runs/<run_id>/validation/projection_scores.parquet`

**POST-FREEZE, ONE-WAY:** This artifact is written AFTER `discovery_frozen=True`
is confirmed.  Scores must **never** flow back into discovery-time projection
(`propagation.py` / `projected_impacts.py`).  `projection_scorer.py` must not
be imported by any discovery-stage module.

**Scoring algorithm:**

1. For each projected impact, compute `realized_return` via the same
   forward-window machinery as `validation.py` (entry = earliest price_date
   strictly after `as_of_date`; exit = latest price_date within the window;
   `available_at <= price_date` guard applied).
2. `hit = 1` if `sign(realized_return) == direction`; `hit = 0` if the signs
   disagree; `hit = null` when `realized_return == 0` or no price data.
3. `hit_rate_by_trigger` = mean(hit) over all non-null hits for the trigger.
4. `rank_corr_by_trigger` = Spearman(strength, |realized_return|) over all
   rows with non-null `realized_return` for the trigger; null when n < 2.

Required columns:

```text
schema_version:         string    — always "1.0"
run_id:                 string    — references run_manifest.json
as_of_date:             string    — run's as_of_date (YYYY-MM-DD)
trigger_id:             string    — entity_id of the triggering Event node
company_id:             string    — entity_id of the impacted Company node
direction:              int32     — +1 or -1, from projected_impacts
strength:               float64   — ordinal strength from projected_impacts
forward_window:         string    — e.g. "1M", "3M"
realized_return:        float64   — realized forward return; null if no data
hit:                    int32     — 1 (correct direction), 0 (wrong), null (no data)
hit_rate_by_trigger:    float64   — mean hit over non-null hits for this trigger
rank_corr_by_trigger:   float64   — Spearman(strength, |ret|) for this trigger
scorer_method:          string    — always "projection_scorer_v1"
caveats:                string    — one-way + illustrative-only caveat
```

Rules:

- The scorer reads `projected_impacts.parquet` post-freeze as a READ-ONLY
  discovery artifact.  It never modifies or regenerates discovery outputs.
- Realized prices are sourced exclusively from `validation/market_prices.parquet`
  (same leakage filter: `price_date > as_of_date`, `available_at <= price_date`).
- Windows without sufficient forward coverage are silently skipped (same OI-7
  coverage gate as `validation.py`).
- An empty schema-valid artifact is written when no impact rows qualify.
- `rank_corr_by_trigger` is `null` when fewer than 2 rows have price data for
  a trigger (Spearman is undefined for n < 2).

### FI-E Module

Python module: `app/backend/theme_engine/projection_scorer.py`

Public API:

```python
score_projections(run_id: str) -> dict
    # Post-freeze projection scoring pass.
    # Raises PermissionError if discovery_frozen != True.
    # Returns dict with keys: success, scored_rows, windows_scored,
    #   artifacts, message.
```
