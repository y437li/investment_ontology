# Data Ingestion Agent

Mission:

Convert source materials into point-in-time raw document records.

Boundary:

- Execute the per-run raw document import and text extraction stage.
- Use Data Engineering adapters and validators where available.
- Do not clean text, chunk documents, define canonical schemas, market data adapters, or validation data logic.

Inputs:

- Raw PDF, MD, TXT, HTML-like export, CSV, or curated text files.
- `source_manifest.csv`
- `configs/pipeline.example.yml`
- `configs/universe.example.yml`

Outputs:

- `raw_documents.parquet`
- ingestion warnings

Acceptance checks:

- Every raw document has `available_at`.
- Future documents are excluded for the run `as_of_date`.
- Duplicate content is detected by hash.
- Raw files are not overwritten.

Source Vintage Rule (OI-8) — enforced at ingest:

- `available_at` = the source's **publication timestamp**, authoritative by type:
  - Filings: `filing_date` (the date the document became public on EDGAR/SEDAR+; NOT the period end date).
  - News / press releases: the article `published_at` date.
  - Prices / fundamentals: the as-reported publication date.
- Ingest is **read-only** on this field: it reads the source's publish timestamp from the manifest and stamps `available_at`; it never invents, defaults to the current time, or shifts the value.
- A source with **no determinable publish time** (i.e., `published_at` or `available_at` is absent or undeterminable) is **quarantined (fail-closed)** — not admitted with a guessed or default date. Quarantine reason: `no_determinable_publish_time`.
- `available_at` is set **once at ingest** and is **immutable downstream**. No later stage may alter it. The `ingested_at` column records the import clock time; it must never substitute for `available_at`.
