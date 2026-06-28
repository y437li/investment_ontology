# Design: Management Sentiment (hybrid lexicon + LLM)

Status: `proposed` (design + dispatch plan; no code yet)
Author: design review, 2026-06-28
Linked spec: `theme_discovery_engine_v1.md` §§6–8 (document spine), §11 (everything-connected / theme = slice)
Related designs: `docs/design_evidence_granularity.md` (EG-B2 is the sibling extraction pass; same skeleton+flesh+reconcile pattern), `docs/design_forward_inference.md` (sentiment is a forward signal — see leakage note)

## Problem

Evidence today is qualitative but **tone-blind**. The system records *what* was said (entities, relations, now quantified facts via EG-B2) but not *how management said it*. Management tone in MD&A and earnings-call remarks — confidence, hedging, evasiveness, forward-looking stance — is a well-studied leading signal that the pipeline currently throws away.

Confirmed against current code (2026-06-28):
- `extraction.py` extracts entities/relations + (post EG-B2) quantified `FinancialMetric` claims; **no tone/sentiment signal anywhere**.
- `configs/ontology.yml` has no sentiment entity/edge.
- Chunks now carry `block_type` / `section_title` (EG-A) and resolve to `document_id` → `company_id`, so management-attributable text (MD&A, transcript) is now addressable — the substrate exists.

## Decisions locked (this round)

- **Method = hybrid, both layers** (mirror EG-B1/B2): a deterministic **lexicon** baseline + a contextual **LLM** pass + a **fusion** layer whose headline output is the *disagreement* between them. Neither alone is sufficient — lexicon misses negation/context, LLM is non-deterministic and can hallucinate tone.
- **Lexicon = Loughran–McDonald (LM)**, kept **locally** under `data/lexicons/`. It is static reference data (a word→category CSV), academic-free to redistribute with attribution, computed purely locally → zero cost, fully PIT-safe, auditable (can name the triggering words). The generic Harvard-GI lexicon is rejected (misclassifies finance terms like "liability"/"cost").
- **Attribution is mandatory**: management tone (MD&A / earnings-call management remarks) ≠ media tone. Every sentiment record carries a `speaker_role` (management / analyst / media) + the source chunk. Do **not** conflate them.

## Decisions locked — round 2 (user, 2026-06-28)

1. **Ontology shape = artifact-first** (mirror EG-B2): a `management_sentiment` discovery artifact + `expresses_sentiment` edges (Company → Sentiment). Attribution rides as a `speaker_role` field — **no new `Management` entity type**.
2. **Tone representation** = the LM category vector (positive, negative, uncertainty, litigious, strong-modal, weak-modal) normalized by token count, PLUS a single fused `tone` ∈ {positive, neutral, negative, hedged}.
3. **Sentiment is discovery-evidence first** — shown, **not** scored into exposure. It becomes a *named, one-way input* to forward inference (#97) later, validated post-freeze; it is **never** silently folded into exposure scores. (See leakage note.)

## ⚠️ Leakage guard

- The LM layer is local + deterministic → trivially PIT-safe.
- The LLM layer + every sentiment record follow the existing discipline: `available_at ≤ run.as_of`, an evidence chunk required, confidence attached (same rule as `document_stated` edges / EG-B2 claims).
- **Sentiment is a forward-looking signal**, so it is the same leakage-prone class as forward inference (#97). Keep it on the discovery side as *evidence*; if it later feeds projection, that flows one-way and is validated post-freeze — never back into discovery.
- Attribution must be point-in-time (the speaker must be management of that company at that time) — reuse OI-4 PIT alias/entity resolution.

---

## Workstream S-A — Lexicon tone scorer (deterministic substrate)

- **Owner:** data-engineering
- **Files:** `data/lexicons/loughran_mcdonald.csv` (committed reference data + a LICENSE/SOURCE note); new `app/backend/theme_engine/sentiment_lexicon.py`; new `configs/sentiment.yml` (category set + weights + attribution rules — categories from config, not hardcoded); chunk-tone contract in `docs/io_contracts.md` + `docs/data_schema.md`; tests under `tests/`.
- **What:** for every chunk, compute an LM tone vector `{positive, negative, uncertainty, litigious, strong_modal, weak_modal}` as token-normalized counts, plus the matched word list (for auditability). Deterministic, local, no network. Tag `speaker_role` from chunk context (MD&A / transcript-management vs news) using `section_title`/`block_type` + document source.
- **Acceptance:** on a committed MD&A-style fixture, the scorer returns the correct category counts and the exact matched words; an uncertainty-heavy passage scores high `uncertainty`; hermetic (no network); a finance term like "liability" is NOT counted negative (proves LM, not GI). `speaker_role` correctly distinguishes a management section from a news chunk.

## Workstream S-B — LLM management-sentiment pass (contextual flesh)

- **Owner:** extraction
- **Files:** `app/backend/theme_engine/extraction.py` (a sentiment pass with its own tool schema), `configs/ontology.yml` (sentiment representation per locked ontology shape), `configs/agents.yml` (prompt in the table); tests with a fake client.
- **What:** on **management-attributable** chunks only (cost discipline), produce a structured judgment `(company, speaker_role, direction, confidence_tone, hedging, forward_stance, evidence_chunk_id)`. **Ground the LLM in S-A's matched words** — pass the LM uncertainty/negative hits into the prompt as evidence so the model judges from the text, not from the void. No record without an evidence chunk; distinguish reported tone from forward-looking stance.
- **Acceptance:** on a committed transcript fixture, ≥1 management-sentiment record with a direction, a forward stance, and a pointing evidence chunk; hermetic test injects a fake client; no record emitted without an evidence chunk; a negation case ("we do **not** see strong demand") is judged negative despite the word "strong" (proves context-awareness over the lexicon).

## Workstream S-C — Fusion / reconciliation (the headline value)

- **Owner:** extraction + data-engineering
- **Files:** fusion logic + `management_sentiment` artifact writer (mirror EG-B2's `financial_metrics.parquet`); artifact contract in `docs/io_contracts.md` + `docs/data_schema.md`; tests.
- **What:** reconcile S-A and S-B per (company, period/chunk): emit a final record with the LM vector, the LLM judgment, and an **`agreement` flag ∈ {agree, hedged, conflict}**. "LLM optimistic + LM uncertainty-dense → hedged" downgrades confidence. The disagreement is itself a first-class signal (management hedging), not noise to be averaged away.
- **Acceptance:** a fixture where LLM says positive but the text is uncertainty-dense yields `agreement = hedged` with reduced confidence; a clean-agreement fixture yields `agree`; the artifact is PIT-clean (`available_at ≤ as_of`) and every row resolves to an evidence chunk via the existing `source.py` chain.

## Workstream S-D — UI surfacing (rides on EG-C)

- **Owner:** frontend
- **Files:** sentiment panel in `app/frontend/src/views/CompanyView.vue` (the EG-C company page); evidence rendering.
- **What:** on the company page, a management-sentiment panel: fused tone over time, the `agreement`/hedged flag, and per-evidence tone (the chunk + its triggering LM words + the LLM read), with "read full source". Hedged/conflict states are visually explicit — never a single misleading "positive".
- **Acceptance:** the company page shows sentiment with its agreement flag and links each reading to its source chunk; a hedged reading is visibly distinct from a clean positive; PIT-clean.

---

## Dispatch plan (reviewed-team via Workflow)

Sequence (S-A substrate first; S-B then S-C; S-D after EG-C exists):

1. **Phase S-A** — local lexicon + deterministic scorer; verify (LM-not-GI, matched-words, speaker_role); Opus gate → CI → merge. Blocks S-B/S-C.
2. **Phase S-B + S-C** — LLM pass + fusion (S-C depends on S-B's output shape); adversarial verify (negation case, no-evidence drop, hedged/conflict logic) + leakage verify; Opus gate → merge.
3. **Phase S-D** — sentiment panel on the EG-C company page, after EG-C lands.

Each phase = one PR, lead-approved, CI green. Worker agents on Sonnet; lead reviewer on Opus. ⚠️ S-B edits `extraction.py` — sequence against any other extraction-touching work (e.g. EG-E/E1) to avoid the same conflict EG-B2/E faced.

## Tracking

File as GitHub issues mirroring this doc:
- **SENT-A** local LM lexicon + deterministic tone scorer
- **SENT-B** LLM management-sentiment pass (grounded by lexicon hits)
- **SENT-C** fusion/reconciliation (agreement flag: agree/hedged/conflict)
- **SENT-D** sentiment panel on the company page (rides on EG-C)

Relates to: EG-B2 (sibling extraction pass, same pattern), EG-C/EG-E (UI + provenance it surfaces through), #97 forward inference (sentiment as a forward signal — one-way, post-freeze validation), OI-2 (interpretive discipline), OI-4 (PIT attribution of the speaker).
