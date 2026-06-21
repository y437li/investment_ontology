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
- `edges.parquet`
- `edge_explanations.parquet`

Acceptance checks:

- Entity types match the ontology.
- Edge types match the ontology.
- Non-trivial edges include evidence chunk ids.
- Low-confidence extractions are marked for review.
- Raw files are never read directly by extraction.
