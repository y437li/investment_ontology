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
  - **Per-theme exposure:** the company belongs to *many* themes — list each `(theme, exposure_score)` it sits in and why; this is the primary spine of the page, not a single theme.
  - **Evidence grouped by theme:** evidence is shown **per theme exposure** (from E3's `(company, theme)` grain), each snippet with the extracted number/fact (D) and "read full source" (already exists). A snippet that backs two themes appears under both; nothing is attributed to a theme it didn't support.
- **Acceptance:** clicking a company anywhere opens its page; financials panel renders real B1/B2 values (or an explicit "no fundamentals available at as_of" state — never silently blank); all numbers are PIT-clean (`available_at ≤ as_of`); the page lists all themes the company is exposed to, and evidence under each theme is that theme's own (test a company in ≥2 themes — no cross-theme bleed); every snippet links to full source.

## Workstream D — Evidence quantification (rides on B)

- **Owner:** extraction + frontend
- **Files:** `reasoning.py` (`_relevant_evidence` — attach the extracted fact, not just the sentence), evidence rendering in `ThemesView.vue` / `CompanyView.vue`.
- **What:** each evidence record carries the **specific number/fact it asserts** (from B2), not only the sentence text — so "证据过于笼统" becomes "Suncor Q2 oil-sands output 740 kbbl/d (+5% q/q), per 6-K p.4" instead of a vague clause.
- **Acceptance:** where a fact was extracted, the evidence chip shows the quantified claim + provenance; where none was, it falls back to today's sentence-level snippet (no regression).

## Workstream E — Provenance & cross-data linkage (make the weak joins first-class)

Diagnosis (code, 2026-06-27): the semantic graph is well-linked — `edges.parquet` carries `evidence_chunk_ids`, `document_stated` edges require ≥1 evidence chunk (`extraction.py:817`), and company↔theme exposure is fully traceable to top evidence chunks (`exposure.py`). But three **reverse / origin joins are missing**, so the UI cannot answer obvious provenance questions without a multi-hop graph walk:

- **Theme → source documents:** no artifact maps a theme/community to its contributing documents. Today it takes a 5-hop reconstruction (`node_ids → entities → source_chunk_ids → chunks → documents`).
- **Company → contributing documents:** extraction drops the originating `document_id` when assembling entities (`extraction.py:722-774`), so "which documents drove Company X's exposure to Theme Y" is unanswerable.
- **Entity provenance:** `entities.parquet.source_chunk_ids` is a list with no join key — no materialized entity↔chunk↔document table.

This is load-bearing for C and D: EG-C's company-filtered evidence and EG-D's per-evidence facts both need the company→document origin that doesn't exist yet.

**Grain — a company traces to MANY themes (many-to-many):** `exposure.parquet` already emits one row per `(company_id, theme_snapshot_id, community_id)` and stores `top_evidence_chunk_ids` *per that pairing* (`exposure.py`, EXPOSURE_COLUMNS). The same document can justify a company's exposure to theme A and be irrelevant to theme B; one chunk can also back several `(company, theme)` pairs. So provenance must key on **`(company_id, theme_snapshot_id)`**, never collapse to company-level — otherwise the company page would attribute theme-A evidence to theme B.

- **Owner:** data-engineering
- **Files:** `app/backend/theme_engine/extraction.py` (carry `document_id` + its `company_id` onto each entity/edge occurrence); new provenance artifact contracts in `docs/io_contracts.md` + `docs/data_schema.md`; aggregation in `exposure.py` / `themes.py`; backend endpoints feeding EG-C; tests under `tests/`.
- **What:**
  1. **E1 — Entity provenance:** at extraction, preserve the originating `document_id` (and its `company_id`) per occurrence; materialize `entity_chunk_provenance` (`entity_id, chunk_id, document_id, company_id, available_at`). Stop discarding `document_id` in entity assembly.
  2. **E2 — Theme → documents:** materialize `theme_document_evidence` (`community_id → contributing chunk_ids / document_ids`, deduped) so a theme renders its sources in one read, no client-side walk.
  3. **E3 — `(Company, Theme)` → documents:** derive from E1, keyed on `(company_id, theme_snapshot_id)` to match the exposure grain. So the company page (EG-C) and quantified evidence (EG-D) list the exact documents/chunks behind *each* theme exposure separately — a company with N themes yields N evidence groups, and a chunk may legitimately appear under more than one.
- **Acceptance:**
  - Given a theme, one API read returns its contributing source documents (deduped, PIT-clean) — no graph walk in the client.
  - Given a company, return its theme exposures and, **per theme**, the documents/chunks behind that specific exposure; a company spanning multiple themes returns multiple distinct evidence groups (no cross-theme bleed, no collapse to a single company-level list).
  - Every provenance row is PIT-clean (`available_at ≤ as_of`); hermetic test on a committed fixture, including one company in ≥2 themes; no validation-only fundamentals read.
- **Why here:** depends on A's chunk schema (so provenance carries `section_title`/`block_type` context); must land before/with C, which consumes it. Runs in Phase B, parallel to B1/B2.

## Workstream F — Graph rendering: beautify now, swappable renderer for scale

Decision (2026-06-27): do **not** switch graph libraries now. "Looks bad" is ~90% styling, not D3's fault; and the layered-band + level/edge/degree filtering in `LayeredGraph.vue` is the hard, already-done part. Switching throws it away for marginal gain. Instead: beautify on D3, and decouple the render layer so a future SVG→Canvas swap (for node counts in the hundreds→thousands) is a local change, not a rewrite.

**Current encapsulation state (`app/frontend/src/components/LayeredGraph.vue`):**
- **Consumer boundary is clean** — props `{nodes, edges, activeHop}` + emit `node-click` (L34-39); the three views (Main/Themes/Graph) are renderer-agnostic. A library swap is invisible to them. Keep this contract.
- **Internal render layer is NOT decoupled** — `render()` (L72-145) fuses three concerns: (1) filter computation (L78-87, pure data, library-agnostic), (2) D3 force layout (L134-143), (3) SVG drawing (L89-132). `hoverNode/restore/highlight` (L147-170) poke D3 selections directly. So an SVG→Canvas swap today means rewriting `render()` + the highlight helpers.

- **Owner:** frontend
- **Files:** `app/frontend/src/components/LayeredGraph.vue`; new `useGraphModel.js` composable; optional `renderers/` (svg + future canvas).
- **What:**
  1. **Beautify (do now, on SVG):** curved/bezier edges with fade + tapered arrows; node gradient/halo (reuse the existing `defs` shadow); low-saturation research palette across the 6 level bands; label de-collision; tuned force params (charge/linkDistance/collide) for a less tangled layout.
  2. **Decouple (leave the seam):** extract the library-agnostic parts — filter computation and the `{nodes, edges, level, degree}` derivation — into a `useGraphModel` composable; define a thin renderer interface (`draw(model)`, `highlightHop(hop)`, `hoverNode(id)`, `destroy()`) with the current code as the **SVG renderer**. Props/emits contract unchanged.
  3. **Lean on semantic de-noise (the real scale fix):** default to community/theme nodes with expand-on-demand, plus the existing degree/level filters — so even at thousands of nodes the user never renders >~200 at once. A 1000-node hairball is unreadable in *any* library; aggregation beats raw render power.
- **Acceptance:**
  - Visual: edges/nodes/palette restyled; the three views render unchanged data through the new structure (no regression in filters, hover, hop-highlight, drag, zoom).
  - Decoupling: filter/model logic lives outside the renderer; swapping in a no-op/alternate renderer requires touching only the renderer module, not `useGraphModel` or the consumers.
  - A documented trigger: when sustained on-screen nodes exceed ~500 and frame rate drops, add a **Canvas renderer** behind the same interface (still D3 force). Only consider WebGL (Sigma) if a single view must render 5k+ raw nodes — not expected for an investment graph.
- **Why separate:** purely frontend, no backend/PIT dependency; can land any time, independent of A–E. Beautify is high-value/low-risk; the decouple is the cheap insurance against the "maybe thousands later" case.

### Explicitly deferred (non-goals this round)

- **Document nodes in the structural graph / community detection** (`graph_build.py:139` excludes them by design). "Which document best explains this theme" is answered by E2's aggregation instead of by graphing documents — kept out to avoid polluting community structure with source nodes.
- **`metadata_inferred` edges in exposure** (admitted to the graph at `graph_build.py:199` but excluded from exposure at `exposure.py:259`). The asymmetry is intentional for now; revisit only if exposure recall is too low. Tracked as a note, not built here.

---

## Dispatch plan (reviewed-team via Workflow, per the model that worked on PR #74)

Sequence (A is a hard prerequisite; B1/B2 then fan out; C/D last):

1. **Phase A** — single worker builds structure-preserving cleaning/chunking in an isolated worktree → adversarial verify (table-recovery + section-metadata negative tests) → Opus lead gate → integrate + CI → merge. **Block downstream until merged** (everything reads the new chunk schema).
2. **Phase B** — after A on main: three workers in parallel worktrees, B1 (XBRL adapter), B2 (LLM fact pass), and E (provenance/cross-data joins). Each self-verifies (hermetic fixtures). Then a **reconciliation verify** agent checks B1/B2 overlap handling, E's PIT-clean provenance, + the leakage guard (no validation-fundamentals read in discovery; `available_at ≤ as_of` everywhere). Opus lead gate → integrate → CI → merge. (E may split into its own PR if it lands ahead of B1/B2.)
3. **Phase C+D** — after B+E: one worker builds `CompanyView` + endpoints (C, consuming E's provenance joins) and folds quantified facts into evidence rendering (D). Verify PIT-clean display + no-silent-blank states. Lead gate → CI → merge.

Each phase = one PR, lead-approved, CI green, before the next starts. Worker agents on Sonnet; lead reviewer on Opus (per standing config).

## Tracking

File as GitHub issues to mirror this doc (keep the workstream id in the title):
- **EG-A** structure-preserving cleaning/chunking — blocking
- **EG-B1** XBRL as-reported fundamentals adapter (discovery-time, PIT)
- **EG-B2** LLM quantified-fact extraction pass (`FinancialMetric`/guidance)
- **EG-C** company detail page + endpoints
- **EG-D** evidence quantification
- **EG-E** provenance & cross-data linkage (entity/theme/company → source documents) — enables C/D
- **EG-F** graph rendering: beautify on D3 now + decouple renderer (swappable SVG→Canvas) — frontend-only, independent

Relates to existing: OI-2 (interpretive-edge discipline — B2 adds quantified facts under the same evidence rule), OI-4 (PIT alias resolution — company-fact attribution must resolve aliases point-in-time), #9/OI-8 (acquisition/vintage — B1 source vintage).
