"""Company detail page data service (EG-C + EG-D, GitHub #93 / #94).

Exposes read-only helpers that power the per-company page:

  GET /api/themes/{run}/companies/{id}
      Company profile + fundamentals (B1, as-reported) + financial facts (B2)
      + per-theme exposure list.  All values are PIT-clean (available_at <= as_of).
      The ``fundamentals`` key ALWAYS carries an ``available`` flag so the UI
      can render an explicit "no fundamentals at as_of" state and never silently
      blank.

  GET /api/themes/{run}/companies/{id}/evidence
      Evidence grouped BY theme (from E3's (company_id, theme_snapshot_id,
      community_id) grain).  Each chunk carries its extracted FinancialMetric
      fact when B2 has one (EG-D), otherwise falls back to a sentence-level
      snippet (no regression).  Evidence under different themes is STRICTLY
      isolated — no cross-theme bleed.

Design constraints:
  - Read-only: never mutates any artifact.
  - PIT discipline: every read uses the run's as_of_date.
  - Fallback-safe: missing artifacts return explicit empty/null states.
  - company_id is the ENTITY id (ent_...) — NOT document.company_id.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from . import run_cache, runs

# ---------------------------------------------------------------------------
# Internal read helpers
# ---------------------------------------------------------------------------


def _get_discovery_dir(run_id: str, as_of: str | None = None) -> Path:
    return runs.discovery_point_dir(run_id, as_of)


def _load_parquet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return run_cache.load_parquet_rows(path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return run_cache.load_json(path)


def _to_date_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    return s[:10] if len(s) >= 10 else s


def _require_run(run_id: str, as_of: str | None = None) -> str:
    """Return the effective PIT date (the selected point or the run's as_of_date)
    or raise 404 when the run is unknown."""
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return as_of if as_of is not None else str(manifest.as_of_date)


# ---------------------------------------------------------------------------
# EG-C: company summary endpoint
# ---------------------------------------------------------------------------


def get_company_profile(run_id: str, company_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Build the full company detail payload.

    Returns
    -------
    {
      company_id, name, ticker, entity_type, as_of_date,
      themes: [{community_id, theme_name, theme_snapshot_id, exposure_score}],
      fundamentals: {available: bool, as_of_date, rows: [...]},
      financial_facts: [{metric_name, value, unit, period, direction,
                          is_guidance, confidence, evidence_chunk_id, source}]
    }

    Never returns None — raises HTTPException 404 if the entity is unknown.
    """
    as_of = _require_run(run_id, as_of)
    ddir = _get_discovery_dir(run_id, as_of)

    # ── Entity lookup ──────────────────────────────────────────────────────
    entities = _load_parquet(ddir / "entities.parquet")
    entity = next(
        (e for e in entities if e.get("entity_id") == company_id),
        None,
    )
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=f"entity not found: {company_id!r} in run {run_id!r}",
        )

    name = entity.get("canonical_name") or entity.get("name") or company_id
    ticker = entity.get("ticker") or None

    # ── Per-theme exposure list ────────────────────────────────────────────
    exposure_rows = _load_parquet(ddir / "company_theme_exposure.parquet")
    company_exposures = [
        r for r in exposure_rows if r.get("company_id") == company_id
    ]

    # Join with theme_snapshots for display name
    snapshots_doc = _load_json(ddir / "theme_snapshots.json")
    snap_by_id: dict[str, dict] = {
        s.get("theme_snapshot_id", ""): s
        for s in snapshots_doc.get("snapshots", [])
    }
    communities_doc = _load_json(ddir / "communities.json")
    comm_by_id: dict[str, dict] = {
        c.get("community_id", ""): c
        for c in communities_doc.get("communities", [])
    }

    themes: list[dict] = []
    for exp in sorted(company_exposures, key=lambda r: -float(r.get("exposure_score") or 0)):
        cid = exp.get("community_id", "")
        snap = snap_by_id.get(exp.get("theme_snapshot_id", ""), {})
        comm = comm_by_id.get(cid, {})
        themes.append({
            "community_id": cid,
            "theme_name": comm.get("theme_name") or snap.get("name") or cid,
            "theme_snapshot_id": exp.get("theme_snapshot_id", ""),
            "exposure_score": float(exp.get("exposure_score") or 0),
        })

    # ── B1: as-reported XBRL fundamentals (PIT-clean) ────────────────────
    fundamentals_path = ddir / "fundamentals_asreported.parquet"
    fundamentals_available = fundamentals_path.exists()
    fund_rows_all: list[dict] = _load_parquet(fundamentals_path)
    # Filter to this company + PIT gate
    # PIT gate (fail-closed, OI-8): a row with a missing/empty available_at cannot
    # be proven knowable at as_of, so it is EXCLUDED (never treated as available).
    fund_rows = [
        r for r in fund_rows_all
        if r.get("company_id") == company_id
        and _to_date_str(r.get("available_at"))
        and _to_date_str(r.get("available_at")) <= as_of
    ]
    fund_rows_clean: list[dict] = [
        {
            "period_end": r.get("period_end"),
            "metric_name": r.get("metric_name"),
            "metric_value": r.get("metric_value"),
            "unit": r.get("unit"),
            "currency": r.get("currency"),
            "filing_date": r.get("filing_date"),
            "available_at": r.get("available_at"),
            "source": r.get("source"),
        }
        for r in fund_rows
    ]

    fundamentals: dict[str, Any] = {
        "available": fundamentals_available and bool(fund_rows),
        "as_of_date": as_of,
        "rows": fund_rows_clean,
    }
    # If the artifact exists but no rows pass PIT, be explicit.
    if fundamentals_available and not fund_rows:
        fundamentals["message"] = (
            f"No as-reported fundamentals available at as_of={as_of} for {company_id}."
        )

    # ── B2: LLM-extracted FinancialMetric facts ───────────────────────────
    metrics_path = ddir / "financial_metrics.parquet"
    all_fm_rows = _load_parquet(metrics_path)

    # PIT-clean via evidence_chunk_id -> chunk available_at
    chunks = _load_parquet(ddir / "chunks.parquet")
    chunk_available: dict[str, str] = {
        c["chunk_id"]: _to_date_str(c.get("available_at"))
        for c in chunks
        if c.get("chunk_id")
    }

    financial_facts: list[dict] = []
    for fm in all_fm_rows:
        if fm.get("company_id") != company_id:
            continue
        ev_cid = fm.get("evidence_chunk_id") or ""
        avail = chunk_available.get(ev_cid, "")
        if avail and avail > as_of:
            continue
        financial_facts.append({
            "metric_id": fm.get("metric_id"),
            "metric_name": fm.get("metric_name"),
            "value": fm.get("value"),
            "unit": fm.get("unit"),
            "period": fm.get("period"),
            "direction": fm.get("direction"),
            "is_guidance": fm.get("is_guidance"),
            "confidence": fm.get("confidence"),
            "evidence_chunk_id": ev_cid,
            "source": fm.get("source"),
        })

    return {
        "company_id": company_id,
        "name": name,
        "ticker": ticker,
        "entity_type": entity.get("entity_type", "Company"),
        "as_of_date": as_of,
        "themes": themes,
        "fundamentals": fundamentals,
        "financial_facts": financial_facts,
    }


# ---------------------------------------------------------------------------
# EG-C: evidence-by-theme endpoint (EG-D: attach extracted facts)
# ---------------------------------------------------------------------------


def _format_fact_label(fm: dict) -> str:
    """Format a FinancialMetric row into a concise quantified claim string.

    Example: 'revenue Q1 2024: $1.5B CAD (actual, rose)'
    """
    parts: list[str] = []
    metric = fm.get("metric_name") or ""
    period = fm.get("period") or ""
    val = fm.get("value")
    unit = fm.get("unit") or ""
    direction = fm.get("direction") or ""
    is_guidance = fm.get("is_guidance") or False

    if metric:
        parts.append(metric)
    if period:
        parts.append(period)
    if val is not None:
        # format large numbers for readability
        try:
            fval = float(val)
            val_str = f"{fval:,.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            val_str = str(val)
        if unit:
            parts.append(f"{val_str} {unit}")
        else:
            parts.append(val_str)
    label = ": ".join(parts[:2]) + (f": {parts[2]}" if len(parts) > 2 else "")
    qualifiers: list[str] = []
    if direction:
        qualifiers.append(direction)
    qualifiers.append("guidance" if is_guidance else "actual")
    if qualifiers:
        label += f" ({', '.join(qualifiers)})"
    return label


def get_company_evidence_by_theme(run_id: str, company_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
    """Return evidence for a company, grouped strictly by theme (E3).

    Each theme group carries only the chunk_ids attributed to THAT
    specific (company, theme) pair — no cross-theme bleed.

    For each chunk, attaches the extracted FinancialMetric fact (EG-D)
    when one exists (evidence_chunk_id join on B2). Falls back to the
    chunk's raw text snippet when no fact was extracted.

    PIT-clean: only chunks with available_at <= run.as_of are included.
    """
    as_of = _require_run(run_id, as_of)
    ddir = _get_discovery_dir(run_id, as_of)

    # ── E3: company-theme provenance ──────────────────────────────────────
    e3_path = ddir / "company_theme_document_evidence.parquet"
    if not e3_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"company_theme_document_evidence.parquet not found for run {run_id!r}; "
                "POST /api/provenance/materialize first."
            ),
        )

    e3_rows = _load_parquet(e3_path)
    company_e3 = [r for r in e3_rows if r.get("company_id") == company_id]

    # ── Chunks lookup ─────────────────────────────────────────────────────
    chunks = _load_parquet(ddir / "chunks.parquet")
    chunk_by_id: dict[str, dict] = {c["chunk_id"]: c for c in chunks if c.get("chunk_id")}

    # ── B2: financial metrics, indexed by evidence_chunk_id ───────────────
    metrics_path = ddir / "financial_metrics.parquet"
    all_fm = _load_parquet(metrics_path)
    # Build index: chunk_id -> list of FinancialMetric rows
    fm_by_chunk: dict[str, list[dict]] = {}
    for fm in all_fm:
        ev = fm.get("evidence_chunk_id") or ""
        if ev:
            fm_by_chunk.setdefault(ev, []).append(fm)

    # ── Theme display names ───────────────────────────────────────────────
    snapshots_doc = _load_json(ddir / "theme_snapshots.json")
    snap_by_id: dict[str, dict] = {
        s.get("theme_snapshot_id", ""): s
        for s in snapshots_doc.get("snapshots", [])
    }
    communities_doc = _load_json(ddir / "communities.json")
    comm_by_id: dict[str, dict] = {
        c.get("community_id", ""): c
        for c in communities_doc.get("communities", [])
    }

    # ── Documents for source attribution ──────────────────────────────────
    docs = _load_parquet(ddir / "documents.parquet")
    doc_by_id: dict[str, dict] = {
        d["document_id"]: d for d in docs if d.get("document_id")
    }

    result: list[dict[str, Any]] = []

    for e3 in company_e3:
        community_id = e3.get("community_id", "")
        theme_snapshot_id = e3.get("theme_snapshot_id", "")
        chunk_ids: list[str] = list(e3.get("chunk_ids") or [])

        snap = snap_by_id.get(theme_snapshot_id, {})
        comm = comm_by_id.get(community_id, {})
        theme_name = comm.get("theme_name") or snap.get("name") or community_id

        chunks_out: list[dict[str, Any]] = []
        for cid in chunk_ids:
            ch = chunk_by_id.get(cid)
            if ch is None:
                continue
            # PIT gate
            avail = _to_date_str(ch.get("available_at"))
            if avail and avail > as_of:
                continue

            text_snippet = ch.get("text") or ""
            doc_id = ch.get("document_id") or ""
            doc = doc_by_id.get(doc_id, {})

            # EG-D: attach extracted FinancialMetric fact if available
            financial_fact: Optional[dict] = None
            fact_label: Optional[str] = None
            fm_list = fm_by_chunk.get(cid, [])
            if fm_list:
                # Pick the highest-confidence fact for this chunk
                best_fm = max(fm_list, key=lambda f: float(f.get("confidence") or 0))
                financial_fact = {
                    "metric_id": best_fm.get("metric_id"),
                    "metric_name": best_fm.get("metric_name"),
                    "value": best_fm.get("value"),
                    "unit": best_fm.get("unit"),
                    "period": best_fm.get("period"),
                    "direction": best_fm.get("direction"),
                    "is_guidance": best_fm.get("is_guidance"),
                    "confidence": best_fm.get("confidence"),
                }
                fact_label = _format_fact_label(best_fm)

            chunks_out.append({
                "chunk_id": cid,
                "text": text_snippet,
                "document_id": doc_id,
                "available_at": avail,
                "section_title": ch.get("section_title"),
                "block_type": ch.get("block_type"),
                # EG-D: quantified fact if extracted; None = sentence-level fallback
                "financial_fact": financial_fact,
                "fact_label": fact_label,
                # Source attribution for "read full source" link
                "document": {
                    "title": doc.get("title"),
                    "source": doc.get("source"),
                    "document_type": doc.get("document_type"),
                    "published_at": doc.get("published_at"),
                },
            })

        result.append({
            "community_id": community_id,
            "theme_name": theme_name,
            "theme_snapshot_id": theme_snapshot_id,
            "chunk_count": len(chunks_out),
            "chunks": chunks_out,
        })

    return result
