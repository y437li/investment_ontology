# Code of Conduct

This project is a research engineering workspace for a time-aware economic narrative discovery engine. Contributors may be humans, Codex agents, Claude agents, or other automation. The same conduct standards apply to all contributors.

## 1. Core Principles

### Evidence First

- Do not present unsupported claims as facts.
- Every important research conclusion must trace to artifacts, source evidence, or validation results.
- Reports must distinguish source facts, model output, inference, and speculation.

### Point-in-Time Discipline

- Respect `available_at` and `as_of_date`.
- Do not let future documents, future returns, future fundamentals, or future community labels enter discovery.
- If leakage is found, report it directly and fix the pipeline before interpreting results.

### Reproducibility

- Prefer reproducible runs over one-off notebooks or screenshots.
- Write outputs to the expected run artifact folder.
- When changing a pipeline stage, preserve or update artifact contracts explicitly.

### Scope Control

- Keep MVP work focused on the current vertical slice.
- Do not silently expand scope into full-market production infrastructure.
- If a change affects product scope, update `theme_discovery_engine_v1.md`, relevant configs, and `INDEX.md`.

## 2. Collaboration Standards

### Be Direct and Specific

- State what changed, why it changed, and how it was checked.
- Use file paths, artifact names, configs, and run ids instead of vague descriptions.
- Raise blockers early, especially data availability, leakage, schema, or validation issues.

### Respect Existing Work

- Do not overwrite user work, generated artifacts, or prior decisions without checking context.
- Preserve established folder structure unless the source-of-truth spec changes.
- Follow `docs/formatting_standards.md` for documents, configs, artifacts, agents, skills, reports, and code.
- Follow `docs/code_style_standards.md` for CS136-inspired function contracts, encapsulation, variables, comments, service boundaries, and tests.
- Follow `docs/io_contracts.md` for required input and output formats.
- Update `INDEX.md` when adding, renaming, or materially changing source docs, configs, agents, skills, or guides.

### No False Certainty

- Do not claim investment usefulness before validation.
- Do not describe LLM-generated theme names as discovered truth.
- Do not hide weak samples, failed runs, or negative validation results.

## 3. Code Standards

### Keep Boundaries Clear

- API routes should stay thin.
- Pipeline logic should live in reusable modules.
- Artifacts should be read and written through explicit helpers where practical.
- Frontend components should not hardcode demo data.
- Encapsulate repeated or fragile behavior behind named functions, services, or helpers.
- Keep thresholds, paths, dates, tickers, model names, and validation windows in configs, constants, or function parameters rather than hardcoded production literals.

### Protect Artifact Contracts

- New artifacts need documented fields, schema/version metadata where practical, and acceptance checks.
- Required artifacts must stay under `data/runs/<run_id>/`.
- Generated data, caches, local inputs, and secrets must not be committed.

### Test What Matters

- Test `available_at` filtering.
- Test required artifact columns.
- Test validation freeze rules.
- Test representative failure cases, not only the happy path.
- If tests cannot be run, say so and explain the residual risk.

### Maintain Comments

- Use comments to explain assumptions, invariants, leakage-sensitive rules, and non-obvious tradeoffs.
- Do not add comments that merely restate obvious code.
- Update comments when behavior changes.

## 4. Agent Conduct

Agents must:

- Read `INDEX.md` and `theme_discovery_engine_v1.md` before substantial changes.
- Use relevant files under `agents/` and `skills/`.
- Produce file-backed outputs when requested, not only chat summaries.
- Keep generated reports linked to evidence and artifacts.
- Avoid unsupported predictions or investment recommendations.
- Update `INDEX.md` when creating or materially changing project source files.

Agents must not:

- Invent unavailable data.
- Treat simulated or LLM-generated text as market evidence.
- Bypass leakage rules for convenience.
- Make destructive file or git changes without explicit instruction.

## 5. Research and Investment Disclaimer

This project produces research tooling and validation artifacts. It does not produce automatic investment advice.

All investment-related outputs must be framed as research signals requiring review, validation, and risk assessment.

## 6. Handling Problems

When you find a problem:

1. Name the issue clearly.
2. Identify affected files, artifacts, or configs.
3. Explain whether it affects correctness, reproducibility, leakage, or interpretation.
4. Propose a scoped fix.
5. Update documentation or tests if the issue changes the expected workflow.
