# Project Index

This is the maintained navigation index for the `investment_ontology` workspace.

Rule:

- Update this file whenever a source document, config, agent spec, skill spec, or implementation guide is added, renamed, or materially changed.
- Do not index generated run artifacts under `data/runs/`.
- Do not index local raw input files under `data/inputs/`.

## Core Documents

| Path | Purpose | Status |
|---|---|---|
| `theme_discovery_engine_v1.md` | Source-of-truth product, architecture, ontology, agents, validation, and implementation milestone spec. | Core |
| `README.md` | Short workspace entrypoint and reading order. | Core |
| `INDEX.md` | Maintained navigation map for this workspace. | Core |
| `CODE_OF_CONDUCT.md` | Collaboration, evidence, code quality, research, and agent conduct rules. | Core |

## Workspace Maintenance

| Path | Purpose | Status |
|---|---|---|
| `.gitignore` | Keeps local data, generated run artifacts, caches, dependencies, and secrets out of source control. | Active |

## Configs

| Path | Purpose | Status |
|---|---|---|
| `configs/universe.example.yml` | Example company universe for MVP development. | Example |
| `configs/pipeline.example.yml` | Example pipeline settings for import, extraction, graph, and interpretation. | Example |
| `configs/validation.example.yml` | Example validation windows, metrics, and leakage rules. | Example |

## Docs

| Path | Purpose | Status |
|---|---|---|
| `docs/folder_structure.md` | Workspace folder layout and maintenance rules. | Reference |
| `docs/formatting_standards.md` | Formatting rules for docs, configs, artifacts, agents, skills, reports, and future code. | Reference |
| `docs/code_style_standards.md` | CS136-inspired code style rules for encapsulation, variables, comments, contracts, service boundaries, and tests. | Reference |
| `docs/data_schema.md` | Layered data model from raw unstructured inputs to cleaned artifacts, structured discovery data, and validation data. | Reference |
| `docs/io_contracts.md` | Canonical input and output formats for artifacts, stages, APIs, agents, skills, and validation data boundaries. | Reference |
| `docs/team_roles.md` | Team role responsibilities, including Data Architect and Data Engineer ownership. | Reference |
| `docs/mirofish_reference.md` | Source-of-truth boundary for borrowing MiroFish workflow patterns without copying its simulation logic. | Reference |
| `docs/implementation_checklist.md` | MVP acceptance checklist and non-goals. | Checklist |
| `docs/codex_agent_workflow.md` | PR/Issue-driven execution protocol for Codex Agents. | Reference |
| `docs/open_issues.md` | Tracked design gaps in the spec with proposed resolutions and decision milestones. | Tracking |
| `docs/pr_1_agent_assignments.md` | PR #1 internal execution board for role-based agent assignment and work status. | Coordination |

## Shared Agents

These specs are tool-agnostic and can be used by Codex or Claude.

| Path | Purpose | Status |
|---|---|---|
| `agents/README.md` | Agent usage instructions. | Reference |
| `agents/orchestrator.md` | Coordinates scope, run plan, artifacts, and acceptance checks. | Active |
| `agents/data_architect_agent.md` | Owns data model, schema contracts, lineage, point-in-time semantics, and storage architecture decisions. | Active |
| `agents/data_engineering_agent.md` | Owns ingestion, source adapters, ETL, data quality, and artifact production pipelines. | Active |
| `agents/data_ingestion_agent.md` | Owns point-in-time raw document ingestion and text extraction. | Active |
| `agents/data_cleaning_agent.md` | Owns cleaned unstructured artifacts, document cleaning logs, chunking, and quarantine rules. | Active |
| `agents/extraction_agent.md` | Owns entity, relationship, alias, and evidence extraction. | Active |
| `agents/graph_theme_agent.md` | Owns Graph(t), community detection, theme snapshots, and metrics. | Active |
| `agents/validation_agent.md` | Owns forward validation and benchmark comparison. | Active |
| `agents/frontend_report_agent.md` | Owns dashboard/report requirements and evidence-backed report generation. | Active |

## Shared Skills

These workflow specs are tool-agnostic and can be used by Codex or Claude.

| Path | Purpose | Status |
|---|---|---|
| `skills/README.md` | Skill usage instructions. | Reference |
| `skills/point_in_time_data.md` | Workflow for historically correct document and chunk artifacts. | Active |
| `skills/unstructured_data_cleaning.md` | Workflow for audited cleaning and chunking of unstructured source documents. | Active |
| `skills/entity_relation_extraction.md` | Workflow for entities, relationships, aliases, and evidence. | Active |
| `skills/temporal_graph_discovery.md` | Workflow for Graph(t), community discovery, and theme snapshots. | Active |
| `skills/validation_backtest.md` | Workflow for validation after discovery artifacts are frozen. | Active |
| `skills/evidence_report_generation.md` | Workflow for artifact-backed research reports. | Active |
| `skills/backend_api_implementation.md` | Workflow for implementing backend API routes and service boundaries. | Active |
| `skills/pipeline_artifact_implementation.md` | Workflow for implementing reproducible pipeline stages and artifact contracts. | Active |
| `skills/frontend_workflow_implementation.md` | Workflow for implementing Vue workflow screens and evidence drilldowns. | Active |
| `skills/test_quality_gate.md` | Workflow for adding tests and verification gates before accepting code changes. | Active |
| `skills/maintainable_code_implementation.md` | Workflow for encapsulated, configurable, well-commented, testable code. | Active |

## Implementation Placeholders

| Path | Purpose | Status |
|---|---|---|
| `app/backend/` | Backend implementation target. | Placeholder |
| `app/frontend/` | Frontend implementation target. | Placeholder |
| `scripts/` | Project scripts and run helpers. | Placeholder |
| `tests/` | Smoke and regression tests. | Placeholder |

## Local Data Areas

These folders are intentionally not indexed file-by-file.

| Path | Purpose | Git Policy |
|---|---|---|
| `data/inputs/documents/` | Local source documents. | Ignored except `.gitkeep` |
| `data/inputs/market/` | Local market input data. | Ignored except `.gitkeep` |
| `data/inputs/fundamentals/` | Local fundamentals input data. | Ignored except `.gitkeep` |
| `data/db/` | Local DuckDB database files. | Ignored except `.gitkeep` |
| `data/runs/` | Generated run artifacts. | Ignored except `.gitkeep` |
| `data/cache/` | LLM and embedding cache. | Ignored except `.gitkeep` |
