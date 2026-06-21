# Skill: Unstructured Data Cleaning

Purpose:

Turn raw unstructured source files into audited, extraction-ready documents and chunks.

Use when:

- Implementing document cleaning or chunking code.
- Reviewing whether raw documents are ready for extraction.
- Debugging missing `available_at`, duplicate documents, OCR noise, or malformed chunks.

Inputs:

- `docs/data_schema.md`
- `docs/io_contracts.md`
- `raw_documents.parquet`
- raw extracted text files
- `configs/pipeline.example.yml`

Steps:

1. Validate required raw document metadata.
2. Reject or quarantine missing `available_at`.
3. Exclude future documents where `available_at > as_of_date`.
4. Normalize text with deterministic rules only.
5. Preserve source spans, page references, hashes, and provenance.
6. Write `documents.parquet`.
7. Write `document_cleaning_log.parquet`.
8. Chunk cleaned documents and write `chunks.parquet`.

Outputs:

- `documents.parquet`
- `document_cleaning_log.parquet`
- `chunks.parquet`
- cleaning warnings

Acceptance checks:

- Raw files are not modified.
- Every cleaned document links to `raw_document_id`.
- Every chunk links to `document_id`.
- Cleaning log explains quarantined records and material text changes.
- Text is not summarized, translated, or semantically rewritten.

Failure modes:

- Treating OCR guesses as source facts.
- Silently inventing `available_at`.
- Running extraction directly on raw files.
- Dropping negative or contradictory evidence during cleaning.
