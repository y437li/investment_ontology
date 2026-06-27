# Design: Evidence Granularity & Company-Level Financial Analysis

Status: `proposed` (design + dispatch plan; no code yet)
Author: design review, 2026-06-27
Linked spec: `theme_discovery_engine_v1.md` §§6–8 (document spine), §11/§12 (themes/slices), §20 (`io_contracts` fundamentals)

## Problem

Three user-reported gaps that are **one broken chain**, not three independent issues:

1. 抽取和清洗有很大优化空间 (extraction & cleaning under-optimized)
2. 前端展示不够细致 (frontend not detailed enough)
3. 公司层面没什么财报分析；现实证据过于笼统 (no company-level financials; evidence too generic)

Root cause — structure is destroyed at the cleaning stage and never recovered:

```
clean (flattens tables/sections) → extract (entities+relations only, no numbers)
   → graph/themes (qualitative edges only) → frontend (nothing concrete to show)
```

Confirmed in code (2026-06-27):
- `data_cleaning.py` flattens `<table>` to text; `chunking.py:177-179` leaves `section_title` / `page_start` / `page_end` **always None**.
- `extraction.py` + `ontology.yml`: 7 entity types, **no `FinancialMetric`**; 8 qualitative edge types; system prompt forbids numbers/facts.
- `io_contracts §20` defines `fundamentals.parquet` but **no code reads or writes it**.
- Frontend: a company exists only as a chip / an exposure-table row (name + 0–1 score + evidence count) / a graph node. **Zero financial fields, no company detail page.**

Fix must be **ordered** (A unblocks B unblocks C); D rides along.

## Decisions locked (user, 2026-06-27)

- Financial data source = **both**: structured XBRL first (skeleton, precise), LLM quantified-fact extraction second (flesh: guidance / forward-looking / narrative numbers).
- Deliverable for this round = **this design doc + dispatch plan**. No code until reviewed.
- **A table representation** = a `block_type="table"` chunk carrying a normalized cell grid (rows × cells), kept inside the existing chunk pipeline — **not** a separate `tables.parquet` artifact (minimal change, one schema).
- **B2 ontology type** = `FinancialMetric` entity (more specific than a generic `Fact`; clearer ontology boundary), added to `ontology.yml`.

## ⚠️ Leakage guard (load-bearing — read before building B)

`io_contracts §20 fundamentals.parquet` is **validation-only and forward-looking** ("Discovery stages must not read validation-only fundamentals"). The company-page financial analysis happens in **discovery**, so it must NOT reuse that artifact.

→ Introduce a **separate discovery-time as-reported fundamentals artifact**, PIT-clean by construction: every row carries `available_at` and only rows with `available_at ≤ run.as_of` are ever surfaced in discovery/extraction/UI. As-reported (first published) values, no restatements pulled forward. This is the same PIT discipline already enforced on chunks; reuse it. The validation §20 artifact stays untouched and frozen-gated.

---

## Workstream A — Structure-preserving cleaning & chunking (UNBLOCKS EVERYTHING)

- **Owner:** data-engineering / pipeline
- **Files:** `app/backend/theme_engine/data_cleaning.py`, `chunking.py`; chunk schema in `docs/io_contracts.md` (§ chunks) + `docs/data_schema.md`; tests under `tests/`.
- **What:**
  1. Table-aware cleaning: detect `<table>` (and ASCII/aligned-column tables in plain-text filings); emit each table as a **structured block** (rows × cells preserved, e.g. a `block_type="table"` chunk carrying a normalized cell grid) instead of flattening to prose.
  2. Section-aware chunking: populate `section_title` (from heading tags / "Item N." patterns / MD&A headers) and `page_start`/`page_end` — stop writing `None`. Tag chunks with `block_type` ∈ {prose, table, heading}.
  3. Keep numbers attached to their unit/label within a block (don't let chunk boundaries split `Revenue | $X | $Y`).
- **Acceptance:**
  - A filing with an income statement produces ≥1 chunk where the statement's rows are recoverable as structured cells (test asserts cell grid, not a prose blob).
  - `section_title` is non-null for ≥80% of chunks on the EDGAR fixture; `block_type` present on every chunk.
  - Sentence-aware prose chunking (PR #88) behavior unchanged for prose blocks; CI green; negative test proves a flattened table is no longer emitted for table input.
- **Why first:** B's structured + LLM extraction both read these chunks. Without structure here, B can only re-guess prose.

## Workstream B — Financial fact / metric extraction (the core of "company financials")

Two sub-tracks, B1 then B2. Both write to the **discovery as-reported fundamentals artifact** (see leakage guard), and both attach facts to `Company` nodes so themes/exposure/UI can read them.

### B1 — Structured XBRL ingestion (skeleton, precise)

- **Owner:** data-engineering
- **Files:** new `app/backend/theme_engine/fundamentals_adapter.py` (mirror `macro_adapter.py` / `altdata_adapter.py` pattern); new `configs/fundamentals.yml` (metric whitelist + source mapping — metric names from config, per §20 rule); new discovery artifact contract in `io_contracts.md`; tests under `tests/`.
- **What:** Read EDGAR XBRL (US-listed) and SEDAR financial statements where available → emit as-reported rows `(company_id, period_end, metric_name, metric_value, unit, currency, filing_date, available_at, source, source_id)`. Metric set from config (revenue, net_income, EPS, gross/operating/EBITDA margin, operating cash flow, total debt, …). PIT: `available_at = filing_date` (first publication); as-reported values only.
- **Acceptance:** for ≥1 real company in the universe, XBRL → ≥5 metrics across ≥2 periods with correct units/currency and `available_at = filing_date`; hermetic test on a committed XBRL fixture (no network); empty-but-schema-valid artifact when a company has no XBRL.

### B2 — LLM quantified-fact extraction (flesh: guidance, forward-looking, narrative numbers)

- **Owner:** extraction
- **Files:** `app/backend/theme_engine/extraction.py`, `configs/ontology.yml` (add `FinancialMetric`/`Fact` entity type + `reports`/`guides_to` edge types), `configs/agents.yml` (new agent prompt for a quantified-claim pass — edit prompt in the table, not code); tests with a fake extractor.
- **What:** a second extraction pass (separate tool schema) that pulls **quantified claims** from narrative (MD&A / news / transcripts): `(company, metric_name, value, unit, period, direction, is_guidance, evidence_chunk_id)` — e.g. "Q2 revenue rose 12% to $X", "raised FY guidance to …". Emit as `Fact` nodes attached to the company, each carrying its source chunk. Distinguish reported-actual vs guidance/forward-looking. Reconcile against B1 where overlapping (XBRL wins for as-reported; LLM owns guidance/narrative). Confidence + evidence required per existing `document_stated_edge_requires_evidence` rule.
- **Acceptance:** on the real news corpus, ≥1 guidance/quantified claim extracted with a correct number, unit, period, and a pointing evidence chunk; hermetic test injects a fake client; no claim emitted without an evidence chunk; reconciliation test proves XBRL value preferred over an LLM-extracted as-reported duplicate.

## Workstream C — Company detail page (frontend)

- **Owner:** frontend
- **Files:** new `app/frontend/src/views/CompanyView.vue` + route; `app/frontend/src/api/themes.js`; new backend endpoint(s) `GET /api/themes/{run}/companies/{id}` (profile + fundamentals + facts) and `GET /api/themes/{run}/companies/{id}/evidence` (company-filtered evidence list).
- **What:** a real per-company page reachable from any company node/chip/exposure row:
  - **Financials panel:** as-reported metrics (B1) as a small statement/trend table; guidance & narrative facts (B2) with source links.
  - **Per-theme exposure:** which themes this company sits in and why (the existing exposure score, now alongside financials).
  - **Company-filtered evidence:** every evidence snippet that mentions this company, each with the extracted number/fact (D) and "read full source" (already exists).
- **Acceptance:** clicking a company anywhere opens its page; financials panel renders real B1/B2 values (or an explicit "no fundamentals available at as_of" state — never silently blank); all numbers are PIT-clean (`available_at ≤ as_of`); evidence list is filtered to the company and links to full source.

## Workstream D — Evidence quantification (rides on B)

- **Owner:** extraction + frontend
- **Files:** `reasoning.py` (`_relevant_evidence` — attach the extracted fact, not just the sentence), evidence rendering in `ThemesView.vue` / `CompanyView.vue`.
- **What:** each evidence record carries the **specific number/fact it asserts** (from B2), not only the sentence text — so "证据过于笼统" becomes "Suncor Q2 oil-sands output 740 kbbl/d (+5% q/q), per 6-K p.4" instead of a vague clause.
- **Acceptance:** where a fact was extracted, the evidence chip shows the quantified claim + provenance; where none was, it falls back to today's sentence-level snippet (no regression).

---

## Dispatch plan (reviewed-team via Workflow, per the model that worked on PR #74)

Sequence (A is a hard prerequisite; B1/B2 then fan out; C/D last):

1. **Phase A** — single worker builds structure-preserving cleaning/chunking in an isolated worktree → adversarial verify (table-recovery + section-metadata negative tests) → Opus lead gate → integrate + CI → merge. **Block downstream until merged** (everything reads the new chunk schema).
2. **Phase B** — after A on main: two workers in parallel worktrees, B1 (XBRL adapter) and B2 (LLM fact pass). Each self-verifies (hermetic fixtures). Then a **reconciliation verify** agent checks B1/B2 overlap handling + the leakage guard (no validation-fundamentals read in discovery; `available_at ≤ as_of` everywhere). Opus lead gate → integrate → CI → merge.
3. **Phase C+D** — after B: one worker builds `CompanyView` + endpoints (C) and folds quantified facts into evidence rendering (D). Verify PIT-clean display + no-silent-blank states. Lead gate → CI → merge.

Each phase = one PR, lead-approved, CI green, before the next starts. Worker agents on Sonnet; lead reviewer on Opus (per standing config).

## Tracking

File as GitHub issues to mirror this doc (keep the workstream id in the title):
- **EG-A** structure-preserving cleaning/chunking — blocking
- **EG-B1** XBRL as-reported fundamentals adapter (discovery-time, PIT)
- **EG-B2** LLM quantified-fact extraction pass (`FinancialMetric`/guidance)
- **EG-C** company detail page + endpoints
- **EG-D** evidence quantification

Relates to existing: OI-2 (interpretive-edge discipline — B2 adds quantified facts under the same evidence rule), OI-4 (PIT alias resolution — company-fact attribution must resolve aliases point-in-time), #9/OI-8 (acquisition/vintage — B1 source vintage).
