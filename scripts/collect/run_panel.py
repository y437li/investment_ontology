"""OI-6 R2 CLI driver — multi-period theme-discovery panel.

Runs the per-point discovery loop over a set of as_of points and builds the
run-level panel artifacts.  Hermetic by default (rule-based extractor when no
LLM env); real-LLM is selected by env only and is the operator's cost.

Usage:
  python scripts/collect/run_panel.py \
      --documents-dir data/inputs/documents \
      --source-manifest data/inputs/documents/news/source_manifest.csv \
      --as-of-dates 2024-03-31,2024-06-30 \
      [--run-id run_...] [--include-weak-signals] [--fact-extraction] [--no-resume]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "app" / "backend"))

from theme_engine import discovery_panel, runs  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="OI-6 R2 multi-period panel driver")
    ap.add_argument("--documents-dir", required=True)
    ap.add_argument("--source-manifest", required=True)
    ap.add_argument(
        "--as-of-dates",
        required=True,
        help="Comma-separated YYYY-MM-DD points, e.g. 2024-03-31,2024-06-30",
    )
    ap.add_argument("--run-id", default=None,
                    help="Reuse an existing run; omit to create a new one.")
    ap.add_argument("--include-weak-signals", action="store_true")
    ap.add_argument("--fact-extraction", action="store_true")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    dates = sorted(d.strip() for d in args.as_of_dates.split(",") if d.strip())

    if args.run_id:
        rid = args.run_id
        print(f"[run_panel] reusing run {rid} with points {dates}", flush=True)
    else:
        run = runs.create_run(RunCreateRequest(as_of_dates=dates, as_of_date=dates[-1]))
        rid = run.run_id
        print(f"[run_panel] created run {rid} with points {dates}", flush=True)

    result = discovery_panel.run_panel(
        rid,
        documents_dir=args.documents_dir,
        source_manifest_path=args.source_manifest,
        include_weak_signals=args.include_weak_signals,
        do_fact_extraction=args.fact_extraction,
        resume=not args.no_resume,
    )

    print(
        f"[run_panel] points_run={result.points_run} "
        f"points_skipped={result.points_skipped}",
        flush=True,
    )
    print("[run_panel] panel_summary:", flush=True)
    print(json.dumps(discovery_panel.panel_summary(rid), indent=2), flush=True)


if __name__ == "__main__":
    main()
