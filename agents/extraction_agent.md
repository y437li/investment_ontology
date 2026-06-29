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

OI-2 Stated-vs-Inferred Discipline (interpretive edges):

Interpretive edge types (`benefits`, `hurts`, `exposed_to`, `causes`, `sensitive_to`) are LLM
judgments that feed exposure -> baskets -> validation. These must be disciplined at extraction time:

- Label edges **explicitly stated in the document text** as `extraction_method=document_stated`.
  These edges MUST have at least one `evidence_chunk_ids` entry. A `document_stated` edge with no
  evidence is rejected (dropped) and never written to `edges.parquet`.
- Label edges that the LLM **inferred beyond what the text says** as `extraction_method=llm_inferred`.
  These are excluded from community discovery and exposure by default.
- Label deterministic metadata-derived signals as `extraction_method=metadata_inferred`.
  Also excluded by default.
- `extraction_method` is an **enum**: `{document_stated, llm_inferred, metadata_inferred}`.
  Out-of-enum values are silently rejected at extraction time.

See spec §7 (method constraints) and §17 (stated-vs-inferred discipline) for the authoritative rule.

Alias table discipline (OI-4 — PIT-vs-global):

Two alias artifacts are written per run by `entity_resolution.resolve_entities()`:

- `entity_aliases.parquet` — **point-in-time (PIT)** table.  Built from chunks
  with `available_at <= as_of_date` only.  `alias_scope="point_in_time"`.
  This is the ONLY alias table consumed by Graph(t), exposure, and community
  detection.  Entities whose sole evidence is future-dated are excluded.

- `entity_aliases_global.parquet` — **global companion** table.  Full corpus,
  no `available_at` filter.  `alias_scope="global"`, `as_of_date=""`.
  FOR INSPECTION / CURATION ONLY.  Must never be read by any discovery-stage
  computation (graph_build, exposure, themes).  Violations are caught by the
  OI-4 isolation test suite.

Acceptance checks:

- Entity types match the ontology.
- Edge types match the ontology.
- Non-trivial edges include evidence chunk ids.
- Low-confidence extractions are marked for review.
- Alias joins for Graph(t) are point-in-time with `available_at <= as_of_date`.
- Alias map entries include `alias_scope` and `as_of_date` in discovery artifacts.
- Future-dated alias sources (available_at > as_of_date) appear in the global
  table but are excluded from the PIT table.
- Raw files are never read directly by extraction.
