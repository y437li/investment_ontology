# Open Issues

Tracked design gaps in `theme_discovery_engine_v1.md` that need an implementation decision before or during the relevant milestone. Each item lists the conflict, the affected spec sections, and the proposed resolution. Update status as items are closed.

Status legend: `open` | `in-progress` | `resolved`

---

## OI-1 Minimal walk-forward for a real research claim

- Status: open
- Affects: sections 1, 22, 28; Milestone 6
- Conflict: The product goal (section 1) is to validate whether discovered communities relate to future outcomes, but the MVP uses a single `as_of_date`, which is a single cross-sectional draw and cannot support statistical association. Walk-forward (section 22) is currently "Later".
- Proposed resolution: Pull a minimal walk-forward (3-4 monthly time points) into the MVP scope so the core hypothesis is testable. Until then, validation output must be labeled illustrative (see section 2 MVP Caveats).
- Decision needed by: Milestone 6 planning.

## OI-2 Discipline for interpretive edges (`benefits` / `hurts` / `exposed_to` / `causes`)

- Status: open
- Affects: sections 7, 9.4, 9.7, 17
- Conflict: Section 17 forbids LLM reasoning in the discovery layer, but these edge types are causal/benefit judgments extracted by the LLM, and they feed exposure -> baskets -> validation. This is reasoning entering discovery through the back door.
- Proposed resolution: Restrict these edges to relationships explicitly stated in a document, each with an evidence chunk. Tag any LLM-inferred relationship with `extraction_method=llm_inferred` and exclude it from exposure by default; treat it as a separate weak signal.
- Decision needed by: Milestone 3.

## OI-3 Freeze enforcement and automated leakage tests

- Status: open
- Affects: sections 8, 9.8, 16, 18; Milestones 5-6
- Conflict: "Validation cannot read future data before freeze" is a process convention with no mechanism. Required artifacts (section 8) mix discovery artifacts and future data (`market_prices`, `fundamentals`) in one run directory.
- Proposed resolution:
  - Physically separate `data/runs/<run_id>/discovery/` and `.../validation/`; future data writes only to the latter.
  - On freeze, hash all discovery artifacts into `run_manifest.json`; validation startup verifies hashes and aborts if missing or changed.
  - pytest gates: (a) every evidence chunk entering `Graph(t)` has `available_at <= as_of_date`; (b) every price/fundamental row read by validation is dated after `as_of_date` and is read only after freeze.
- Decision needed by: Milestone 5.

## OI-4 Point-in-time entity / alias resolution

- Status: open
- Affects: sections 10, 15, 16
- Conflict: Section 15 filters documents/entities/edges by `available_at <= t`, but alias merging (section 10) uses embeddings over the full corpus, so future documents can shape the canonical entity set at time `t`. Section 16 lists this as weak leakage but only "monitor".
- Proposed resolution: Build the alias table for `Graph(t)` using only documents with `available_at <= t`. Keep a global alias table separately for non-temporal inspection.
- Decision needed by: Milestone 4.

## OI-5 Graph projection before community detection

- Status: open
- Affects: sections 7, 11
- Conflict: `Document` is a node type and `mentioned_in` is an edge. Running Louvain/Leiden on a graph containing Document nodes makes documents high-degree hubs and clusters by co-membership rather than economic relationship.
- Proposed resolution: Define an explicit projection to an entity-only graph (Document nodes and `mentioned_in` edges used for evidence, not for community structure) before community detection.
- Decision needed by: Milestone 4.

## OI-6 Run model vs multi-period walk-forward

- Status: open
- Affects: sections 8, 22
- Conflict: `run_manifest.json` carries a single `as_of_date`, but walk-forward builds graphs at many `t`. The single-manifest model cannot express a run with multiple time points.
- Proposed resolution: Define one run = one `as_of_date`, and model a walk-forward as a parent sweep over child runs (e.g. `sweep_manifest.json` referencing child run ids). Confirm before OI-1.
- Decision needed by: Milestone 6 planning.

## OI-7 Forward-window vs data coverage constraint

- Status: open
- Affects: sections 19, 22
- Conflict: 1M/3M forward returns require `as_of_date` to sit at least 3 months before the end of available data. With 12-24 months of data and a late `as_of_date`, forward returns may not exist.
- Proposed resolution: Add a validation precondition that rejects an `as_of_date` lacking sufficient forward coverage for the configured holding periods.
- Decision needed by: Milestone 6.
