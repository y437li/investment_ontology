# Folder Structure

Recommended workspace layout:

```text
investment_ontology/
  INDEX.md
  theme_discovery_engine_v1.md
  README.md
  configs/
    universe.example.yml
    pipeline.example.yml
    validation.example.yml
  docs/
    folder_structure.md
    formatting_standards.md
    code_style_standards.md
    data_schema.md
    io_contracts.md
    team_roles.md
    mirofish_reference.md
    implementation_checklist.md
  agents/
    README.md
    orchestrator.md
    data_architect_agent.md
    data_engineering_agent.md
    data_ingestion_agent.md
    data_cleaning_agent.md
    extraction_agent.md
    graph_theme_agent.md
    validation_agent.md
    frontend_report_agent.md
  skills/
    README.md
    point_in_time_data.md
    unstructured_data_cleaning.md
    entity_relation_extraction.md
    temporal_graph_discovery.md
    validation_backtest.md
    evidence_report_generation.md
    backend_api_implementation.md
    pipeline_artifact_implementation.md
    frontend_workflow_implementation.md
    test_quality_gate.md
    maintainable_code_implementation.md
  data/
    inputs/
      documents/
      market/
      fundamentals/
    db/
    runs/
    cache/
  app/
    backend/
    frontend/
  scripts/
  tests/
```

Rules:

- `INDEX.md` is the maintained navigation map.
- `theme_discovery_engine_v1.md` is the source of truth.
- `agents/` and `skills/` are tool-agnostic; Codex and Claude can both use them.
- `data/` should contain generated or local input data and should not be treated as source code.
- Every run should write to `data/runs/<run_id>/`.
- Every run should include `run_manifest.json`.
- When adding, renaming, or materially changing source docs, configs, agents, skills, or guides, update `INDEX.md`.
