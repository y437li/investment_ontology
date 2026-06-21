# Skill: Test and Quality Gate

Purpose:

Add the minimum tests and checks needed before treating an implementation change as acceptable.

Use when:

- Completing backend, frontend, or pipeline code changes.
- Adding new artifacts or configs.
- Preparing a demo run.

Inputs:

- changed files.
- expected artifacts.
- sample fixture data.
- `docs/implementation_checklist.md`.
- `docs/io_contracts.md`.

Steps:

1. Identify the smallest meaningful test for the changed behavior.
2. Add fixture data if needed.
3. Test leakage-sensitive rules explicitly.
4. Test artifact existence and required columns.
5. Run backend tests or targeted smoke checks.
6. Run frontend build if UI changed.
7. Record commands and results in the final handoff.

Outputs:

- tests.
- fixtures.
- smoke scripts if useful.
- verification notes.

Acceptance checks:

- `available_at` filtering is tested when ingestion changes.
- Artifact schemas are tested when pipeline code changes.
- Validation freeze rule is tested when validation changes.
- Frontend build passes when UI changes.
- Failure messages are actionable.

Failure modes:

- Only testing happy-path LLM output.
- Using future data in fixtures without marking it.
- Skipping frontend build after route/component changes.
- Reporting tests passed without actually running them.
