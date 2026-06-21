# Skill: Point-in-Time Data

Purpose:

Keep discovery data historically correct.

Inputs:

- raw documents.
- `as_of_date`.
- universe config.

Steps:

1. Require `published_at` and `available_at`.
2. Reject or quarantine missing `available_at`.
3. Exclude documents with `available_at > as_of_date`.
4. Write `documents.parquet`.
5. Chunk included documents and write `chunks.parquet`.

Outputs:

- `documents.parquet`
- `chunks.parquet`

Failure modes:

- Using `period_end` instead of `available_at`.
- Letting revised future documents enter discovery.
- Losing document ids during chunking.

