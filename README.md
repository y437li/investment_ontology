# Theme Discovery Engine

This workspace defines the MVP architecture for a time-aware economic narrative discovery engine.

Start with:

- `INDEX.md`: maintained navigation index.
- `theme_discovery_engine_v1.md`: source-of-truth project spec.
- `CODE_OF_CONDUCT.md`: collaboration, evidence, code, and agent conduct rules.
- `docs/folder_structure.md`: workspace layout.
- `docs/formatting_standards.md`: formatting rules for docs, configs, artifacts, agents, skills, and future code.
- `docs/code_style_standards.md`: CS136-inspired encapsulation, no-hardcoding, variables, comments, service boundaries, contracts, and test rules.
- `docs/io_contracts.md`: canonical input and output formats for stages, artifacts, APIs, agents, and skills.
- `docs/mirofish_reference.md`: what to borrow from MiroFish and what to replace.
- `configs/*.yml`: example runtime configuration.
- `agents/*.md`: shared Codex/Claude role specs.
- `skills/*.md`: shared Codex/Claude workflow specs.

MVP goal:

```text
Documents -> Entities -> Graph(t) -> Communities -> Theme Snapshots -> Validation -> Report
```

The first demo should be local, reproducible, and evidence-backed.

Maintenance rule:

- When adding, renaming, or materially changing a source document, config, agent, skill, or guide, update `INDEX.md`.
