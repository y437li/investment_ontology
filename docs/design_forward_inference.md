# Design: Forward-Looking Inference (theme/factor → implied company impact)

Status: `proposed` (design + dispatch plan; decisions locked 2026-06-28; no code yet)
Author: design review, 2026-06-27
GitHub issue: #97
Linked spec: `theme_discovery_engine_v1.md` §11 (theme = slice / everything-connected), §12 (slices), §20 (`io_contracts` fundamentals / validation)
Related design: `docs/design_evidence_granularity.md` (EG-B/E supply the quantified facts + provenance this layer consumes)

## Problem

The system is **entirely retrospective**. It explains what already happened; it never projects forward.

Confirmed in code (2026-06-27):
- `reasoning.py` — read-only narrative synthesis over existing edges/evidence; the system prompt forbids outside knowledge (`reasoning.py:180-186`). Produces `narrative` + `reasoning_steps` (provenance-labelled hops) + `reasoning_chain`, but **derives no forward conclusion** and writes no new graph data (cached JSON views only).
- `walk_forward.py` — historical backtest (PIT re-detection by month-end). Not forward.
- `validation.py` — measures **realized** forward-window returns (prices after `as_of`). Backtest, not projection.
- `exposure.py` — scores the **current** graph state (company→theme distance); no outward propagation.

So nothing answers the core research question: *"if this factor/theme moves, which companies are implied to be affected, in which direction, and why?"*

Note: the stale `docs/factor-propagation-architecture` branch is about the theme **hierarchy / structural lineage** (spec §11), **not** forward inference. This capability is genuinely undesigned.

## What we already have to build on

- **Directional, signed-ish edges already exist**: `causes`, `benefits` (+), `hurts` (−), `exposed_to`, `sensitive_to`, plus `located_in` (`configs/ontology.yml`; structural set in `graph_build.py`). These are the propagation substrate — they carry direction and (for benefits/hurts) an implicit sign, with `confidence` and `evidence_chunk_ids`.
- **A PIT-clean graph** (`available_at ≤ as_of` enforced on chunks/edges) — propagation can inherit this discipline.
- **A validation harness** (`validation.py`) that already computes realized forward returns — the natural scorer for "were the projections any good?"
- **GraphRAG reasoning** (`reasoning.py`) — the place to attach an evidence-backed "why" to each projected impact.

## ⚠️ Leakage guard (load-bearing)

Forward inference is the **most leakage-prone** thing in the system — it is literally about the future.
- Propagation may read ONLY edges/facts with `available_at ≤ run.as_of`. No restatements, no validation-only fundamentals (`io_contracts §20`, "discovery must not read validation-only fundamentals").
- A projection is a **hypothesis about the future**, never a fact. Every projected impact carries provenance (the edge path + evidence chunks that justify it) and a confidence, same discipline as `document_stated` edges.
- Validation (scoring projections vs realized returns) lives **after the freeze**, reading the validation artifacts; it must never feed back into the discovery-time projection. One-way only.

---

## Decisions locked (user, 2026-06-28)

1. **"Projection" = two-layer.**
   - **Deterministic propagation** (core, v1): a signed, decaying shock propagated along directional edges — transparent, auditable, cheap.
   - **LLM-reasoned impact** (flesh, v1.1): an evidence-backed narrative + sanity-check on each propagated impact. Rides on the deterministic layer; never invents edges.
   - Pure-LLM "what-if" with no graph propagation is rejected (unauditable, leakage-prone, no provenance spine).
2. **Output = a derived `projected_impacts` artifact** (PIT-clean, rebuilt per run, never restated) — must be ranked/validated/shown, which a pure on-the-fly view can't be backtested into.
3. **Trigger model = data-driven first.** **v1: event/theme activation only** — the system projects forward automatically when an existing Event or theme node fires (no user input). **v1.1: user-entered scenario shocks** ("oil −10%") as an interactive what-if sandbox. v1 reuses the existing pipeline; the scenario-input UI is deferred.
4. **v1 = direction + ordinal strength** (sign × decayed path weight), NOT calibrated % impact — honest about what the graph supports. Calibrated magnitude is a later, validation-gated step.

---

## Workstream P-A — Signed/weighted edge model (substrate)

- **Owner:** data-engineering + ontology
- **Files:** `configs/ontology.yml` (edge sign/polarity metadata), `app/backend/theme_engine/graph_build.py` (carry polarity + weight into the graph), schema notes in `docs/io_contracts.md`; tests under `tests/`.
- **What:** give directional edges an explicit **polarity** (`benefits=+1`, `hurts=−1`, `causes`/`exposed_to`/`sensitive_to` = signed per extracted direction) and a **propagation weight** derived from `confidence` (and optionally evidence count / recency). No new edges — annotate existing ones. Polarity comes from config + the edge's extracted direction, not hardcoded.
- **Acceptance:** every structural edge in the graph carries a polarity ∈ {+1, −1, 0/unknown} and a weight ∈ (0,1]; unit test on a fixture proves `hurts` → −1, `benefits` → +1, and an undirected/unknown edge → 0 (excluded from signed propagation); no change to community detection inputs.

## Workstream P-B — Propagation engine (deterministic core)

- **Owner:** data-engineering
- **Files:** new `app/backend/theme_engine/propagation.py` (mirror the read-from-frozen-artifacts pattern of `exposure.py`); tests under `tests/`.
- **What:** given a trigger node (factor/event/theme) and an initial signed shock, propagate outward along signed edges with **distance decay** (e.g. `impact(c) = Σ_paths sign(path) · Π weights · decay^len`), capped hop-count, sign-aware aggregation across multiple paths. Read-only over the PIT graph. Deterministic, seedless.
- **Acceptance:** on a hand-built fixture graph, a +shock on factor F yields the correct sign and ordering of impacted companies (e.g. a company linked F→benefits→C is +, F→hurts→C is −, a 2-hop path decays below a 1-hop path); hermetic; no network; respects `available_at ≤ as_of` (a future-dated edge does not contribute).

## Workstream P-C — Projection artifact + provenance

- **Owner:** data-engineering
- **Files:** new artifact contract `projected_impacts` in `docs/io_contracts.md` + `docs/data_schema.md`; writer in `propagation.py`; tests.
- **What:** persist `(run_id, as_of_date, trigger_id, trigger_kind, company_id, direction, strength, path, contributing_edge_ids, evidence_chunk_ids, confidence, method)`. `path` = the edge chain that produced the impact, so the UI can show *why*. PIT-clean by construction; derived/regenerable.
- **Acceptance:** for a real trigger in the universe, ≥1 projected impact written with a non-empty `path` and evidence chunks resolvable via the existing `source.py` chain; empty-but-schema-valid artifact when a trigger reaches no companies; every row PIT-clean.

## Workstream P-D — LLM-reasoned impact narrative (flesh, optional v1.1)

- **Owner:** extraction / reasoning
- **Files:** `app/backend/theme_engine/reasoning.py` (a projection-narrative pass), `configs/agents.yml` (prompt in the table). Reuses the evidence rule.
- **What:** for each top projected impact, synthesize an evidence-backed "why" (using the `path` + its evidence chunks) **and** a skeptical sanity check ("does the evidence actually support this direction?"). Never creates edges or numbers; if evidence is thin, it says so and downgrades confidence. Distinguish *projected/hypothetical* from *document-stated*.
- **Acceptance:** narrative cites only chunks in the impact's `path`; a thin-evidence impact is flagged low-confidence, not asserted; hermetic test with a fake client; no claim without an evidence chunk.

## Workstream P-E — Validation hook (is the projection any good?)

- **Owner:** validation
- **Files:** `app/backend/theme_engine/validation.py` (a projection-scoring pass), validation artifact in `docs/io_contracts.md`; tests.
- **What:** post-freeze, compare each projected `direction` (and ordinal `strength`) against **realized** forward-window returns (reuse `validation.py`'s existing forward-return machinery). Report hit-rate / rank correlation by trigger. **One-way**: scores never flow back into discovery-time projection.
- **Acceptance:** on a fixture with known forward returns, projection hit-rate is computed correctly; the scorer reads only validation artifacts; a leakage test proves no validation data is read during propagation.

## Workstream P-F — Projection UI (v1 data-driven; scenario input v1.1)

- **Owner:** frontend
- **Files:** new `app/frontend/src/views/ScenarioView.vue` + route; `app/frontend/src/api/themes.js`; endpoint `GET /api/themes/{run}/projections?trigger=...` (and a trigger list endpoint).
- **What:** **v1** — browse the system's auto-generated projections: pick from the data-driven triggers (an Event / theme that fired) → ranked list of implied company impacts, each with direction, ordinal strength, the **edge path** (reuse `LayeredGraph.vue` to draw the path), and evidence ("read full source" already exists). Explicit "hypothetical / projected" labelling — never styled like a stated fact. **v1.1** — add a user-entered scenario-shock input ("oil −10%") for interactive what-if (deferred).
- **Acceptance:** selecting a trigger renders ranked impacts with visible paths + evidence; PIT-clean (only `available_at ≤ as_of`); a no-reach trigger shows an explicit empty state; projections are visually distinct from document-stated evidence.

---

## Dispatch plan (reviewed-team via Workflow)

Sequence (P-A substrate first; P-B/P-C are the core; P-D/P-E/P-F ride after):

1. **Phase P-A** — annotate edges with polarity/weight; verify signs; lead gate → CI → merge. Blocks P-B.
2. **Phase P-B+P-C** — propagation engine + artifact in parallel-ish (C depends on B's output shape); adversarial verify (sign/decay/PIT) + leakage verify; Opus lead gate → merge.
3. **Phase P-D+P-E+P-F** — narrative, validation scorer, and scenario UI after the artifact exists. Verify evidence discipline + one-way validation + PIT display. Lead gate → merge.

Each phase = one PR, lead-approved, CI green. Worker agents on Sonnet; lead reviewer on Opus.

## Tracking

File as GitHub issues mirroring this doc (keep the id in the title); parent = #97:
- **FI-A** signed/weighted edge model
- **FI-B** deterministic propagation engine
- **FI-C** `projected_impacts` artifact + provenance
- **FI-D** LLM-reasoned impact narrative (v1.1)
- **FI-E** projection validation hook (post-freeze, one-way)
- **FI-F** scenario UI

Relates to: OI-2 (interpretive-edge discipline — projections are interpretive, same evidence rule), OI-3 (freeze/leakage tests — projection is the highest-risk leakage surface), EG-B/E (projections consume quantified facts + provenance).
