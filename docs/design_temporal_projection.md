# Design: Temporal run model, bipartite projection, vintage & forward-window (B-class)

Status: `proposed` (design + dispatch plan; decisions locked 2026-06-28; OI-7 implementable now, OI-5/OI-6 implement after review)
Author: design review, 2026-06-28
Covers GitHub issues: OI-8 #9, OI-5 #6, OI-6 #7, OI-7 #8
Relates to: OI-1 #2 (walk-forward validation — already implemented), OI-3 #4 (leakage gates), OI-4 #5 (PIT alias).

## Why these four together

They are the **temporal + structural spine** of the engine, and they interlock:

```
OI-8 (vintage)  →  defines available_at  →  the PIT foundation everything stands on
OI-5 (projection) → defines what a "theme" IS at a single time point
OI-6 (multi-period run) → loops the whole discovery over time → time-series panel
OI-7 (forward window) → how each point's outcome is measured
```

Decided as a set (user, 2026-06-28) so the run model, theme definition, and validation window are coherent rather than retrofitted.

---

## OI-8 — Source vintage & `available_at` (design only; the PIT foundation)

**Locked rule:** `available_at` = the **source's publication time**, authoritatively:
- filings → `filing_date` (first publication on EDGAR/SEDAR+)
- news → `published_at`
- prices/fundamentals → the as-reported publication date

**Discipline:**
- Ingest is **read-only on the timestamp**: the pipeline reads the source's publish time and stamps `available_at`; it never invents or shifts it.
- A source with **no determinable publish time is REJECTED (fail-closed)** at ingest — quarantined, not admitted with a guessed date. (Consistent with OI-3 fail-closed PIT and #77.)
- `available_at` is set ONCE at ingest and is immutable downstream; every later artifact inherits it.

**Deliverable:** this is design-only — the rule is documented in spec §6/§16 + `agents/data_ingestion_agent.md` + `docs/io_contracts.md` (raw_documents / documents schema), and the ingest quarantine path enforces "no publish time → reject." No new engine subsystem.

**Why it's first:** every other temporal guarantee (OI-3 gates, OI-1 walk-forward, OI-6 multi-period) is only as sound as the `available_at` stamp. Get the stamp authoritative, or the whole PIT edifice is built on sand.

---

## OI-5 — Bipartite company↔concept projection (what a theme IS)

**Locked decision:** detect themes on a **bipartite projection (companies ↔ concepts), keeping both sides** — not on the raw heterogeneous graph (current), not on a collapsed company-company graph.

**Design:**
- Build a bipartite graph: one side = `Company` entities; other side = the binding nodes (`EconomicConcept`, `Commodity`, `MacroIndicator`, `Event`). Edges = the structural relations between them (PIT-filtered).
- **Community detection runs on this bipartite structure** (or its weighted company-projection derived from shared concepts). A theme = a **cluster of companies + the concepts that bind them** — so every theme answers "which companies, and *because of which concepts*."
- Keep **both sides** in the theme artifact: the company members AND the concept "spine," so the existing reasoning/UI can explain the cluster, not just list companies.
- The heterogeneous full graph stays for evidence/provenance; the bipartite projection is the community-detection input.

**Acceptance (design):** spec §11 defines the bipartite projection + that a theme carries its concept spine; community detection consumes the projection; existing PIT discipline preserved (projection built only from `available_at ≤ as_of` edges).

**Why before OI-6:** the projection is the *unit* that OI-6 re-detects at each time point. Define it once, then loop it.

---

## OI-6 — Native multi-period run (the big re-architecture)

**Locked decision:** a **run is natively multi-period**. Discovery re-runs at each monthly as_of point; the run produces a **time-series panel**, not a single cross-sectional draw.

**This is a major re-architecture — flagged as such.** It changes the run model itself:
- **Run manifest** carries an ordered list of monthly as_of points (reuse OI-1's `walk_forward.as_of_dates` shape).
- At each point `t_i`, run the discovery pipeline **PIT to `t_i`** (clean→extract→graph→**OI-5 projection**→themes→exposure), using only `available_at ≤ t_i` data. Artifacts become **per-point** (e.g. `discovery/<t_i>/...`) or gain a `time_point` dimension.
- The run output is a **panel**: theme emergence/persistence across points, exposure trajectories per company, so you can see a theme *form* rather than a single snapshot.
- **Subsumes OI-1**: OI-1's walk-forward validation currently varies only the entry date over a fixed frozen snapshot; under OI-6, each point has its *own* PIT discovery, so validation becomes genuinely out-of-sample per point. OI-1's `claim_supported` rule (≥3 points) maps directly onto the run's points.

**Cost & risk (be honest):** run cost ≈ ×N points; the artifact layout, freeze/hash gate (OI-3), provenance (EG-E), and every endpoint that reads "the" discovery artifacts must become point-aware. This is the largest single change in the project.

**Therefore: design-and-review FIRST, implement phase-by-phase.** Proposed phases:
1. **R1 — run model & layout:** manifest point-list, per-point artifact layout, freeze/hash per point, point-aware run_cache + leakage gate. No new discovery logic yet — just make the pipeline runnable at an arbitrary `t_i` and stored per point.
2. **R2 — drive the loop:** orchestrate clean→…→exposure across the point list (reusing R1 layout); produce the panel artifact (theme lineage + exposure trajectories).
3. **R3 — panel UI + validation:** time-series views (theme emergence, exposure trajectory); wire OI-1 validation to the per-point panel; OI-7 window per point.

Each phase is its own design-checkpoint + dispatch. Do NOT one-shot.

**Acceptance (design):** spec §6/§22/§27 define the multi-period run, per-point PIT layout, and the panel; dependency on OI-5 (the per-point detection unit) and OI-8 (the `available_at` it filters on) stated.

---

## OI-7 — Forward window & coverage (implementable now)

**Locked decision:** **3-month forward window**; when a point lacks sufficient forward price coverage, **skip that point** (do NOT shrink the window).

**Design / implementation:**
- `configs/validation.example.yml`: `sweep.forward_window: 3M` (from the current default), and a coverage policy `on_insufficient_coverage: skip` (vs shrink).
- `validation.py`: the existing `_check_forward_coverage` already gates points; make it **skip** a point lacking ≥3M of forward prices (rather than admit a clamped/short window), and record the skipped points + reason in the panel.
- Ties into OI-1: a skipped point doesn't count toward `n_points` for `claim_supported`.

**Acceptance:** a fixture point with <3M forward coverage is SKIPPED (not shrunk), excluded from `n_points`, and reported as skipped; window is 3M from config; spec §22 states the skip-not-shrink rule. **This is small and self-contained — can ship without the OI-5/OI-6 re-architecture.**

---

## Dispatch / sequencing

```
OI-8 (vintage rule + ingest reject)        — design-only doc + ingest fail-closed   [light]
OI-7 (3M window, skip-not-shrink)           — config + validation.py + tests          [light, ship now]
OI-5 (bipartite projection)                 — design then implement (graph/themes)    [moderate]
OI-6 (multi-period run) R1 → R2 → R3        — design-checkpoint per phase             [major]
```

Recommended order: **OI-8 + OI-7 first** (light, foundational/independent), then **OI-5** (defines the unit), then **OI-6** phased (consumes OI-5 + OI-8). OI-6 is the project's largest change and is gated on its own design review per phase.

Worker agents on Sonnet; lead reviewer on Opus; each phase = one PR, lead-approved, CI green, PIT/leakage discipline preserved throughout.
