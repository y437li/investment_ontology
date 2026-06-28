"""SENT-D: Management-sentiment panel data for the company detail page (GitHub #102).

Reads management_sentiment_fused.parquet (SENT-C artifact) and resolves each
evidence_chunk_id to its source chunk (text + document metadata), then returns
a structured payload for the company-page sentiment panel.

PIT discipline: only rows with available_at <= run.as_of_date are emitted.
(The fused artifact already enforces this at write time, but we re-gate here
as a defence-in-depth guard.)

Discovery-evidence only: reads management_sentiment_fused.parquet which is
produced from discovery chunks — never from exposure or propagation artifacts.

Endpoint:
    GET /api/themes/{run_id}/companies/{company_id}/sentiment
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from . import run_cache, runs


# ---------------------------------------------------------------------------
# Shared helpers (mirrors company_profile.py style)
# ---------------------------------------------------------------------------

def _get_discovery_dir(run_id: str) -> Path:
    return runs.get_run_dir(run_id) / "discovery"


def _load_parquet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return run_cache.load_parquet_rows(path)


def _to_date_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    return s[:10] if len(s) >= 10 else s


def _require_run(run_id: str) -> str:
    """Return run's as_of_date or raise 404."""
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return str(manifest.as_of_date)


# ---------------------------------------------------------------------------
# Fused-tone visual metadata helpers
# ---------------------------------------------------------------------------

# Agreement / fused_tone → display label and severity class used by the UI.
# The UI must render hedged/conflict states VISUALLY DISTINCT from positive.
_TONE_META: dict[str, dict[str, str]] = {
    "positive":  {"label": "Positive",  "severity": "positive"},
    "negative":  {"label": "Negative",  "severity": "negative"},
    "neutral":   {"label": "Neutral",   "severity": "neutral"},
    "hedged":    {"label": "Hedged",    "severity": "hedged"},
}

_AGREEMENT_META: dict[str, dict[str, str]] = {
    "agree":    {"label": "LM + LLM agree",   "severity": "agree"},
    "hedged":   {"label": "Hedged language",   "severity": "hedged"},
    "conflict": {"label": "Signal conflict",   "severity": "conflict"},
}


def _tone_meta(fused_tone: str) -> dict[str, str]:
    return _TONE_META.get(fused_tone, {"label": fused_tone or "—", "severity": "neutral"})


def _agreement_meta(agreement: str) -> dict[str, str]:
    return _AGREEMENT_META.get(agreement, {"label": agreement or "—", "severity": "neutral"})


# ---------------------------------------------------------------------------
# Lexicon hits parsing
# ---------------------------------------------------------------------------

def _parse_lexicon_hits(raw: Any) -> dict:
    """Parse the lexicon_hits JSON string into a dict.

    The column stores a JSON-serialised dict like
    {"positive": ["strong", "grew"], "negative": []}.  Returns {} on any parse
    error so the UI can still render without crashing.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Core endpoint function
# ---------------------------------------------------------------------------


def get_company_sentiment(run_id: str, company_id: str) -> dict[str, Any]:
    """Return the management-sentiment panel payload for a company page.

    Schema
    ------
    {
      "company_id": str,
      "as_of_date": str,
      "available": bool,                   # False when artifact absent / no rows
      "message": str | None,               # set when available=False
      "fused_tone_summary": {              # overall fused tone across all readings
        "dominant_tone": str,              # most common fused_tone
        "tone_counts": {positive:n, ...},  # tallied counts
        "has_conflict": bool,
        "has_hedged": bool,
      },
      "readings": [                        # one entry per evidence_chunk_id
        {
          "fusion_id": str,
          "fused_tone": str,               # positive|negative|neutral|hedged
          "fused_tone_label": str,         # display label
          "fused_tone_severity": str,      # positive|negative|neutral|hedged  (UI class)
          "agreement": str,                # agree|hedged|conflict
          "agreement_label": str,
          "agreement_severity": str,       # agree|hedged|conflict  (UI class)
          "fused_confidence": float,
          "direction": str,                # LLM direction
          "confidence_tone": str,
          "hedging": bool,
          "forward_stance": str,
          "lm_direction": str,
          "tone_positive": float,
          "tone_negative": float,
          "tone_uncertainty": float,
          "lexicon_hits": dict,            # parsed {category: [matched_words]}
          "available_at": str,             # chunk available_at (PIT)
          "evidence_chunk_id": str,
          "chunk_text": str | None,        # first 400 chars of the source chunk
          "section_title": str | None,
          "document": {                    # source attribution (for "read full source")
            "title": str | None,
            "source": str | None,
            "document_type": str | None,
            "published_at": str | None,
          },
        },
        ...
      ]
    }

    PIT: only rows with available_at <= as_of_date are included.
    Sorted: conflict first, then hedged, then by available_at descending.
    """
    as_of = _require_run(run_id)
    ddir = _get_discovery_dir(run_id)

    # ── Load fused artifact ───────────────────────────────────────────────────
    fused_path = ddir / "management_sentiment_fused.parquet"
    if not fused_path.exists():
        return {
            "company_id": company_id,
            "as_of_date": as_of,
            "available": False,
            "message": (
                "management_sentiment_fused.parquet not found for this run. "
                "Run POST /api/sentiment/fuse first."
            ),
            "fused_tone_summary": None,
            "readings": [],
        }

    all_rows = _load_parquet(fused_path)

    # Filter to this company + PIT gate (defence-in-depth re-gate)
    company_rows = [
        r for r in all_rows
        if r.get("company_id") == company_id
        and (
            not _to_date_str(r.get("available_at"))
            or _to_date_str(r.get("available_at")) <= as_of
        )
    ]

    if not company_rows:
        return {
            "company_id": company_id,
            "as_of_date": as_of,
            "available": False,
            "message": (
                f"No management-sentiment readings available for {company_id} "
                f"at as_of={as_of}."
            ),
            "fused_tone_summary": None,
            "readings": [],
        }

    # ── Chunk and document lookup ─────────────────────────────────────────────
    chunks = _load_parquet(ddir / "chunks.parquet")
    chunk_by_id: dict[str, dict] = {
        c["chunk_id"]: c for c in chunks if c.get("chunk_id")
    }

    docs = _load_parquet(ddir / "documents.parquet")
    doc_by_id: dict[str, dict] = {
        d["document_id"]: d for d in docs if d.get("document_id")
    }

    # ── Build reading objects ─────────────────────────────────────────────────
    _SEVERITY_ORDER = {"conflict": 0, "hedged": 1, "agree": 2}

    readings: list[dict[str, Any]] = []
    tone_counts: dict[str, int] = {}

    for row in company_rows:
        fused_tone = str(row.get("fused_tone") or "neutral")
        agreement = str(row.get("agreement") or "agree")
        evidence_chunk_id = str(row.get("evidence_chunk_id") or "")

        # Tone summary accumulation
        tone_counts[fused_tone] = tone_counts.get(fused_tone, 0) + 1

        # Resolve chunk
        ch = chunk_by_id.get(evidence_chunk_id, {})
        chunk_text: Optional[str] = ch.get("text") or None
        if chunk_text and len(chunk_text) > 400:
            chunk_text = chunk_text[:397] + "…"

        doc_id = ch.get("document_id") or ""
        doc = doc_by_id.get(doc_id, {})

        readings.append({
            "fusion_id": str(row.get("fusion_id") or ""),
            "fused_tone": fused_tone,
            "fused_tone_label": _tone_meta(fused_tone)["label"],
            "fused_tone_severity": _tone_meta(fused_tone)["severity"],
            "agreement": agreement,
            "agreement_label": _agreement_meta(agreement)["label"],
            "agreement_severity": _agreement_meta(agreement)["severity"],
            "fused_confidence": float(row.get("fused_confidence") or 0.0),
            "direction": str(row.get("direction") or ""),
            "confidence_tone": str(row.get("confidence_tone") or ""),
            "hedging": bool(row.get("hedging") or False),
            "forward_stance": str(row.get("forward_stance") or ""),
            "lm_direction": str(row.get("lm_direction") or ""),
            "tone_positive": float(row.get("tone_positive") or 0.0),
            "tone_negative": float(row.get("tone_negative") or 0.0),
            "tone_uncertainty": float(row.get("tone_uncertainty") or 0.0),
            "lexicon_hits": _parse_lexicon_hits(row.get("lexicon_hits")),
            "available_at": _to_date_str(row.get("available_at")),
            "evidence_chunk_id": evidence_chunk_id,
            "chunk_text": chunk_text,
            "section_title": ch.get("section_title") or None,
            "document": {
                "title": doc.get("title") or None,
                "source": doc.get("source") or None,
                "document_type": doc.get("document_type") or None,
                "published_at": _to_date_str(doc.get("published_at")) or None,
            },
        })

    # Sort: conflict first, then hedged, then by available_at descending
    readings.sort(
        key=lambda r: (
            _SEVERITY_ORDER.get(r["agreement"], 9),
            # negate string comparison: newer dates first within each tier
            "".join(
                chr(0x10FFFF - ord(c)) if c.isdigit() else c
                for c in r["available_at"]
            ),
        )
    )

    # ── Fused-tone summary ────────────────────────────────────────────────────
    dominant_tone = max(tone_counts, key=lambda t: tone_counts[t]) if tone_counts else "neutral"
    has_conflict = any(r["agreement"] == "conflict" for r in readings)
    has_hedged = any(r["agreement"] == "hedged" for r in readings)

    return {
        "company_id": company_id,
        "as_of_date": as_of,
        "available": True,
        "message": None,
        "fused_tone_summary": {
            "dominant_tone": dominant_tone,
            "dominant_tone_label": _tone_meta(dominant_tone)["label"],
            "dominant_tone_severity": _tone_meta(dominant_tone)["severity"],
            "tone_counts": tone_counts,
            "has_conflict": has_conflict,
            "has_hedged": has_hedged,
            "reading_count": len(readings),
        },
        "readings": readings,
    }
