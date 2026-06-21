# Skill: Pipeline and Artifact Implementation

Purpose:

Implement reusable pipeline stages that transform inputs into versioned artifacts.

Use when:

- Building ingestion, chunking, extraction, graph, community, exposure, or validation code.
- Adding artifact schemas or run manifest handling.
- Making pipeline stages reproducible.

Inputs:

- `theme_discovery_engine_v1.md`
- `docs/io_contracts.md`
- `configs/pipeline.example.yml`
- `configs/validation.example.yml`
- upstream artifacts from `data/runs/<run_id>/`.

Steps:

1. Define input artifacts and output artifacts explicitly.
2. Load config once and snapshot it into `run_manifest.json`.
3. Add `schema_version`, `pipeline_version`, `created_at`, `as_of_date`, and input hashes where practical.
4. Make each stage idempotent or clearly document overwrite behavior.
5. Validate required columns before processing.
6. Write artifacts atomically where possible.
7. Add small fixture-based tests.

Outputs:

- pipeline module code.
- artifact writer/reader helpers.
- schema validation helpers.
- fixture-based tests.

Acceptance checks:

- Stage can run on a small fixture.
- Missing required columns fail with clear errors.
- Artifacts are written under `data/runs/<run_id>/`.
- Re-running the same fixture is deterministic unless randomness is explicitly seeded.
- Output artifacts match the spec in `theme_discovery_engine_v1.md`.

Failure modes:

- One-off scripts with hardcoded paths.
- Artifacts without schema/version metadata.
- Silent overwrite of prior run outputs.
- Non-deterministic graph or validation output without a seed.
