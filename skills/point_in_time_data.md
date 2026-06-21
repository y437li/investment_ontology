# Skill: Point-in-Time Data

Purpose:

Keep discovery data historically correct.

Inputs:

- raw documents.
- `source_manifest.csv`.
- `as_of_date`.
- universe config.

Steps:

1. Require `published_at` and `available_at`.
2. Reject or quarantine missing `available_at`.
3. Exclude documents with `available_at > as_of_date`.
4. Write `raw_documents.parquet`.
5. Hand off to Data Cleaning Agent for `documents.parquet`, `document_cleaning_log.parquet`, and `chunks.parquet`.

Outputs:

- `raw_documents.parquet`
- `documents.parquet`
- `document_cleaning_log.parquet`
- `chunks.parquet`

Failure modes:

- Using `period_end` instead of `available_at`.
- Letting revised future documents enter discovery.
- Losing document ids during chunking.
- Running extraction on raw files instead of cleaned chunks.
