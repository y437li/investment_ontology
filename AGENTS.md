# AGENTS.md

This repository uses Codex role-based execution. If `AGENTS.md` conflicts with local issue docs, this file is the local baseline for task behavior and contribution style.

## 1) Team execution model

- Use repository issues/PR as the upstream plan source.
- Internal execution ownership is by role (not GitHub usernames) unless explicit assignees are added.
- Roles defined for this repo:
  - `agent-doc-logic`
  - `agent-doc-validation`
  - `agent-doc-graph`
  - `agent-doc-architecture`
  - `agent-doc-extraction`
  - `agent-doc-data-engineering`
  - `agent-doc-issues`
  - `agent-doc-index`

- Priority for execution decisions:
  1. GitHub issue/PR state
  2. `docs/open_issues.md`
  3. `docs/pr_1_agent_assignments.md`
  4. `theme_discovery_engine_v1.md`
  5. `docs/io_contracts.md` / `docs/data_schema.md`

## 2) Workflow rules

- Do not start implementation unless issue scope is assigned or explicitly requested.
- Before coding, confirm:
  - target files
  - acceptance conditions
  - state transitions (`assigned` -> `in-progress` -> `completed`)
- Keep `INDEX.md` aligned when adding/renaming tracked docs.
- Avoid broad refactors. Make minimal, reviewable changes.

## 3) Data and time-semantics rules

- `available_at` is mandatory for raw discovery artifacts.
- `published_at`, `available_at`, and source-vintage style provenance are required for source acquisition paths.
- Prefer as-reported historical values over live/restated values.
- Keep `source_id` and stable document identifiers preserved.

## 4) API/backend implementation baseline

- Keep route handlers thin.
- Keep helpers in `app/backend/theme_engine/*` with explicit validation functions.
- Keep responses deterministic and schema-validated via Pydantic models.
- Return clear 4xx errors with actionable reasons for malformed data/manifests.

## 5) PR/Issue tracking updates

- Code changes tied to a specific issue/role should update:
  - `docs/open_issues.md` task state and status
  - `docs/pr_1_agent_assignments.md` status row when used
  - corresponding issue comments (design/acceptance summary)

## 6) Change control

- Do not revert unrelated changes in files you did not touch.
- Prefer explicit file-scoped edits.
- Do not run full test suites unless explicitly requested.
