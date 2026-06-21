# Skill: Backend API Implementation

Purpose:

Implement backend endpoints that expose run creation, raw data import, data cleaning, chunking, extraction, graph build, theme discovery, exposure, discovery freeze, validation, report generation, and artifact reads.

Use when:

- Adding or modifying backend routes.
- Wiring pipeline steps to HTTP APIs.
- Implementing run status and artifact retrieval.

Inputs:

- `theme_discovery_engine_v1.md`
- `docs/io_contracts.md`
- `configs/*.yml`
- existing backend code under `app/backend/` or a MiroFish-derived Flask backend.
- expected artifacts under `data/runs/<run_id>/`.

Steps:

1. Define the request and response schema before writing route logic.
2. Validate `run_id`, `as_of_date`, config paths, and required input files.
3. Keep routes thin; put work in service or pipeline modules.
4. Write artifacts to `data/runs/<run_id>/`.
5. Return task status and artifact paths, not large raw payloads by default.
6. Surface errors with actionable messages and stage names.
7. Add or update smoke tests for each endpoint.
8. If adapting MiroFish, keep the thin route and task-status patterns but rename routes and services to this project's stage names.

Outputs:

- backend route code.
- service or pipeline module code.
- API smoke tests.
- updated docs or index entries if new files are added.

Acceptance checks:

- API inputs are schema-validated.
- Missing `available_at` or missing `as_of_date` fails early.
- Route handlers do not embed long pipeline logic.
- Endpoint outputs point to artifacts.
- Errors include run id and pipeline stage.
- Tests cover success and one failure case.

Failure modes:

- Putting all logic directly inside route handlers.
- Returning unsupported report conclusions from the API.
- Reading validation data before discovery artifacts are frozen.
- Writing artifacts outside the run directory.
- Copying MiroFish simulation route names into the core API.
