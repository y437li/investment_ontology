# Open Issues

Tracked design gaps in `theme_discovery_engine_v1.md` that need an implementation or design decision before or during the relevant milestone. Each item is written as a dispatchable task: any executor (Claude Code or Codex CLI) should be able to pick it up from the **Owner**, **Files**, and **Acceptance** fields without further scoping.

Status legend: `open` | `assigned` | `in-progress` | `completed`

Tasks are dispatched as GitHub issues; keep the OI id in the issue title so the two stay linked.

---

## PR-1 Execution Dispatch Board

Current PR scope: `#1 Spec: fix single-snapshot metric logic, add MVP caveats and open-issues tracker`  
Current source of truth: `theme_discovery_engine_v1.md` + `docs/open_issues.md`

This board assigns work by agent role (not GitHub user) for immediate parallel execution.

## Codex 队友固定责任映射

当前 OI 由以下内部队友负责：

- `agent-doc-logic`：OI-1、OI-7（度量口径、Walk-forward 条件与研究声明边界）
- `agent-doc-validation`：OI-1、OI-7、OI-6（验证范围、拒绝条件、运行状态语义）
- `agent-doc-issues`：OI-8、OI-4、OI-5（Issue 任务可执行化与同步）
- `agent-doc-graph`：OI-2、OI-5（边/图结构治理、社区检测前投影）
- `agent-doc-architecture`：OI-3、OI-6（清单/扫掠模型、冻结与 artifact 约束）
- `agent-doc-extraction`：OI-4（点时别名解析与全局别名隔离）
- `agent-doc-data-engineering`：OI-8（采集源、历史版本、as-reported 历史值）
- `agent-doc-index`：INDEX 与文档索引同步（持续维护）

你看到的 GitHub issue 目前会与角色同步，而不是直接绑定外部人员。

Dispatch mode: repo-internal role assignment (no external teammate assignment available on this repository currently).

| Agent role | OI id | Task | Primary artifact | Current state |
|---|---|---|---|---|
| `agent-doc-logic` | OI-1 / OI-7 | Finalize single-snapshot vs walk-forward metric logic, forward-coverage precondition wording, and keep claims honest | `theme_discovery_engine_v1.md` §§20/21/22 | completed |
| `agent-doc-validation` | OI-1 / OI-7 / OI-6 | Align validation scope language and rejection conditions for missing forward data windows | `theme_discovery_engine_v1.md`, `configs/validation.example.yml` | completed |
| `agent-doc-issues` | OI-8 / OI-4 / OI-5 | Convert critical design questions into implementable issue tasks; keep wording dispatchable with Owner / Files / Acceptance | `docs/open_issues.md` | completed |
| `agent-doc-graph` | OI-2 / OI-5 | Clarify discovery/exposure edge discipline and explicit entity-only graph projection for community detection | `theme_discovery_engine_v1.md`, `docs/io_contracts.md` | completed |
| `agent-doc-architecture` | OI-3 / OI-6 | Define manifest + leakage gate semantics and walk-forward sweep/run model | `theme_discovery_engine_v1.md`, `docs/io_contracts.md` | completed |
| `agent-doc-extraction` | OI-4 | Point-in-time alias resolution and non-temporal diagnostics alias artifact split | `theme_discovery_engine_v1.md`, `agents/extraction_agent.md`, `docs/io_contracts.md` | completed |
| `agent-doc-index` | all | Keep `INDEX.md` synchronized with every added/renamed artifact and status updates; verified no new files need indexing after this PR round | `INDEX.md` | completed |
| `agent-doc-data-engineering` | OI-8 | Data acquisition ownership, source vintage, and as-reported fundamental discipline | `theme_discovery_engine_v1.md`, `agents/data_engineering_agent.md`, `docs/data_schema.md` | completed |

Dispatch note:
- OI-1..OI-8 are dispatched as GitHub issues #2..#9 and also mirrored by this in-repo role board.
- No external teammates are currently requested on PR #1; assignment is role-internal by design.

## OI-1 Minimal walk-forward for a real research claim

- Status: in_progress
- Affects: sections 1, 22, 28; Milestone 6
- Owner: Research / Quant Engineer (`agents/validation_agent.md`)
- Conflict: The product goal (section 1) is to validate whether discovered communities relate to future outcomes, but the MVP uses a single `as_of_date`, which is a single cross-sectional draw and cannot support statistical association. Walk-forward (section 22) is currently "Later".
- Proposed resolution: Pull a minimal walk-forward (3-4 monthly time points) into MVP scope so the core hypothesis is testable. Until then, validation output stays labeled illustrative (section 2 MVP Caveats).
- Files: `theme_discovery_engine_v1.md` (sections 22, 27 Milestone 6), `configs/validation.example.yml`, `agents/validation_agent.md`.
- Acceptance: spec defines a minimal walk-forward (>= 3 time points) with explicit sweep semantics; validation config carries the time-point list; no excess-return claim is presented from a single snapshot.
- Decision needed by: Milestone 6 planning.

## OI-2 Discipline for interpretive edges (`benefits` / `hurts` / `exposed_to` / `causes`)

- Status: completed
- Affects: sections 7, 9.4, 9.7, 17
- Owner: LLM / Extraction Engineer (`agents/extraction_agent.md`)
- Conflict: Section 17 forbids LLM reasoning in the discovery layer, but these edge types are causal/benefit judgments extracted by the LLM, and they feed exposure -> baskets -> validation. This is reasoning entering discovery through the back door.
- Proposed resolution: Restrict these edges to relationships explicitly stated in a document, each with an evidence chunk. Tag any LLM-inferred relationship with `extraction_method=llm_inferred` and exclude it from exposure by default; treat it as a separate weak signal.
- Files: `theme_discovery_engine_v1.md` (sections 7, 17), `agents/extraction_agent.md`, `docs/io_contracts.md` (edge schema), `agents/graph_theme_agent.md` (exposure filter).
- Acceptance: edge schema has an `extraction_method` enum; exposure computation excludes `llm_inferred` edges by default; spec states the stated-vs-inferred rule.
- Decision needed by: Milestone 3.

## OI-3 Freeze enforcement and automated leakage tests

- Status: completed
- Affects: sections 8, 9.8, 16, 18; Milestones 5-6
- Owner: Data Architect (`agents/data_architect_agent.md`) + Research / Quant Engineer
- Conflict: "Validation cannot read future data before freeze" is a process convention with no mechanism. Required artifacts (section 8) mix discovery artifacts and future data (`market_prices`, `fundamentals`) in one run directory.
- Proposed resolution:
  - Physically separate `data/runs/<run_id>/discovery/` and `.../validation/`; future data writes only to the latter.
  - On freeze, hash all discovery artifacts into `run_manifest.json`; validation startup verifies hashes and aborts if missing or changed.
  - pytest gates (under top-level `tests/`): (a) every evidence chunk entering `Graph(t)` has `available_at <= as_of_date`; (b) every price/fundamental row read by validation is dated after `as_of_date` and is read only after freeze.
- Files: `theme_discovery_engine_v1.md` (sections 8, 16), `docs/io_contracts.md` (run layout, manifest), `agents/validation_agent.md`, `tests/` (new leakage tests).
- Acceptance: run layout splits discovery/validation; freeze writes artifact hashes. Freeze hash preflight is implemented in commit `54f535d`; two leakage pytest gates are added, but full validation pipeline is still not yet wired in PR #19.
- Decision needed by: Milestone 5.

## OI-4 Point-in-time entity / alias resolution

- Status: completed
- Affects: sections 10, 15, 16
- Owner: LLM / Extraction Engineer (`agents/extraction_agent.md`)
- Conflict: Section 15 filters documents/entities/edges by `available_at <= t`, but alias merging (section 10) uses embeddings over the full corpus, so future documents can shape the canonical entity set at time `t`. Section 16 lists this as weak leakage but only "monitor".
- Proposed resolution: Build the alias table for `Graph(t)` using only documents with `available_at <= t`. Keep a global alias table separately for non-temporal inspection.
- Files: `theme_discovery_engine_v1.md` (sections 10, 15), `agents/extraction_agent.md`, `docs/io_contracts.md` (alias schema).
- Acceptance: spec states alias resolution is point-in-time; alias artifact records the `as_of` used; global table is separate and clearly non-temporal.
- Decision needed by: Milestone 4.

## OI-5 Graph projection before community detection

- Status: completed
- Affects: sections 7, 11
- Owner: Research / Quant Engineer (`agents/graph_theme_agent.md`)
- Conflict: `Document` is a node type and `mentioned_in` is an edge. Running Louvain/Leiden on a graph containing Document nodes makes documents high-degree hubs and clusters by co-membership rather than economic relationship.
- Proposed resolution: Define an explicit projection to an entity-only graph (Document nodes and `mentioned_in` edges used for evidence, not for community structure) before community detection.
- Files: `theme_discovery_engine_v1.md` (sections 7, 11, 15), `agents/graph_theme_agent.md`, `docs/io_contracts.md` (graph.json contract).
- Acceptance: spec defines the entity-only projection used for community detection; `graph.json` distinguishes evidence edges from structural edges.
- Decision needed by: Milestone 4.

## OI-6 Run model vs multi-period walk-forward

- Status: completed
- Affects: sections 8, 22
- Owner: Data Architect (`agents/data_architect_agent.md`)
- Conflict: `run_manifest.json` carries a single `as_of_date`, but walk-forward builds graphs at many `t`. The single-manifest model cannot express a run with multiple time points.
- Proposed resolution: Define one run = one `as_of_date`, and model a walk-forward as a parent sweep over child runs (e.g. `sweep_manifest.json` referencing child run ids). Confirm before OI-1.
- Files: `theme_discovery_engine_v1.md` (sections 8, 22), `docs/io_contracts.md` (manifest + sweep contract).
- Acceptance: spec defines run-vs-sweep relationship and a `sweep_manifest.json` schema referencing child run ids.
- Decision needed by: Milestone 6 planning.

## OI-7 Forward-window vs data coverage constraint

- Status: completed
- Affects: sections 19, 22
- Owner: Research / Quant Engineer (`agents/validation_agent.md`)
- Conflict: 1M/3M forward returns require `as_of_date` to sit at least 3 months before the end of available data. With 12-24 months of data and a late `as_of_date`, forward returns may not exist.
- Proposed resolution: Add a validation precondition that rejects an `as_of_date` lacking sufficient forward coverage for the configured holding periods.
- Files: `theme_discovery_engine_v1.md` (sections 19, 22), `agents/validation_agent.md`, `configs/validation.example.yml`.
- Acceptance: validation refuses to run when `as_of_date` + max holding period exceeds the available price coverage, with a clear error.
- Decision needed by: Milestone 6.

## OI-8 Source-data acquisition and vintage ownership (design only)

- Status: completed (design); spec §6, `agents/data_engineering_agent.md`, and `docs/data_schema.md` now state PIT acquisition + as-reported-fundamentals rules, the free source stack, and the deferral/trigger. No collection code written.
- Affects: sections 6, 9.2, 16; Milestone 2
- Owner: Data Engineer (`agents/data_engineering_agent.md`) with Data Architect review
- Context: There is currently no source data at all, and no agent owns *acquiring* it. `data_ingestion_agent` only registers files that already exist; nobody owns fetching filings/news/prices/macro/fundamentals and stamping `published_at` / `available_at` / data vintage. This is the single largest point-in-time risk: live web/API pulls return today's revised values and survivorship-biased membership.
- Scope decision: MVP does NOT add an autonomous web-scraping collection agent. Acquisition responsibility is folded into `data_engineering_agent` ("source adapters") with explicit point-in-time discipline. A dedicated `data_collection_agent.md` is only split out later when section 29 alt-data / API sources land.
- Proposed resolution (design, not execution):
  - Add an acquisition + vintage hard rule to `agents/data_engineering_agent.md`: every fetched record carries `published_at`, `available_at`, and source vintage; fundamentals use as-reported historical values only (never live/restated); universe membership at `t` is recorded to avoid survivorship bias.
  - Add a sentence to section 6 requiring point-in-time acquisition and as-reported fundamentals.
  - Document the recommended free, point-in-time-friendly source stack (filings via SEC EDGAR / SEDAR+, prices, macro via FRED / Bank of Canada, fundamentals via EDGAR XBRL) and the known-hard sources (point-in-time news, transcripts) as deferred.
  - Record the trigger condition for promoting acquisition into a dedicated `data_collection_agent.md`.
- Files: `theme_discovery_engine_v1.md` (section 6), `agents/data_engineering_agent.md`, `docs/data_schema.md` (source/vintage fields).
- Acceptance: spec and the data engineering agent state the point-in-time acquisition + as-reported-fundamentals rule; source-stack and deferral are documented; no collection code is written.
- Decision needed by: Milestone 2.
- Completion note:
  - `theme_discovery_engine_v1.md` section 6 now includes an explicit promotion trigger for `data_collection_agent.md`.
  - `agents/data_engineering_agent.md` now records immutable source-vintage policy and hard-source escalation.
  - `docs/data_schema.md` now documents source-time provenance fields for ingest rows.
