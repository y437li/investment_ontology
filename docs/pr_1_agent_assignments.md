# PR #1 Agent Execution Assignments

Scope:
- PR: [#1](https://github.com/y437li/investment_ontology/pull/1)
- Branch: `spec/logic-fixes-and-open-issues`
- Repository: `y437li/investment_ontology`

Working mode:
- GitHub PR #1 has no active review requests; work is internally staged by role first.
- Canonical backlog now lives in GitHub issues #2..#9 and is mirrored here for role-based execution.

Dispatch submission:
- [x] 分配说明已提交：每个 role 已在此文件标注并可独立并行执行
- [x] 进度回写入口已建立：每项完成后更新本文件和 [docs/open_issues.md] board
- [ ] 如需对外并行协作：补充具体 @GitHub 用户名后，可一键迁移到 assignee/reviewer。

## 1) Task Allocation (now)

| Agent role | Deliverable | Source | Due | Status |
|---|---|---|---|---|
| `agent-doc-logic` | Single-snapshot metric logic language and caveats | `theme_discovery_engine_v1.md` | immediate | completed |
| `agent-doc-validation` | Validation coverage and forward-window rule wording | `theme_discovery_engine_v1.md`, `configs/validation.example.yml` | immediate | completed |
| `agent-doc-issues` | Open-issues tracker quality gate and dispatchability | `docs/open_issues.md` | immediate | completed |
| `agent-doc-graph` | Graph projection and edge semantics consistency checks | `theme_discovery_engine_v1.md` | immediate | completed |
| `agent-doc-extraction` | Point-in-time alias resolution and global diagnostics split | `theme_discovery_engine_v1.md`, `agents/extraction_agent.md`, `docs/io_contracts.md` | immediate | completed |
| `agent-doc-architecture` | Manifest + sweep model and leakage gate updates | `theme_discovery_engine_v1.md`, `docs/io_contracts.md` | immediate | completed |
| `agent-doc-index` | Navigation consistency for changed docs | `INDEX.md` | immediate | completed |
| `agent-doc-data-engineering` | Source acquisition, vintage hard rules, as-reported fundamentals policy | `theme_discovery_engine_v1.md`, `agents/data_engineering_agent.md`, `docs/data_schema.md` | immediate | completed |

## 2) Update rhythm

- PR #1 status is checked before each round.
- Any change in `theme_discovery_engine_v1.md` requiring new tasks updates both:
  - `docs/open_issues.md` dispatch board
  - this assignment file

## 3) Completion rule

- A task is considered complete only when:
  - the target artifact is updated,
  - cross-references remain valid,
  - and the status is updated to `completed` in the dispatch board.
