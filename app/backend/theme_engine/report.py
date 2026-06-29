"""M7: Artifact-backed research report generation (ReportAgent, spec §9.9).

Reads (all from existing run artifacts — no new computation):
  - ``run_manifest.json``                            (io_contracts §2)
  - ``discovery/communities.json``                   (io_contracts §14)
  - ``discovery/theme_snapshots.json``               (io_contracts §15)
  - ``discovery/theme_metrics.parquet``              (io_contracts §17)
  - ``discovery/company_theme_exposure.parquet``     (io_contracts §18)
  - ``validation/validation.csv``                    (io_contracts §22, optional)

Writes:
  - ``report.md``                                    (io_contracts §23)

CONTRACT RULES (spec §9.9, §2 MVP Caveats, io_contracts §23):
  - Report is assembled FROM EXISTING ARTIFACTS ONLY.
    Do NOT compute new discovery/validation numbers.
  - Every key claim links to a specific artifact/evidence id.
  - NO unsupported prediction or investment claims.
  - If validation is absent or illustrative-only (single-snapshot),
    the report must say so and carry the illustrative/no-alpha caveat.
  - DETERMINISTIC: same run -> identical report.md bytes.
  - LLM narration is behind an injectable NarratorInterface;
    the default (used in all tests) is deterministic and makes no network call.

Forbidden phrases:
  'will outperform', 'guaranteed', 'buy', 'sell', 'alpha claim',
  'proven alpha', 'statistical significance' (without an explicit caveat).
"""

from __future__ import annotations

import csv
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from . import run_cache, runs

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Illustrative caveat text (mirrors validation._SINGLE_SNAPSHOT_CAVEAT)
# ---------------------------------------------------------------------------

_ILLUSTRATIVE_CAVEAT = (
    "**ILLUSTRATIVE ONLY — SINGLE-SNAPSHOT MVP**: "
    "One as_of_date over this universe yields a single cross-sectional draw "
    "which cannot support any statistical claim that themes are associated with "
    "future outcomes. No alpha or causal claim is made. "
    "Backtesting requires a multi-period walk-forward panel (spec §2)."
)

_VALIDATION_ABSENT_NOTE = (
    "**Validation artifacts not present**: `validation/validation.csv` was not "
    "found for this run. Forward-return validation has not been executed, or "
    "was blocked due to insufficient forward price coverage. "
    + _ILLUSTRATIVE_CAVEAT
)

_FORBIDDEN_PHRASES = frozenset(
    {
        "will outperform",
        "guaranteed",
        " buy ",
        " sell ",
        "proven alpha",
        "statistical significance",
    }
)

# ---------------------------------------------------------------------------
# Narrator interface (injectable; default is deterministic, no network calls)
# ---------------------------------------------------------------------------


class NarratorInterface(ABC):
    """Abstract narrator for report section prose.

    The default implementation (DeterministicNarrator) is used in tests.
    An LLM-backed narrator may be injected at runtime but MUST NOT be called
    in code paths exercised by tests.
    """

    @abstractmethod
    def describe_theme(self, theme_name: str, summary: str, metrics: dict) -> str:
        """Return a short prose description of a theme given its metadata."""

    @abstractmethod
    def describe_validation_summary(self, validation_rows: list[dict]) -> str:
        """Return a prose summary of validation results."""


class DeterministicNarrator(NarratorInterface):
    """Deterministic narrator that produces stable output from artifact data.

    No network calls. No randomness. Used as the default in all code paths,
    including tests.
    """

    def describe_theme(self, theme_name: str, summary: str, metrics: dict) -> str:
        strength = metrics.get("strength")
        cohesion = metrics.get("cohesion")
        parts = [f"Theme: **{theme_name}**."]
        if summary:
            parts.append(summary)
        if strength is not None:
            parts.append(f"Strength score: {float(strength):.4f}.")
        if cohesion is not None:
            parts.append(f"Cohesion score: {float(cohesion):.4f}.")
        return " ".join(parts)

    def describe_validation_summary(self, validation_rows: list[dict]) -> str:
        if not validation_rows:
            return "No validation rows available."
        n_rows = len(validation_rows)
        themes = {r.get("theme_name", "") for r in validation_rows}
        windows = {r.get("forward_window", "") for r in validation_rows}
        return (
            f"Validation covers {n_rows} row(s) across "
            f"{len(themes)} theme(s) and "
            f"{len(windows)} forward window(s)."
        )


# Module-level default narrator (deterministic; swap out at runtime for LLM narrator)
_DEFAULT_NARRATOR: NarratorInterface = DeterministicNarrator()


# ---------------------------------------------------------------------------
# Artifact readers
# ---------------------------------------------------------------------------


def _read_communities(run_dir: Path) -> dict:
    p = run_dir / "discovery" / "communities.json"
    if not p.exists():
        return {}
    return run_cache.load_json(p)


def _read_theme_snapshots(run_dir: Path) -> dict:
    p = run_dir / "discovery" / "theme_snapshots.json"
    if not p.exists():
        return {}
    return run_cache.load_json(p)


def _read_theme_metrics(run_dir: Path) -> list[dict]:
    p = run_dir / "discovery" / "theme_metrics.parquet"
    if not p.exists():
        return []
    try:
        return run_cache.load_parquet_rows(p)
    except Exception as exc:
        _log.warning("report: failed to read theme_metrics.parquet at %s: %s", p, exc)
        return []


def _read_exposure(run_dir: Path) -> list[dict]:
    p = run_dir / "discovery" / "company_theme_exposure.parquet"
    if not p.exists():
        return []
    try:
        return run_cache.load_parquet_rows(p)
    except Exception as exc:
        _log.warning("report: failed to read company_theme_exposure.parquet at %s: %s", p, exc)
        return []


def _read_validation_csv(run_dir: Path) -> list[dict]:
    p = run_dir / "validation" / "validation.csv"
    if not p.exists():
        return []
    try:
        with open(p, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception as exc:
        _log.warning("report: failed to read validation.csv at %s: %s", p, exc)
        return []


# ---------------------------------------------------------------------------
# Report assembly helpers
# ---------------------------------------------------------------------------


def _format_optional_float(val: Any, decimals: int = 4) -> str:
    if val is None:
        return "N/A (temporal metric — single-snapshot MVP)"
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return "N/A"


def _format_return(val_str: str) -> str:
    """Format a return string from validation.csv (already formatted or empty)."""
    if not val_str:
        return "N/A"
    try:
        f = float(val_str)
        return f"{f:.4%}"
    except (ValueError, TypeError):
        return val_str


def _top_exposure_rows_for_community(
    community_id: str,
    exposure_rows: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """Return top-N exposure rows for a community, sorted by exposure_score DESC."""
    community_rows = [
        r for r in exposure_rows
        if r.get("community_id") == community_id
    ]
    return sorted(
        community_rows,
        key=lambda r: (-float(r.get("exposure_score") or 0.0), str(r.get("company_id") or "")),
    )[:top_n]


def _build_metrics_lookup(metrics_rows: list[dict]) -> dict[str, dict]:
    """community_id -> metrics row."""
    return {r.get("community_id", ""): r for r in metrics_rows if r.get("community_id")}


def _build_snapshot_lookup(snapshots_doc: dict) -> dict[str, dict]:
    """community_id -> snapshot dict."""
    result: dict[str, dict] = {}
    for snap in snapshots_doc.get("snapshots", []):
        cid = snap.get("community_id", "")
        if cid:
            result[cid] = snap
    return result


# ---------------------------------------------------------------------------
# Main report assembly
# ---------------------------------------------------------------------------


def generate_report(
    run_id: str,
    narrator: Optional[NarratorInterface] = None,
) -> Path:
    """Assemble report.md from existing run artifacts.

    Args:
        run_id: The run to generate a report for.
        narrator: Optional NarratorInterface. Defaults to DeterministicNarrator.
            Must not make network calls in tests.

    Returns:
        Path to the written report.md.

    Raises:
        HTTPException(404): run not found or required artifacts missing.
    """
    if narrator is None:
        narrator = _DEFAULT_NARRATOR

    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    run_dir = runs.get_run_dir(run_id)

    # --- Load artifacts ---
    communities_doc = _read_communities(run_dir)
    snapshots_doc = _read_theme_snapshots(run_dir)
    metrics_rows = _read_theme_metrics(run_dir)
    exposure_rows = _read_exposure(run_dir)
    validation_rows = _read_validation_csv(run_dir)

    communities = communities_doc.get("communities", [])
    snapshot_lookup = _build_snapshot_lookup(snapshots_doc)
    metrics_lookup = _build_metrics_lookup(metrics_rows)

    # --- Assemble report sections ---
    sections: list[str] = []

    # Title
    sections.append("# Theme Discovery Report\n")
    sections.append(
        f"_Generated from run artifacts. Run ID: `{run_id}`. "
        f"Artifact source: `data/runs/{run_id}/`._\n"
    )

    # --- §1: Run Metadata ---
    sections.append("## Run Metadata\n")
    sections.append(f"| Field | Value |")
    sections.append(f"|---|---|")
    sections.append(f"| Run ID | `{manifest.run_id}` |")
    sections.append(f"| As-of Date | `{manifest.as_of_date}` |")
    sections.append(f"| Created At | `{manifest.created_at}` |")
    sections.append(f"| Code Version | `{manifest.code_version}` |")
    sections.append(f"| Universe Config | `{manifest.universe_config}` |")
    sections.append(f"| Pipeline Config | `{manifest.pipeline_config}` |")
    sections.append(f"| Validation Config | `{manifest.validation_config}` |")
    frozen_str = "Yes" if manifest.discovery_frozen else "No"
    frozen_at_str = manifest.frozen_at if manifest.frozen_at else "Not frozen"
    sections.append(f"| Discovery Frozen | {frozen_str} |")
    sections.append(f"| Frozen At | `{frozen_at_str}` |")
    sections.append("")

    if not manifest.discovery_frozen:
        sections.append(
            "> **Warning**: Discovery artifacts have not been frozen for this run. "
            "Validation results (if any) may not be reliable. "
            "Run `POST /api/discovery/freeze` before running validation.\n"
        )

    # --- §2: Data Coverage ---
    sections.append("## Data Coverage\n")
    n_communities = len(communities)
    n_snapshots = len(snapshots_doc.get("snapshots", []))
    # Universe size: distinct company_ids in exposure
    company_ids_in_exposure = sorted(
        {str(r.get("company_id") or "") for r in exposure_rows if r.get("company_id")}
    )
    n_companies = len(company_ids_in_exposure)
    n_exposure_pairs = len(exposure_rows)

    sections.append(f"| Metric | Count |")
    sections.append(f"|---|---|")
    sections.append(f"| Communities discovered | {n_communities} |")
    sections.append(f"| Theme snapshots | {n_snapshots} |")
    sections.append(f"| Companies in exposure | {n_companies} |")
    sections.append(f"| Company-theme pairs | {n_exposure_pairs} |")
    sections.append(
        f"| Validation rows | "
        f"{'0 (not available)' if not validation_rows else len(validation_rows)} |"
    )
    sections.append("")
    sections.append(
        f"_Source artifacts: `discovery/communities.json` ({n_communities} communities), "
        f"`discovery/theme_snapshots.json` ({n_snapshots} snapshots), "
        f"`discovery/company_theme_exposure.parquet` ({n_exposure_pairs} rows)._\n"
    )

    # --- §3: Emerging Themes ---
    sections.append("## Emerging Themes\n")
    if not communities:
        sections.append("_No communities discovered for this run._\n")
    else:
        emerging = [
            c for c in communities
            if snapshot_lookup.get(c.get("community_id", ""), {}).get("state") == "Emerging"
        ]
        if not emerging:
            emerging = communities  # fall back to all if no Emerging-labelled ones
        for community in emerging:
            cid = community.get("community_id", "")
            snap = snapshot_lookup.get(cid, {})
            metrics = metrics_lookup.get(cid, {})
            theme_name = community.get("theme_name", cid)
            theme_summary = community.get("theme_summary", "")

            prose = narrator.describe_theme(theme_name, theme_summary, metrics)
            sections.append(f"### {theme_name}\n")
            sections.append(prose + "\n")
            sections.append(
                f"- **Community ID**: `{cid}` "
                f"_(source: `discovery/communities.json`)_"
            )
            snap_id = snap.get("theme_snapshot_id", "")
            if snap_id:
                sections.append(
                    f"- **Snapshot ID**: `{snap_id}` "
                    f"_(source: `discovery/theme_snapshots.json`)_"
                )
            state = snap.get("state", "Unknown")
            sections.append(f"- **State**: {state}")
            sections.append(f"- **Community size**: {community.get('size', 'N/A')} entities")
            sections.append(f"- **Density**: {_format_optional_float(community.get('density'), 4)}")

            # Metrics from theme_metrics.parquet
            sections.append(
                f"- **Strength** _(from `discovery/theme_metrics.parquet`)_: "
                f"{_format_optional_float(metrics.get('strength'), 4)}"
            )
            sections.append(
                f"- **Cohesion** _(from `discovery/theme_metrics.parquet`)_: "
                f"{_format_optional_float(metrics.get('cohesion'), 4)}"
            )
            sections.append(
                f"- **Saturation** _(from `discovery/theme_metrics.parquet`)_: "
                f"{_format_optional_float(metrics.get('saturation'), 4)}"
            )
            # Temporal metrics — always null in single-snapshot
            for tfield in ("momentum", "birth_score", "novelty"):
                sections.append(
                    f"- **{tfield.replace('_', ' ').title()}** _(temporal — single-snapshot MVP)_: "
                    f"N/A (not available in single-snapshot run)"
                )

            # Top entities and companies
            top_entities = community.get("top_entities", [])
            top_companies_list = community.get("top_companies", [])
            if top_entities:
                sections.append(
                    f"- **Top Entities**: {', '.join(top_entities)} "
                    f"_(source: `discovery/communities.json`)_"
                )
            if top_companies_list:
                sections.append(
                    f"- **Top Companies**: {', '.join(top_companies_list)} "
                    f"_(source: `discovery/communities.json`)_"
                )
            sections.append("")

    # --- §4: Accelerating Themes ---
    sections.append("## Accelerating Themes\n")
    sections.append(
        "_Acceleration metrics (Momentum, Birth Score, Novelty) require temporal "
        "lineage from multiple snapshots. These are not available in a single-snapshot MVP run. "
        "See `discovery/theme_lineage.json` (lineage_mode=single_snapshot) for details._\n"
    )

    # --- §5: Company Exposure ---
    sections.append("## Company Exposure\n")
    if not exposure_rows:
        sections.append(
            "_No company-theme exposure data available. "
            "Run `POST /api/exposure/compute` first._\n"
        )
    else:
        sections.append(
            "_Exposure scores are computed from `discovery/company_theme_exposure.parquet` "
            "(io_contracts §18). Calculation method: `exposure_v1_document_stated` by default "
            "(OI-2 policy: only `document_stated` edges contribute)._\n"
        )

        # Show top exposure highlights per community
        for community in communities:
            cid = community.get("community_id", "")
            snap = snapshot_lookup.get(cid, {})
            theme_name = community.get("theme_name", cid)
            top_rows = _top_exposure_rows_for_community(cid, exposure_rows, top_n=5)
            if not top_rows:
                continue

            sections.append(f"### {theme_name} — Top Exposed Companies\n")
            sections.append(
                f"_Community: `{cid}` | "
                f"Snapshot: `{snap.get('theme_snapshot_id', 'N/A')}` | "
                f"Source: `discovery/company_theme_exposure.parquet`_\n"
            )
            sections.append(
                "| Rank | Company ID | Ticker | Exposure Score | "
                "Graph Distance | Evidence Count | Top Evidence Chunks |"
            )
            sections.append("|---|---|---|---|---|---|---|")
            for rank, row in enumerate(top_rows, start=1):
                comp_id = row.get("company_id", "")
                ticker = row.get("ticker") or "N/A"
                score = row.get("exposure_score")
                dist = row.get("graph_distance")
                ev_count = row.get("evidence_count", 0)
                top_chunks = row.get("top_evidence_chunk_ids") or []
                chunk_display = (
                    ", ".join(f"`{c}`" for c in top_chunks[:3])
                    if top_chunks else "none"
                )
                sections.append(
                    f"| {rank} | `{comp_id}` | {ticker} | "
                    f"{_format_optional_float(score, 6)} | "
                    f"{_format_optional_float(dist, 2)} | "
                    f"{ev_count} | {chunk_display} |"
                )
            sections.append("")

    # --- §6: Validation Results ---
    sections.append("## Validation Results\n")
    if not validation_rows:
        sections.append(_VALIDATION_ABSENT_NOTE + "\n")
    else:
        # Check if this is single-snapshot illustrative
        caveats_in_rows = " ".join(r.get("caveats", "") for r in validation_rows)
        is_illustrative = "ILLUSTRATIVE" in caveats_in_rows.upper()
        backtest_status_values = {r.get("schema_version", "") for r in validation_rows}

        sections.append(
            "_Validation results from `validation/validation.csv` (io_contracts §22). "
            "Results are traceable to baskets in `validation/portfolio_baskets.parquet`._\n"
        )

        # Validation summary prose
        prose = narrator.describe_validation_summary(validation_rows)
        sections.append(prose + "\n")

        # Add illustrative caveat if applicable
        if is_illustrative:
            sections.append(f"> {_ILLUSTRATIVE_CAVEAT}\n")

        # Table of validation results
        sections.append(
            "| Theme | Basket ID | Forward Window | Basket Return | "
            "Benchmark | Benchmark Return | Excess Return | Sample Size |"
        )
        sections.append("|---|---|---|---|---|---|---|---|")
        for row in validation_rows:
            theme_name_v = row.get("theme_name", row.get("community_id", ""))
            basket_id_v = row.get("basket_id", "")
            window = row.get("forward_window", "")
            basket_ret = _format_return(row.get("theme_basket_return", ""))
            bm_name = row.get("benchmark_name", "")
            bm_ret = _format_return(row.get("benchmark_return", ""))
            excess = _format_return(row.get("excess_return", ""))
            sample = row.get("sample_size", "N/A")
            start_date_v = row.get("start_date", "")
            end_date_v = row.get("end_date", "")
            sections.append(
                f"| {theme_name_v} | `{basket_id_v}` | {window} | {basket_ret} | "
                f"{bm_name} | {bm_ret} | {excess} | {sample} |"
            )
        sections.append("")

        # Always append single-snapshot caveat footer
        sections.append(
            f"> **Caveat (spec §2)**: {_ILLUSTRATIVE_CAVEAT}\n"
        )

    # --- §7: Evidence Notes ---
    sections.append("## Evidence Notes\n")
    sections.append(
        "_Evidence traceability: every exposure score and community claim is backed "
        "by specific evidence chunk IDs from `discovery/edges.parquet` and "
        "`discovery/edge_explanations.parquet`._\n"
    )

    # List a few evidence chunk ids per community for traceability
    if exposure_rows:
        all_chunk_ids: set[str] = set()
        for row in exposure_rows:
            for cid in (row.get("top_evidence_chunk_ids") or []):
                all_chunk_ids.add(str(cid))
        if all_chunk_ids:
            sample_chunks = sorted(all_chunk_ids)[:10]
            sections.append(
                "Sample evidence chunk IDs referenced by exposure rows "
                "(full list in `discovery/company_theme_exposure.parquet`, "
                "column `top_evidence_chunk_ids`):\n"
            )
            for chk in sample_chunks:
                sections.append(f"- `{chk}`")
            sections.append("")

    sections.append(
        "_For full edge provenance, see `discovery/edge_explanations.parquet` "
        "(io_contracts §12)._\n"
    )

    # --- §8: Caveats ---
    sections.append("## Caveats\n")
    sections.append(f"1. {_ILLUSTRATIVE_CAVEAT}\n")
    sections.append(
        "2. **Temporal metrics unavailable**: Momentum, Birth Score, Novelty, and "
        "Acceleration require multiple as_of_date snapshots (walk-forward panel). "
        "These fields are `null` in `discovery/theme_metrics.parquet` for this run.\n"
    )
    sections.append(
        "3. **Community naming**: Theme names are deterministic placeholders derived "
        "from top entity labels (naming_model=`deterministic`). They are metadata only "
        "and do not constitute research conclusions.\n"
    )
    sections.append(
        "4. **Report determinism**: This report is assembled deterministically from "
        "artifacts. Running `POST /api/report/generate` on the same run produces "
        "identical output.\n"
    )
    sections.append(
        "5. **No investment advice**: This report is for research purposes only. "
        "It does not constitute investment advice and must not be used as the sole "
        "basis for any investment decision (spec §9.9).\n"
    )

    # --- Footer ---
    sections.append("---\n")
    sections.append(
        f"_Report generated from run `{run_id}` artifacts. "
        f"Source artifacts: `data/runs/{run_id}/`. "
        f"io_contracts §23._\n"
    )

    # --- Write report.md ---
    report_text = "\n".join(sections)
    report_path = run_dir / "report.md"
    report_path.write_text(report_text, encoding="utf-8")

    return report_path
