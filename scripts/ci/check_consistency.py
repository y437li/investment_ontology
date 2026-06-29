#!/usr/bin/env python3
"""Spec/docs consistency and leakage guardrail checks for investment_ontology.

This is the "test suite" for a currently docs-first repo: it enforces the
invariants the spec relies on so a drifting PR fails CI instead of silently
breaking the design. All checks are stdlib-only and run from the repo root.

Exit code 0 = all checks pass; 1 = at least one failure.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "theme_discovery_engine_v1.md"
INDEX = REPO / "INDEX.md"
OPEN_ISSUES = REPO / "docs" / "open_issues.md"

# §25 required agents and §26 required skills (kept in sync with the spec).
REQUIRED_AGENTS = [
    "orchestrator", "data_architect_agent", "data_engineering_agent",
    "data_ingestion_agent", "data_cleaning_agent", "extraction_agent",
    "graph_theme_agent", "validation_agent", "frontend_report_agent",
]
REQUIRED_SKILLS = [
    "point_in_time_data", "unstructured_data_cleaning",
    "entity_relation_extraction", "temporal_graph_discovery",
    "validation_backtest", "evidence_report_generation",
    "backend_api_implementation", "pipeline_artifact_implementation",
    "frontend_workflow_implementation", "test_quality_gate",
    "maintainable_code_implementation",
]
ALLOWED_EXTRACTION_METHODS = {"document_stated", "llm_inferred", "metadata_inferred"}

failures: list[str] = []
passes: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passes if ok else failures).append(name if ok else f"{name} -> {detail}")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def git_tracked(pathspec: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", pathspec], cwd=REPO, capture_output=True, text=True
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    # 1. Required agent specs exist.
    missing = [a for a in REQUIRED_AGENTS if not (REPO / "agents" / f"{a}.md").exists()]
    check("required agent specs exist", not missing, f"missing: {missing}")

    # 2. Required skill specs exist.
    missing = [s for s in REQUIRED_SKILLS if not (REPO / "skills" / f"{s}.md").exists()]
    check("required skill specs exist", not missing, f"missing: {missing}")

    # 3. INDEX.md file references resolve.
    broken = []
    for m in re.finditer(r"`([^`]+)`", read(INDEX)):
        ref = m.group(1)
        if "/" in ref and (ref.endswith("/") or re.search(r"\.\w+$", ref)):
            if not (REPO / ref).exists():
                broken.append(ref)
    check("INDEX.md references resolve", not broken, f"broken: {sorted(set(broken))}")

    # 4. extraction_method values are within the allowed enum.
    bad = set()
    for p in [SPEC, *(REPO / "docs").glob("*.md"), *(REPO / "agents").glob("*.md")]:
        for m in re.finditer(r"extraction_method\s*=\s*`?([a-z_]+)`?", read(p)):
            v = m.group(1)
            if v not in ALLOWED_EXTRACTION_METHODS:
                bad.add(v)
    check("extraction_method enum consistent", not bad, f"unexpected: {sorted(bad)}")

    # 5. Spec has its full numbered section sequence (1..30).
    nums = [int(n) for n in re.findall(r"^# (\d+)\.", read(SPEC), re.M)]
    check("spec sections 1..30 present", nums == list(range(1, 31)),
          f"found {len(nums)} sections: gaps={sorted(set(range(1, 31)) - set(nums))}")

    # 6. available_at mandatory statement present (core PIT invariant).
    check("available_at mandatory rule present",
          "available_at` is mandatory" in read(SPEC), "missing in spec §6")

    # 7. LEAKAGE GUARD: no raw input/run/db/cache data committed (only .gitkeep).
    # Reference data (e.g. data/lexicons/ — the Loughran-McDonald tone lexicon a
    # code path depends on) is allowlisted: it is neither raw input nor run/cache
    # data, and shipping it keeps CI hermetic (no download at test time).
    _DATA_ALLOWLIST = ("data/lexicons/",)
    leaked = [f for f in git_tracked("data/")
              if not f.endswith(".gitkeep") and "/.git" not in f
              and not f.startswith(_DATA_ALLOWLIST)]
    check("no committed data/ payloads", not leaked, f"committed: {leaked}")

    # 8. Every OI in open_issues.md is dispatchable (Owner / Files / Acceptance).
    text = read(OPEN_ISSUES)
    blocks = re.split(r"^## (OI-\d+.*)$", text, flags=re.M)
    incomplete = []
    # re.split yields [pre, header1, body1, header2, body2, ...]
    for i in range(1, len(blocks), 2):
        header, body = blocks[i], blocks[i + 1]
        oid = header.split()[0]
        if not all(k in body for k in ("Owner:", "Files:", "Acceptance:")):
            incomplete.append(oid)
    check("OI tasks have Owner/Files/Acceptance", not incomplete,
          f"incomplete: {incomplete}")

    # 9. tests live under tests/ only (no stray test files next to app/scripts code).
    stray = [f for f in git_tracked("*")
             if re.search(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py)$", f)
             and not f.startswith("tests/")]
    check("tests live under tests/ only", not stray, f"stray tests: {stray}")

    # Report.
    print(f"PASS ({len(passes)}):")
    for p in passes:
        print(f"  ✓ {p}")
    if failures:
        print(f"\nFAIL ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("\nAll consistency checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
