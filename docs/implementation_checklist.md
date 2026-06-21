# Implementation Checklist

## MVP Acceptance

- [ ] `as_of_date` is required for every run.
- [ ] Documents without `available_at` are rejected or quarantined.
- [ ] `documents.parquet` is written.
- [ ] `chunks.parquet` is written.
- [ ] `entities.parquet` is written.
- [ ] `edges.parquet` is written.
- [ ] Important edges include evidence chunk ids.
- [ ] `graph.json` is written.
- [ ] `communities.json` is written.
- [ ] `theme_snapshots.json` is written.
- [ ] Theme names are metadata, not discovery inputs.
- [ ] `company_theme_exposure.parquet` is written.
- [ ] Discovery artifacts are frozen before validation.
- [ ] `validation.csv` is written.
- [ ] `report.md` cites artifacts and evidence.
- [ ] Demo run is reproducible.

## Non-Goals for MVP

- [ ] No trading automation.
- [ ] No full-market coverage.
- [ ] No production permissions.
- [ ] No OASIS social simulation dependency.
- [ ] No unsupported prediction claims.

