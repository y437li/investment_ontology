# Implementation Checklist

## MVP Acceptance

- [ ] `as_of_date` is required for every run.
- [ ] `source_manifest.csv` exists for raw document batches.
- [ ] Documents without `available_at` are rejected or quarantined.
- [ ] `raw_documents.parquet` is written.
- [ ] `documents.parquet` is written.
- [ ] `document_cleaning_log.parquet` is written.
- [ ] `chunks.parquet` is written.
- [ ] Extraction only reads cleaned `chunks.parquet`.
- [ ] `entities.parquet` is written.
- [ ] `edges.parquet` is written.
- [ ] Important edges include evidence chunk ids.
- [ ] `graph.json` is written.
- [ ] `communities.json` is written.
- [ ] `theme_snapshots.json` is written.
- [ ] `theme_lineage.json` is written, even if single-snapshot lineage is empty.
- [ ] Theme names are metadata, not discovery inputs.
- [ ] `company_theme_exposure.parquet` is written.
- [ ] Exposure is computed before validation data is loaded.
- [ ] Discovery artifacts are frozen before validation.
- [ ] `market_prices.parquet` is written for validation.
- [ ] `fundamentals.parquet` is written or schema-valid empty when disabled.
- [ ] `portfolio_baskets.parquet` records basket constituents and weights.
- [ ] `validation.csv` is written.
- [ ] `report.md` cites artifacts and evidence.
- [ ] Demo run is reproducible.

## Non-Goals for MVP

- [ ] No trading automation.
- [ ] No full-market coverage.
- [ ] No production permissions.
- [ ] No OASIS social simulation dependency.
- [ ] No MiroFish simulation route names in the core API.
- [ ] No unsupported prediction claims.
