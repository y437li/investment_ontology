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
| L3 | Structured validation artifacts | Data Engineering Agent, Validation Agent | prices, fundamentals, baskets, validation metrics | `market_prices.parquet`, `fundamentals.parquet`, `portfolio_baskets.parquet`, `validation.csv` |

Rule:

- No stage may skip from L0 raw files directly to L2 extraction.
- L2 discovery artifacts must be frozen before L3 validation reads future outcomes.

## 2. L0 Raw Unstructured Input Standard

Raw documents live under:

```text
data/inputs/documents/
```

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
language
source_url
license
confidentiality
notes
```

Rules:

- `raw_path` must be relative to the input root.
- `available_at` is mandatory and must reflect when the document was knowable to the market, not only the document's period end.
- `source_id` and `raw_path` must be stable enough for deduplication.
- Raw files are local inputs and must not be committed to Git.

## 3. L1 Cleaning Standard

The Data Cleaning Agent turns L0 records into cleaned unstructured artifacts. It may normalize formatting, but it must not rewrite meaning.

Allowed cleaning actions:

- Extract text from PDF, MD, TXT, or HTML-like exports.
- Normalize line endings and whitespace.
- Remove repeated page headers, footers, and page numbers when detected by deterministic rules.
- Preserve section titles, page references, and source spans where available.
- Quarantine unreadable files, missing metadata, duplicates, and future documents.

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

## 5. L3 Structured Validation Standard

Validation data is structured and intentionally separated from discovery data:

```text
market_prices.parquet
fundamentals.parquet
portfolio_baskets.parquet
validation.csv
```

Rules:

- Discovery stages must not read `market_prices.parquet`, `fundamentals.parquet`, `portfolio_baskets.parquet`, or `validation.csv`.
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
