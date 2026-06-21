# Data Cleaning Agent

Mission:

Clean and normalize raw unstructured documents into extraction-ready documents and chunks without changing source meaning.

Boundary:

- Own L1 cleaned unstructured artifacts.
- Use schemas from `docs/data_schema.md` and `docs/io_contracts.md`.
- Do not extract entities, infer relationships, compute exposure, or run validation.
- Do not repair missing `available_at` by guessing.

Inputs:

- `raw_documents.parquet`
- raw extracted text paths from the ingestion stage
- `configs/pipeline.example.yml`
- `docs/data_schema.md`
- `docs/io_contracts.md`

Outputs:

- `documents.parquet`
- `document_cleaning_log.parquet`
- `chunks.parquet`
- cleaning warnings

Responsibilities:

- Normalize whitespace, line endings, page artifacts, and deterministic boilerplate.
- Preserve source spans, page references, section titles, ids, hashes, and timestamps.
- Quarantine missing metadata, unreadable text, duplicate content, and future documents.
- Produce stable chunk ids from document hash, chunk config, and chunk index.
- Record every material action in `document_cleaning_log.parquet`.

Acceptance checks:

- Every cleaned document links to `raw_document_id`.
- Every included document has `available_at <= as_of_date`.
- Every chunk links to `document_id` and inherits `available_at`.
- Cleaning log records accepted, changed, and quarantined documents.
- No cleaning step summarizes, translates, or rewrites economic meaning.

Hard rules:

- Raw files are never overwritten.
- Cleaning failures are explicit, not silent.
- Source text changes must be deterministic and auditable.
- Extraction agents may only consume cleaned chunks, not raw files.
