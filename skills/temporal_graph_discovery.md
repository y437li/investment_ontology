# Skill: Temporal Graph Discovery

Purpose:

Build `Graph(t)` and discover theme communities.

Inputs:

- `entities.parquet`
- `edges.parquet`
- `as_of_date`
- graph config.

Steps:

1. Filter entities and edges by `as_of_date`.
2. Build weighted graph.
3. Run Louvain or Leiden.
4. Compute community metrics.
5. Name communities only after detection.

Outputs:

- `graph.json`
- `communities.json`
- `theme_snapshots.json`
- `theme_metrics.parquet`

Failure modes:

- Predefining themes.
- Naming communities before detection.
- Using future edges.

