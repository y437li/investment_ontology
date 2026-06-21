# Team Roles

This project needs explicit data architecture and data engineering ownership. The MVP can combine roles in one person, but the responsibilities must not disappear.

## MVP Team Shape

Minimum viable team:

| Role | Required for MVP | Can Be Combined? | Core Ownership |
|---|---:|---:|---|
| Product / Research Lead | Yes | No | Research question, universe, validation interpretation |
| Data Architect | Yes | Yes, with Data Engineer for MVP | Data model, schema versioning, lineage, point-in-time rules |
| Data Engineer | Yes | Yes, with Data Architect for MVP | Ingestion, adapters, ETL, data quality, artifact production |
| Research / Quant Engineer | Yes | Sometimes | Graph metrics, exposure, validation, benchmarks |
| Full-stack Engineer | Yes | Sometimes | Workflow UI, backend APIs, artifact inspection |
| LLM / Extraction Engineer | Yes | Sometimes | Structured extraction, entity resolution, model/cost controls |

For a small demo, one strong engineer may cover Full-stack + LLM + some pipeline work, and one data-minded engineer may cover Data Architect + Data Engineer. For anything beyond the demo, keep Data Architect and Data Engineer as separate accountable roles.

## Data Architect

Mission:

Own the shape and meaning of data across the platform.

Responsibilities:

- Define canonical entity, edge, document, theme, exposure, and validation schemas.
- Maintain `docs/io_contracts.md`.
- Maintain schema versioning and migration rules.
- Define point-in-time data semantics, especially `available_at`.
- Define lineage and provenance requirements.
- Decide which fields belong in configs, manifests, artifacts, or source records.
- Design the migration path from DuckDB/Parquet to PostgreSQL/pgvector/S3 when needed.
- Review any change that modifies artifact contracts or data semantics.

Key deliverables:

- Schema contracts.
- Data dictionary.
- Lineage and provenance rules.
- Versioning policy.
- Storage architecture decisions.

Non-negotiable standards:

- No dataset enters discovery without `available_at`.
- No validation data is available before discovery artifacts are frozen.
- No artifact contract changes without documentation and tests.

## Data Engineer

Mission:

Build and operate the data movement and data quality layer.

Responsibilities:

- Implement document, market, and fundamental data ingestion.
- Build source adapters for manual files first, then SEDAR/EDGAR/news/market APIs later.
- Produce `documents.parquet`, `chunks.parquet`, market inputs, and fundamentals inputs.
- Validate required columns and types.
- Deduplicate source records using content hashes or source ids.
- Maintain raw-to-artifact transformation scripts.
- Add data quality checks and failure reports.
- Keep local data, caches, generated outputs, and secrets out of source control.

Key deliverables:

- Ingestion pipelines.
- Source adapters.
- Data quality checks.
- Fixture datasets.
- Data run logs.

Non-negotiable standards:

- Data pipeline failures must be explicit.
- Missing `available_at` must reject or quarantine records.
- Source ids and content hashes must be retained.
- Generated artifacts must stay under `data/runs/<run_id>/`.

## Boundary Between Data Architect and Data Engineer

| Area | Data Architect | Data Engineer |
|---|---|---|
| Schema | Defines canonical contract | Implements contract |
| `available_at` semantics | Defines rule | Enforces rule |
| Source adapter | Defines expected output | Builds adapter |
| Data quality | Defines gates | Implements checks |
| Storage | Chooses architecture | Operates storage/pipeline |
| Migration | Designs migration path | Executes migration |
| Validation leakage | Defines boundary | Enforces in pipeline |

## Scale-Up Trigger

Split Data Architect and Data Engineer into separate people when any of these happen:

- More than 100 companies.
- More than 24 months of monthly snapshots.
- More than three source families.
- Automated external data feeds.
- PostgreSQL/S3 migration.
- Multiple contributors writing pipeline code.
- Any backtest intended to support a research conclusion.

## Review Rules

Data Architect review is required for:

- changes to `docs/io_contracts.md`
- new artifact columns
- removed artifact columns
- schema version changes
- new source families
- new point-in-time semantics
- storage migration

Data Engineer review is required for:

- new ingestion code
- source adapter changes
- data quality checks
- fixture data changes
- run artifact write behavior

