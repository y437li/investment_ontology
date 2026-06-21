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
| L2 | Structured discovery artifacts | Extraction Agent, Graph Theme Agent | entities, aliases, edges, graph, communities, theme snapshots, document-theme affinity | `entities.parquet`, `edges.parquet`, `graph.json`, `theme_snapshots.json`, `document_theme_affinity.parquet` |
| L3 | Structured validation artifacts | Data Engineering Agent, Validation Agent | prices, fundamentals, baskets, validation metrics | `market_prices.parquet`, `fundamentals.parquet`, `portfolio_baskets.parquet`, `validation.csv` |

Rule:

- No stage may skip from L0 raw files directly to L2 extraction.
- L2 discovery artifacts must be frozen before L3 validation reads future outcomes.

## 2. Physical Storage Layout (structured vs. non-structured)

- Non-structured raw corpus (`L0` inputs): `data/inputs/documents/` and `data/inputs/documents/source_manifest.csv` only.
- Structured discovery outputs (`L1`-`L2`): `data/runs/<run_id>/discovery/`.
- Structured validation outputs (`L3`): `data/runs/<run_id>/validation/`.
- Run control file: `data/runs/<run_id>/run_manifest.json`.
- Sweep control file: `data/runs/<sweep_id>/sweep_manifest.json`.

Do not place structured artifacts under `data/inputs/`.  
Do not place non-structured raw document files under `data/runs/` (pointers only in parquet).

Collection stage input convention:

- Source collection spec CSV rows include metadata for each incoming source and one of:
  - `source_file` (local file path, resolved relative to the spec location), or
  - `source_url` (HTTP/HTTPS fetch source).
- Collect writes immutable source files into `data/inputs/documents/...` and writes
  `source_manifest.csv` as the handoff to `/api/data/import`.

## 3. L0 Raw Unstructured Input Standard

Raw documents live under:

```text
data/inputs/documents/
```

Recommended physical layout:

```text
data/inputs/documents/
  <provider>/
    <source_id>/
      <source_vintage>/
        <YYYY>/<MM>/<DD>/...raw_files...
```

Examples:

- `data/inputs/documents/sec/0000320193/2024-06/v1/10-k-2024q2.pdf`
- `data/inputs/documents/rss/tsla-earnings/2025-01-15/tsla_earnings.md`

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
- Raw files are immutable by policy: new restatements must be stored as new `source_vintage` paths, never by overwriting.

Storage split convention for non-structured data:

- Source documents: `data/inputs/documents/` (or mounted object-store mirror in production).
- Source manifest: `data/inputs/documents/source_manifest.csv`.
- Run-level references: keep only metadata/pointers in `discovery/raw_documents.parquet` (`raw_path`, `source_vintage`, `raw_content_hash`), not duplicated binary payloads under `data/runs`.

Acquisition standard (the fetch step that produces L0, owned by the Data Engineer; see spec section 6):

- Every fetched record carries `published_at`, `available_at`, and `vintage`.
- Structured inputs (prices, fundamentals, macro) carry the same point-in-time stamps; fundamentals use as-reported values only, never live or restated figures.
- Universe membership is recorded per `as_of_date` to avoid survivorship bias.
- Acquisition is deterministic: the same source and window reproduce the same records and `content_hash`.

## 4. L1 Cleaning Standard

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

## 5. L2 Structured Discovery Standard

Extraction converts cleaned chunks into structured discovery data:

```text
chunks.parquet
-> entities.parquet
-> entity_aliases.parquet
-> edges.parquet
-> edge_explanations.parquet
-> document_theme_affinity.parquet
```

Rules:

- Every non-trivial entity or edge should keep evidence references.
- Entity ids and edge ids must be stable for the same run input and config.
- `theme_name` is interpretation metadata, not a discovery input.
- Low-confidence records should be included with confidence fields or routed to review, not silently dropped.

## 6. L3 Structured Validation Standard

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

## 7. Agent Handoff Order

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

## 8. Quality Gates

Before extraction starts:

- `raw_documents.parquet` exists.
- `documents.parquet` exists.
- `document_cleaning_log.parquet` exists.
- `chunks.parquet` exists.
- Every included document has `available_at <= as_of_date`.
- Every chunk has a non-empty `text` field and a valid `document_id`.

Before validation starts:

- Discovery artifacts are frozen.
- Document-theme affinity can be optional in early runs; if produced, include `document_theme_affinity.parquet`.
- `company_theme_exposure.parquet` exists.
- Validation data is loaded only after freeze.
- Basket construction is documented in `portfolio_baskets.parquet`.
