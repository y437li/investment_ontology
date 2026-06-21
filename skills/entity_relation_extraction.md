# Skill: Entity and Relationship Extraction

Purpose:

Convert chunks into structured entities and evidence-backed relationships.

Inputs:

- `chunks.parquet`
- ontology.
- LLM config.

Steps:

1. Extract entities with allowed types only.
2. Extract relationships with allowed edge types only.
3. Attach evidence chunk ids.
4. Normalize aliases.
5. Mark confidence and method.

Outputs:

- `entities.parquet`
- `entity_aliases.parquet`
- `edges.parquet`
- `edge_explanations.parquet`

Failure modes:

- Edges without evidence.
- LLM-invented entities not present in source text.
- Mixing theme naming with discovery.

