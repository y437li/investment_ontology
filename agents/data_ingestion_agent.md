# Data Ingestion Agent

Mission:

Convert source materials into point-in-time documents and chunks.

Inputs:

- Raw PDF, MD, TXT, CSV, or curated text files.
- `configs/pipeline.example.yml`
- `configs/universe.example.yml`

Outputs:

- `documents.parquet`
- `chunks.parquet`
- ingestion warnings

Acceptance checks:

- Every document has `available_at`.
- Future documents are excluded for the run `as_of_date`.
- Duplicate content is detected by hash.
- Chunks retain document and time metadata.

