# Extraction Agent

Mission:

Extract entities, relationships, and evidence-backed edge explanations from cleaned chunks.

Inputs:

- cleaned `chunks.parquet`
- ontology from `theme_discovery_engine_v1.md`
- pipeline config

Outputs:

- `entities.parquet`
- `entity_aliases.parquet`
- `entity_aliases_global.parquet` (optional diagnostics copy)
- `edges.parquet`
- `edge_explanations.parquet`

Acceptance checks:

- Entity types match the ontology.
- Edge types match the ontology.
- Non-trivial edges include evidence chunk ids.
- Low-confidence extractions are marked for review.
- Alias joins for Graph(t) are point-in-time with `available_at <= as_of_date`.
- Alias map entries include `alias_scope` and `as_of_date` in discovery artifacts.
- Raw files are never read directly by extraction.
