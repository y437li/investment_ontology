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
