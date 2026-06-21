# Formatting Standards

This document defines formatting rules for project documents, configs, artifacts, agents, skills, and future code.

## 1. File Naming

Use lowercase snake case for source documents:

```text
theme_discovery_engine_v1.md
formatting_standards.md
pipeline.example.yml
backend_api_implementation.md
```

Use these extensions:

- Markdown: `.md`
- YAML configs: `.yml`
- JSON artifacts: `.json`
- CSV validation outputs: `.csv`
- Parquet tables: `.parquet`

Do not use spaces in project source filenames.

## 2. Markdown Format

Rules:

- One `# H1` per document.
- Use `##` and `###` for sections.
- Prefer short sections with clear headings.
- Use tables for indexes and file maps.
- Use fenced code blocks with language labels where practical.
- Use backticks for file paths, config keys, artifact names, and API routes.
- Keep project docs evidence-oriented and implementation-ready.

Avoid:

- Marketing language.
- Unsupported claims.
- Long prose without artifacts or acceptance checks.
- Mixing source facts, model output, and inference without labels.

## 3. Source-of-Truth Order

When documents conflict, follow this order:

1. `theme_discovery_engine_v1.md`
2. `INDEX.md`
3. `CODE_OF_CONDUCT.md`
4. files under `docs/`
5. files under `agents/`
6. files under `skills/`
7. configs under `configs/`

If a lower-priority file intentionally changes scope, update the higher-priority file.

## 4. INDEX Maintenance

Update `INDEX.md` when adding, renaming, or materially changing:

- source documents
- config examples
- agent specs
- skill specs
- implementation guides
- maintenance files

Do not index generated run artifacts or local raw inputs file-by-file.

## 5. Agent Spec Format

Agent files should use this shape:

```markdown
# Agent Name

Mission:

Responsibilities:

Inputs:

Outputs:

Acceptance checks:

Hard rules:
```

Agent specs should describe work roles, not magical autonomous behavior.

## 6. Skill Spec Format

Skill files should use this shape:

```markdown
# Skill: Skill Name

Purpose:

Use when:

Inputs:

Steps:

Outputs:

Acceptance checks:

Failure modes:
```

Code-writing skills must also define:

- service boundaries
- artifact contracts
- tests or smoke checks
- verification expectations

## 7. Config Format

YAML config rules:

- Use lowercase snake case keys.
- Use ISO dates: `YYYY-MM-DD`.
- Use relative paths from the workspace root.
- Keep examples small and runnable.
- Do not put secrets in config examples.

Example:

```yaml
as_of_date: "2024-06-30"
chunk_size: 800
min_edge_confidence: 0.55
community_algorithm: leiden
```

Secrets belong in `.env`, which must not be committed.

## 8. Artifact Format

Run artifacts live under:

```text
data/runs/<run_id>/
```

Use this run id format:

```text
run_YYYYMMDD_HHMMSS
```

Artifacts should include version and provenance fields where practical:

```text
schema_version
pipeline_version
created_at
as_of_date
input_hash
model_config_hash
```

Column names should use lowercase snake case:

```text
document_id
available_at
source_entity_id
target_entity_id
edge_type
evidence_chunk_ids
```

Required MVP artifacts are listed in `theme_discovery_engine_v1.md`.

Canonical input and output fields are defined in `docs/io_contracts.md`.

## 9. Report Format

Reports should be markdown and should include:

- run id
- as-of date
- universe
- data coverage
- theme summary
- top exposed companies
- evidence references
- validation results
- caveats

Reports must not:

- imply automatic investment advice
- hide failed or weak validation
- cite LLM output as market evidence
- claim themes were discovered by LLM if they came from graph communities

## 10. Backend Code Format

When backend code is added:

- Keep route handlers thin.
- Put reusable logic under services or pipeline modules.
- Use Pydantic or equivalent schema validation.
- Use lowercase snake case for Python files and functions.
- Use clear error messages with `run_id` and stage name.
- Keep artifact read/write behavior centralized where practical.
- Follow `docs/code_style_standards.md` for encapsulation, no-hardcoding, variable/config usage, comments, and function contracts.

Recommended checks:

```text
ruff
pytest
```

## 11. Frontend Code Format

When frontend code is added:

- Use PascalCase for Vue components.
- Use lowercase route paths.
- Put API calls in dedicated client modules.
- Keep demo data out of components.
- Show run id, as-of date, artifact state, and error state in workflow pages.
- Keep evidence drilldown available from theme, company, edge, and report views.

Recommended checks:

```text
npm run build
```

## 12. UI Text

Use research-product language:

- "theme community"
- "evidence"
- "exposure"
- "validation"
- "forward return"
- "caveat"

Avoid marketing or certainty language:

- "predict anything"
- "guaranteed"
- "high-fidelity future"
- "winning stock"

## 13. Review Checklist

Before accepting a file change:

- Does it follow naming rules?
- Is `INDEX.md` updated if needed?
- Are configs free of secrets?
- Are artifact names and fields consistent?
- Are evidence and validation claims traceable?
- Are code changes covered by a small test or smoke check?
