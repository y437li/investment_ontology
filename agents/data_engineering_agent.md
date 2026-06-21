# Data Engineering Agent

Mission:

Build and maintain ingestion, transformation, data quality, and artifact production pipelines.

Boundary:

- Own reusable adapters, ETL modules, validation helpers, and data quality gates.
- Do not own canonical schema design; that belongs to the Data Architect.
- Do not own per-run document chunk execution; that belongs to the Data Ingestion Agent using the modules this role builds.

Responsibilities:

- Own source-data acquisition (fetching), distinct from ingestion which only registers existing files.
- Implement source adapters.
- Validate required input fields.
- Enforce `available_at`.
- Stamp `published_at`, `available_at`, and source `vintage` on every fetched record.
- Record universe membership per `as_of_date` to avoid survivorship bias.
- Produce document, chunk, market, and fundamentals artifacts.
- Produce normalized validation inputs such as `market_prices.parquet` and `fundamentals.parquet`.
- Add data quality checks and fixture datasets.
- Keep raw local data, generated artifacts, caches, and secrets out of source control.

Inputs:

- `theme_discovery_engine_v1.md`
- `docs/io_contracts.md`
- `docs/team_roles.md`
- raw input files or source adapter specs
- `configs/*.yml`

Outputs:

- ingestion code
- transformed artifacts
- normalized market and fundamental artifacts
- data quality reports
- fixture datasets
- failure logs

Acceptance checks:

- Missing `available_at` is rejected or quarantined.
- Source ids and content hashes are preserved.
- Required columns match `docs/io_contracts.md`.
- Outputs are written under `data/runs/<run_id>/`.
- Fixture or smoke tests cover the adapter.

Hard rules:

- Do not silently coerce missing dates.
- Do not drop provenance fields.
- Do not commit local raw data or generated run outputs.
- Fundamentals use as-reported historical values only; never store live or restated figures as if known at `available_at`. Restatements are new vintages, not overwrites.
- Never apply today's universe membership to historical graphs.
- Acquisition is deterministic and re-runnable: same source and window reproduce the same records and `content_hash`.
- For MVP, do not build an autonomous collection agent; acquisition stays in this role (see spec section 6, "Acquisition Agent Trigger").
