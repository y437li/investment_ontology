# Skill: Maintainable Code Implementation

Purpose:

Write code that is configurable, encapsulated, testable, and easy for future Codex or Claude agents to extend.

Style target:

- Follow `docs/code_style_standards.md`, especially the Waterloo CS136-inspired design recipe: purpose, contract, requires, effects, helpers, constants, and tests.

Use when:

- Writing new backend, pipeline, frontend, or script code.
- Refactoring repeated logic.
- Replacing hardcoded demo behavior.
- Adding comments or docstrings.

Inputs:

- `theme_discovery_engine_v1.md`
- `docs/code_style_standards.md`
- `docs/formatting_standards.md`
- relevant implementation files
- relevant configs

Steps:

1. Identify the behavior boundary before writing code.
2. Move repeated or fragile behavior into named functions, services, or helpers.
3. Replace hardcoded values with config, constants, or function parameters.
4. Write non-trivial function contracts with purpose, requires, effects, returns, and raises.
5. Add comments for assumptions, invariants, leakage rules, and non-obvious tradeoffs.
6. Use named constants or config entries for meaningful values.
7. Keep comments current when changing behavior.
8. Add a small test, fixture, or smoke check for the changed behavior.
9. Update `INDEX.md` if new source files or guides are added.

Outputs:

- maintainable implementation code.
- updated configs if needed.
- comments/docstrings where useful.
- tests or smoke checks.

Acceptance checks:

- No material threshold, path, date, ticker, model, or validation window is hardcoded in production code.
- Repeated logic is encapsulated behind a named boundary.
- Non-trivial functions have clear contracts.
- Route handlers stay thin.
- Artifact read/write behavior is centralized or consistently wrapped.
- Comments explain why, not obvious what.
- Tests or smoke checks cover the new behavior.

Failure modes:

- One-off scripts with embedded paths and tickers.
- Duplicated artifact-writing logic.
- Comments that contradict current behavior.
- Config values copied into code literals.
- Refactors that add abstraction without reducing risk or duplication.
