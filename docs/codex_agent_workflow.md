# Codex Agent Workflow: PR + Issue Driven Execution

This is the execution protocol for Codex Agents in this repository.

## 1. Trigger and context

1. Watch repository state:
  - Open PRs with `gh pr list --state open ...`
  - Open issues with `gh issue list --state open ...`
2. A Codex Agent can start only if:
  - There is an open PR with an attached implementation scope (description, body, or comments), and
  - At least one linked issue is open or recently active, or explicit role tasks are missing state in board files.
3. Load PR scope first, then linked issues, then role boards:
  - `docs/pr_1_agent_assignments.md` (or equivalent assignment board for the PR)
  - `docs/open_issues.md`

## 2. Role resolution (internal, no GitHub identity needed)

Use role ownership by task type:

- `agent-doc-logic`: specification logic and metric semantics
- `agent-doc-validation`: validation rules, forward windows, rejection conditions
- `agent-doc-graph`: graph projection, edge discipline, exposure boundaries
- `agent-doc-architecture`: manifests, run/sweep, artifact layout, leakage gates
- `agent-doc-index`: documentation index and board consistency
- `agent-doc-issues`: issue decomposition, task formatting, dispatchability

If an issue has no clear role tag, default it to `agent-doc-issues`.

## 3. Issue-driven execution loop

For each issue selected by the agent:

1. Set PR/board status to `assigned`.
2. Resolve requirement into:
  - Owner
  - Files
  - Acceptance
3. Edit target artifacts with minimal changes.
4. Run self-check of acceptance condition(s) against changed text.
5. Mark status to `in-progress` -> `completed`.
6. Leave an issue comment with:
  - changed files
  - exact acceptance check results
  - remaining risk or follow-up

## 4. Required artifacts to keep in sync

- `docs/open_issues.md` as the dispatch board / task index.
- `docs/pr_1_agent_assignments.md` (or PR-specific assignment board).
- `INDEX.md` whenever a new/renamed source doc appears.
- PR body/comments for visibility when an issue is moved forward.

## 5. Mandatory completion criteria

A task is `completed` only when:

- Target files are updated.
- Cross-references are still valid.
- Board state has been updated (`assigned` -> `in-progress` -> `completed`) in the proper row.

## 6. Scope limits

- Primary edit surface:
  - `docs/`, `configs/`, `agents/`, `theme_discovery_engine_v1.md`, and PR board files.
- Do not modify unrelated files.
- Do not start validation runs or broad test suites unless explicitly requested.

## 7. Failure and handoff

When blocked:

- Move task to `blocked`.
- Record one blocking reason line and required input in both board + issue comment.
- Stop changes on that task until unblock signal arrives.

