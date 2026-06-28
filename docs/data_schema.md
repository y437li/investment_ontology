# Data Schema and Cleaning Standard

This document defines the data shape before `docs/io_contracts.md` gets into per-artifact field contracts. The order is intentional:

```text
Raw unstructured inputs
-> cleaned unstructured artifacts
-> structured discovery artifacts
-> structured validation artifacts
```

MiroFish can inform the upload and task-status workflow, but it does not define this project's data schema.

## 1. Layer Model

| Layer | Data Type | Owner | Examples | Canonical Output |
|---|---|---|---|---|
| L0 | Raw unstructured inputs | Data Ingestion Agent | PDF, MD, TXT, HTML exports, transcript files | `raw_documents.parquet` |
| L1 | Cleaned unstructured artifacts | Data Cleaning Agent | normalized text, document metadata, chunks, cleaning log | `documents.parquet`, `document_cleaning_log.parquet`, `chunks.parquet` |
| L2 | Structured discovery artifacts | Extraction Agent, Graph Theme Agent | entities, aliases, edges, graph, communities, theme snapshots | `entities.parquet`, `edges.parquet`, `graph.json`, `theme_snapshots.json` |
| L2-P | Provenance reverse-join artifacts (EG-E) | Provenance Agent | entity-chunk-document linkage; theme->documents; (company,theme)->documents | `entity_chunk_provenance.parquet`, `theme_document_evidence.parquet`, `company_theme_document_evidence.parquet` |
| L3 | Structured validation artifacts | Data Engineering Agent, Validation Agent | prices, fundamentals, baskets, validation metrics | `market_prices.parquet`, `fundamentals.parquet`, `portfolio_baskets.parquet`, `validation.csv` |

Rule:

- No stage may skip from L0 raw files directly to L2 extraction.
- L2 discovery artifacts must be frozen before L3 validation reads future outcomes.
- L2-P provenance artifacts are computed from L2 artifacts and must be regenerated if upstream L2 artifacts change.

### EG-E Provenance Layer (L2-P)

EG-E materializes three reverse-join artifacts that answer provenance questions in
one read, without requiring a client-side graph walk:

| Artifact | Key | What it answers |
|---|---|---|
| `entity_chunk_provenance.parquet` | `(entity_id, chunk_id)` | Which document + subject company produced each entity mention? (E1) |
| `theme_document_evidence.parquet` | `community_id` | Which documents form the evidence base for a theme? (E2) |
| `company_theme_document_evidence.parquet` | `(company_id, theme_snapshot_id, community_id)` | Which documents back a specific company's exposure to a specific theme? (E3) |

Critical correctness rule for E3:
- `company_id` is the Company ENTITY id, not `document.company_id`.
- A news article about CompanyX that mentions CompanyY only appears in CompanyY's
  evidence group (because the structural edge that causes CompanyY's exposure was
  extracted from that chunk with CompanyY as an endpoint).
- Using `document.company_id` for this attribution would be wrong.

## 2. L0 Raw Unstructured Input Standard

Raw documents live under:

```text
data/inputs/documents/
```

Source-time metadata is required for every ingest row in L0/L1/L3 adapters:

- `published_at`: original publication timestamp from source.
- `available_at`: first date the information was available to the market.
- `source_id`: stable source native identifier.
- `vintage`: ingest batch id or upstream revision/release timestamp.
- `ingested_at`: local pipeline ingestion timestamp.
- `as_of_date` (for run snapshots): market date used for point-in-time point selection.

Each batch must include a source manifest:

```text
data/inputs/documents/source_manifest.csv
```

Required manifest columns:

```text
source
source_id
raw_path
title
document_type
company_id
published_at
available_at
vintage
language
source_url
license
confidentiality
notes
```

Rules:

- `raw_path` must be relative to the input root.
- `available_at` is mandatory and must reflect when the document was knowable to the market, not only the document's period end.
- `vintage` records the as-of moment of the retrieved version of the source (ingestion timestamp and/or source release batch version), so later restatements are stored as new vintages rather than overwrites, enabling reproducible replay.
- `source_id` and `raw_path` must be stable enough for deduplication.
- Raw files are local inputs and must not be committed to Git.

Acquisition standard (the fetch step that produces L0, owned by the Data Engineer; see spec section 6):

- Every fetched record carries `published_at`, `available_at`, and `vintage`.
- Structured inputs (prices, fundamentals, macro) carry the same point-in-time stamps; fundamentals use as-reported values only, never live or restated figures.
- Universe membership is recorded per `as_of_date` to avoid survivorship bias.
- Acquisition is deterministic: the same source and window reproduce the same records and `content_hash`.

## 3. L1 Cleaning Standard

The Data Cleaning Agent turns L0 records into cleaned unstructured artifacts. It may normalize formatting, but it must not rewrite meaning.

Allowed cleaning actions:

- Extract text from PDF, MD, TXT, or HTML-like exports.
- Normalize line endings and whitespace.
- Remove repeated page headers, footers, and page numbers when detected by deterministic rules.
- Preserve section titles, page references, and source spans where available.
- Quarantine unreadable files, missing metadata, duplicates, and future documents.
- Detect and preserve table structure: HTML `<table>` elements and ASCII
  pipe-delimited tables are converted to ``[[[TABLE_DATA:{json}]]]`` markers
  embedded in the cleaned text so downstream chunking can recover the cell grid.
- Detect and preserve section headings: HTML `<h1>`–`<h6>` tags are converted
  to ``[[[SECTION_TITLE:text]]]`` markers so chunking can populate `section_title`.

Forbidden cleaning actions:

- Do not summarize source text into replacement text.
- Do not infer missing `available_at`.
- Do not merge different source documents into one canonical document.
- Do not remove negative, contradictory, or low-confidence evidence.
- Do not silently translate or paraphrase source text.

Required L1 artifacts:

```text
raw_documents.parquet
documents.parquet
document_cleaning_log.parquet
chunks.parquet
```

Cleaning log requirements:

- Every material cleaning action must be logged.
- Every quarantined document must have a reason.
- Each cleaned document must link back to `raw_document_id`.
- Each chunk must link back to `document_id` and inherit `available_at`.

## 4. L2 Structured Discovery Standard

Extraction converts cleaned chunks into structured discovery data:

```text
chunks.parquet
-> entities.parquet
-> entity_aliases.parquet
-> edges.parquet
-> edge_explanations.parquet
```

Rules:

- Every non-trivial entity or edge should keep evidence references.
- Entity ids and edge ids must be stable for the same run input and config.
- `theme_name` is interpretation metadata, not a discovery input.
- Low-confidence records should be included with confidence fields or routed to review, not silently dropped.

## 4a. As-Reported Fundamentals (Discovery-time, EG-B1)

The XBRL ingestion adapter (`fundamentals_adapter.py`) writes a **discovery-time**
as-reported fundamentals artifact that is PIT-clean by construction:

```text
discovery/fundamentals_asreported.parquet
```

Schema: `(company_id, period_end, metric_name, metric_value, unit, currency,
filing_date, available_at, source, source_id)`.

Universe: S&P/TSX 60 (Canadian companies; currency CAD; country Canada).
- Companies are identified by their `tsx_ticker` (e.g. `"RY.TO"`).
- Cross-listed filers are fetched from EDGAR using `sec_cik` from
  `configs/universe.tsx60.yml`. Companies with `sec_cik=null` (e.g. Hydro One,
  Constellation Software) have no EDGAR data and receive an empty artifact.

Taxonomy: IFRS (`ifrs-full` namespace) is **primary** for Canadian cross-filers.
US-GAAP (`us-gaap`) is searched as a **fallback**. IFRS concepts mapped:
- `Revenue` -> revenue, `ProfitLoss` -> net_income
- `BasicEarningsLossPerShare` -> eps
- `CashFlowsFromUsedInOperatingActivities` -> operating_cash_flow
- `LongtermBorrowings` -> total_debt
- `GrossProfit` / `Revenue` -> gross_margin (derived)
- `ProfitFromOperatingActivities` / `Revenue` -> operating_margin (derived)

Currency rules:
- `currency` is read from the XBRL unit string; never assumed.
- `"CAD"` -> `"CAD"`. `"CAD/shares"` (EPS) -> currency `"CAD"`, unit `"CAD/shares"`.
- Ratio metrics have `currency=null`, `unit="ratio"`.

Other rules:
- `available_at = filing_date` (first public date; never `period_end`).
- Metric names drawn exclusively from `configs/fundamentals.yml`.
- Empty-but-schema-valid when a company has no XBRL (`sec_cik=null` or file absent).
- Arrow schema is pinned on write so all-None columns (e.g. `currency` for ratio
  rows) remain `string` type rather than being inferred as `null`.
- **Not** a substitute for, and never overwriting, the §20 `validation/fundamentals.parquet`.

See `docs/io_contracts.md §20a` for the full field contract.

## 4c. LM Tone Scoring (Discovery-time, SENT-A, GitHub #99)

**New in SENT-A.** The LM tone scorer reads `chunks.parquet` (joined with
`documents.parquet` for `document_type`) and writes:

```text
discovery/chunk_tone.parquet  — LM tone vector per chunk
```

**Layer:** L2 discovery artifact. Substrate for SENT-B (company-level aggregation)
and SENT-C (temporal trending).

**Lexicon:** Loughran-McDonald (2011) Master Dictionary. Committed subset at
`data/lexicons/loughran_mcdonald.csv`; replace with the full dictionary for
production (loader is drop-in compatible).

**chunk_tone.parquet schema:**

```text
chunk_id:             string       — join key to chunks.parquet
document_id:          string       — join key to documents.parquet
available_at:         string       — YYYY-MM-DD; inherited from chunk
speaker_role:         string       — "management" | "analyst" | "media" | "unknown"
token_count:          int          — denominator for normalisation
tone_positive:        float        — positive_count / token_count
tone_negative:        float        — negative_count / token_count
tone_uncertainty:     float        — uncertainty_count / token_count
tone_litigious:       float        — litigious_count / token_count
tone_strong_modal:    float        — strong_modal_count / token_count
tone_weak_modal:      float        — weak_modal_count / token_count
matched_positive:     list[str]    — matched token list (auditability)
matched_negative:     list[str]
matched_uncertainty:  list[str]
matched_litigious:    list[str]
matched_strong_modal: list[str]
matched_weak_modal:   list[str]
```

**Config:** Category list and speaker-role attribution rules are in
`configs/sentiment.yml` — not hardcoded in the scorer.

**Finance-neutral terms:** "liability", "cost", "depreciation" score 0 on the
negative category (proving LM, not Harvard-IV GI).

See `docs/io_contracts.md §S-A` for the full field contract.

## 4b. LLM Quantified-Fact Extraction (Discovery-time, EG-B2)

The LLM fact-extraction pass (`run_fact_extraction`) reads `chunks.parquet` and
writes two additional discovery artifacts:

```text
discovery/financial_metrics.parquet       — FinancialMetric nodes
discovery/financial_metric_edges.parquet  — Company -> FinancialMetric edges
```

**financial_metrics.parquet schema:**
`(schema_version, metric_id, company_id, metric_name, value, unit, period,
direction, is_guidance, confidence, evidence_chunk_id, source, created_at)`

**financial_metric_edges.parquet schema:**
`(schema_version, edge_id, company_entity_id, metric_id, edge_type,
evidence_chunk_ids, confidence, created_at)`

`edge_type` values: `"reports"` (as-reported, `is_guidance=False`) and
`"guides_to"` (management guidance, `is_guidance=True`).

**PIT rule:** Only chunks with `available_at <= run.as_of_date` are processed.
Chunks that are future-dated relative to the run's as_of_date are skipped before
extraction begins; no claims from those chunks appear in the output.

**Reconciliation with B1:**
- `metric_name` must be in `configs/fundamentals.yml` whitelist (shared contract
  with B1).
- For as-reported overlaps (`is_guidance=False`), B1 XBRL values win: if
  `fundamentals_asreported.parquet` has a row for the same
  `(company_id, period_end, metric_name)`, the LLM claim is dropped.
- Period matching normalizes LLM free-text periods to ISO calendar-quarter-end
  dates before the B1 lookup (e.g. "Q2 2024" -> "2024-06-30").
- Guidance claims (`is_guidance=True`) are always kept — B2 owns guidance
  exclusively.

**Evidence requirement:** Every `financial_metrics` row must carry a non-empty
`evidence_chunk_id`. Claims without evidence are dropped before writing.

See `docs/io_contracts.md §20b` and `§20c` for the full field contracts.

## 5. L3 Structured Validation Standard

Validation data is structured and intentionally separated from discovery data:

```text
market_prices.parquet
fundamentals.parquet        ← validation-only (§20); discovery must not read this
portfolio_baskets.parquet
validation.csv
```

Rules:

- Discovery stages must not read `market_prices.parquet`, `fundamentals.parquet`, `portfolio_baskets.parquet`, or `validation.csv`.
- The discovery-time `fundamentals_asreported.parquet` (§20a) is a **separate** artifact; it is not the same as `validation/fundamentals.parquet`.
- Exposure must be computed before validation loads future market or fundamental outcomes.
- `portfolio_baskets.parquet` must preserve constituents, weights, and selection rules so validation can be reproduced.

## 6. Agent Handoff Order

```text
Data Ingestion Agent
-> Data Cleaning Agent
-> Extraction Agent
-> Graph Theme Agent
-> Validation Agent
-> Frontend Report Agent
```

Minimum handoff fields:

```text
Input layer:
Output layer:
Artifacts read:
Artifacts written:
Records accepted:
Records quarantined:
Cleaning or validation rules applied:
Known caveats:
Next agent:
```

## 7. Quality Gates

Before extraction starts:

- `raw_documents.parquet` exists.
- `documents.parquet` exists.
- `document_cleaning_log.parquet` exists.
- `chunks.parquet` exists.
- Every included document has `available_at <= as_of_date`.
- Every chunk has a non-empty `text` field and a valid `document_id`.

Before validation starts:

- Discovery artifacts are frozen.
- `company_theme_exposure.parquet` exists.
- Validation data is loaded only after freeze.
- Basket construction is documented in `portfolio_baskets.parquet`.
