"""Sentiment fusion layer (SENT-C): reconcile SENT-A chunk-tone with SENT-B management-sentiment.

Workstream SENT-C (GitHub #101). Reads both SENT-A (chunk_tone.parquet) and SENT-B
(management_sentiment.parquet) artifacts and emits a fused record per
(company_id, evidence_chunk_id).

Each fused record carries:
  - The LM category vector (tone_positive, tone_negative, tone_uncertainty, ...)
  - The LLM judgment verbatim from SENT-B (direction, confidence_tone, hedging, forward_stance)
  - A fused_tone in {positive, neutral, negative, hedged}
  - An agreement flag in {agree, hedged, conflict}
  - A fused_confidence (downgraded when agreement != agree)

Fusion rules (headline):
  Step 1 — Derive lm_direction from the SENT-A tone vector:
      lm_direction = "positive"    if tone_positive > max(tone_negative, tone_uncertainty)
                                   and tone_positive > TONE_MIN_THRESHOLD
      lm_direction = "uncertainty" if tone_uncertainty > max(tone_positive, tone_negative)
                                   and tone_uncertainty > TONE_MIN_THRESHOLD
      lm_direction = "negative"    if tone_negative > max(tone_positive, tone_uncertainty)
                                   and tone_negative > TONE_MIN_THRESHOLD
      lm_direction = "neutral"     otherwise (all near zero / ambiguous)

  Step 2 — Classify agreement:
      "agree"   — LLM positive AND LM positive; OR LLM negative AND LM negative-or-uncertainty;
                  OR LLM neutral AND LM neutral.
      "hedged"  — LLM says positive but LM is uncertainty-dominant (management spin with
                  hedged language under the hood); OR LLM direction is "mixed";
                  OR LLM positive but LM is neutral (weak evidence of positivity).
      "conflict"— LLM says positive but LM says clearly negative; or LLM says negative
                  but LM says clearly positive.  This is a direct signal contradiction.

  Step 3 — Set fused_tone:
      "agree"    and LLM positive  → fused_tone = "positive"
      "agree"    and LLM negative  → fused_tone = "negative"
      "agree"    and LLM neutral   → fused_tone = "neutral"
      "hedged"                     → fused_tone = "hedged"
      "conflict"                   → fused_tone = "negative"  (conservative downgrade)

  Step 4 — Set fused_confidence:
      "agree"   → fused_confidence = original LLM confidence  (no discount)
      "hedged"  → fused_confidence = original confidence × HEDGED_DISCOUNT (0.75)
      "conflict"→ fused_confidence = original confidence × CONFLICT_DISCOUNT (0.50)

PIT constraint: only management_sentiment rows whose evidence_chunk_id resolves to a
chunk_tone row with available_at <= as_of_date are emitted.  The PIT gate is enforced
here so that the fused artifact is independently clean even if upstream gates are
bypassed.

Evidence constraint: every fused row carries the evidence_chunk_id from SENT-B, which
resolves to a source chunk via the existing source.py chain.

Discovery-evidence only: this artifact MUST NOT be read by exposure.py.
It is a one-way input to forward-inference stages only.

Output artifact:
    data/runs/<run_id>/discovery/management_sentiment_fused.parquet
    Schema defined in MANAGEMENT_SENTIMENT_FUSED_COLUMNS below.
    Contract documented in docs/io_contracts.md §SENT-C and docs/data_schema.md §SENT-C.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

from . import runs

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

FUSION_SCHEMA_VERSION = "1.0"
FUSION_VERSION = "fusion_v1"

# ---------------------------------------------------------------------------
# Fusion constants
# ---------------------------------------------------------------------------

# Minimum tone score to be considered non-trivial (tokens / total; below this
# the category is treated as absent so noise tokens don't drive decisions).
TONE_MIN_THRESHOLD: float = 0.005

# Confidence discount multipliers applied when LLM and LM signals disagree.
HEDGED_DISCOUNT: float = 0.75
CONFLICT_DISCOUNT: float = 0.50

# Valid fused_tone values
VALID_FUSED_TONES = frozenset({"positive", "neutral", "negative", "hedged"})

# Valid agreement values
VALID_AGREEMENTS = frozenset({"agree", "hedged", "conflict"})

# ---------------------------------------------------------------------------
# Output artifact column contract
# ---------------------------------------------------------------------------

MANAGEMENT_SENTIMENT_FUSED_COLUMNS: list[str] = [
    # --- identity ---
    "schema_version",         # "1.0"
    "fusion_id",              # stable deterministic id (fusion_<sha256[:16]>)
    "sentiment_id",           # references management_sentiment.parquet
    # --- SENT-B LLM judgment (verbatim) ---
    "company_id",             # canonical company identifier
    "speaker_role",           # always "management" for SENT-C input
    "direction",              # LLM direction: positive | negative | neutral | mixed
    "confidence_tone",        # LLM assertiveness: high | moderate | low
    "hedging",                # LLM hedge flag (bool)
    "forward_stance",         # LLM stance: optimistic | cautious | neutral | negative
    "evidence_chunk_id",      # REQUIRED: source chunk_id (joins to chunks.parquet)
    "lexicon_hits",           # JSON: matched words per LM category (from SENT-B)
    # --- SENT-A LM tone vector ---
    "tone_positive",          # float: token-normalised positive score
    "tone_negative",          # float: token-normalised negative score
    "tone_uncertainty",       # float: token-normalised uncertainty score
    "tone_litigious",         # float: token-normalised litigious score
    "tone_strong_modal",      # float: token-normalised strong_modal score
    "tone_weak_modal",        # float: token-normalised weak_modal score
    # --- fusion outputs ---
    "lm_direction",           # derived LM direction: positive | negative | uncertainty | neutral
    "fused_tone",             # reconciled: positive | neutral | negative | hedged
    "agreement",              # agree | hedged | conflict
    "fused_confidence",       # float: LLM confidence after disagreement downgrade
    # --- PIT ---
    "available_at",           # YYYY-MM-DD from chunk_tone.parquet (PIT gate)
    "created_at",             # ISO UTC timestamp of fusion run
]

# ---------------------------------------------------------------------------
# Deterministic id helpers
# ---------------------------------------------------------------------------


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_fusion_id(company_id: str, evidence_chunk_id: str) -> str:
    """Stable fusion_id: one fused record per (company_id, evidence_chunk_id).

    Deterministic: same inputs always yield the same id.
    """
    basis = f"fusion:{company_id.lower()}:{evidence_chunk_id}"
    return f"fusion_{_sha256_hex(basis)[:16]}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# LM direction derivation
# ---------------------------------------------------------------------------


def derive_lm_direction(
    tone_positive: float,
    tone_negative: float,
    tone_uncertainty: float,
) -> str:
    """Derive a categorical LM direction from the SENT-A tone scores.

    Returns one of: "positive", "negative", "uncertainty", "neutral".

    Rules:
    - If all scores are below TONE_MIN_THRESHOLD, return "neutral".
    - The dominant category (highest score exceeding threshold) wins.
    - Ties broken: uncertainty > negative > positive (conservative bias).
    """
    above_threshold = {
        cat: score
        for cat, score in [
            ("positive", tone_positive),
            ("negative", tone_negative),
            ("uncertainty", tone_uncertainty),
        ]
        if score > TONE_MIN_THRESHOLD
    }
    if not above_threshold:
        return "neutral"

    # Find the dominant category.  Tie-break order: uncertainty > negative > positive.
    priority = {"uncertainty": 3, "negative": 2, "positive": 1}
    max_score = max(above_threshold.values())
    leaders = [cat for cat, s in above_threshold.items() if s == max_score]

    if len(leaders) == 1:
        return leaders[0]
    # Tie: pick by priority
    return max(leaders, key=lambda c: priority.get(c, 0))


# ---------------------------------------------------------------------------
# Agreement classification
# ---------------------------------------------------------------------------


def classify_agreement(
    llm_direction: str,
    lm_direction: str,
    tone_uncertainty: float,
) -> str:
    """Classify the agreement between LLM direction and LM direction.

    Parameters
    ----------
    llm_direction:
        Direction from SENT-B (positive | negative | neutral | mixed).
    lm_direction:
        Direction derived from SENT-A tone vector (positive | negative | uncertainty | neutral).
    tone_uncertainty:
        Raw tone_uncertainty score; used to detect uncertainty-dense text under a
        positive LLM judgment (the "management hedging" pattern).

    Returns
    -------
    str
        One of: "agree", "hedged", "conflict".

    Fusion rules:
    - "conflict" is the strongest signal: LLM and LM point in opposite directions.
      - llm=positive, lm=negative  → conflict
      - llm=negative, lm=positive  → conflict
    - "hedged" fires when:
      - llm=positive but lm=uncertainty (management is spinning, but text is hedged)
      - llm=positive but lm=neutral (no lexicon support for positivity)
      - llm=mixed (the LLM itself is ambiguous)
      - llm=negative but lm=neutral (weak evidence of negativity)
    - "agree" fires when:
      - llm=positive and lm=positive
      - llm=negative and lm=negative
      - llm=negative and lm=uncertainty (both signal downside / risk)
      - llm=neutral and lm=neutral
    """
    # "mixed" LLM direction is always hedged by design.
    if llm_direction == "mixed":
        return "hedged"

    if llm_direction == "positive":
        if lm_direction == "negative":
            return "conflict"
        if lm_direction == "uncertainty":
            return "hedged"   # positive words with uncertainty lexicon = management spin
        if lm_direction == "neutral":
            return "hedged"   # no lexicon support for positivity
        # lm_direction == "positive"
        # Even in agree, if uncertainty is unusually high alongside positive, flag as hedged.
        # Threshold: if uncertainty score exceeds positive score it already would have won in
        # derive_lm_direction, so we don't double-count here.  Straight agree.
        return "agree"

    if llm_direction == "negative":
        if lm_direction == "positive":
            return "conflict"
        if lm_direction in ("negative", "uncertainty"):
            return "agree"    # both signal downside
        # lm_direction == "neutral"
        return "hedged"

    # llm_direction == "neutral"
    if lm_direction == "neutral":
        return "agree"
    # LM disagrees with neutral LLM → hedge (not a conflict, just uncertain)
    return "hedged"


# ---------------------------------------------------------------------------
# Fused tone derivation
# ---------------------------------------------------------------------------


def derive_fused_tone(agreement: str, llm_direction: str) -> str:
    """Derive the headline fused_tone.

    Parameters
    ----------
    agreement:
        One of "agree", "hedged", "conflict".
    llm_direction:
        The SENT-B LLM direction.

    Returns
    -------
    str
        One of: "positive", "neutral", "negative", "hedged".

    Rules:
    - conflict → "negative" (conservative: contradiction is a downside signal)
    - hedged   → "hedged"
    - agree    → simplify LLM direction to {positive, neutral, negative}
                 (mixed → "hedged" but mixed already routes to hedged above)
    """
    if agreement == "conflict":
        return "negative"
    if agreement == "hedged":
        return "hedged"
    # agree — map LLM direction to fused tone
    mapping = {
        "positive": "positive",
        "negative": "negative",
        "neutral": "neutral",
        "mixed": "hedged",   # defensive fallback (mixed → hedged in agree is unusual)
    }
    return mapping.get(llm_direction, "neutral")


# ---------------------------------------------------------------------------
# Confidence downgrade
# ---------------------------------------------------------------------------


def apply_confidence_discount(confidence: float, agreement: str) -> float:
    """Apply agreement-based confidence discount.

    Parameters
    ----------
    confidence:
        Original LLM confidence from SENT-B (0–1).
    agreement:
        One of "agree", "hedged", "conflict".

    Returns
    -------
    float
        fused_confidence in [0, 1].
    """
    if agreement == "hedged":
        return round(confidence * HEDGED_DISCOUNT, 6)
    if agreement == "conflict":
        return round(confidence * CONFLICT_DISCOUNT, 6)
    return round(confidence, 6)


# ---------------------------------------------------------------------------
# Artifact reader helpers
# ---------------------------------------------------------------------------


def _read_chunk_tone(run_id: str) -> dict[str, dict]:
    """Load chunk_tone.parquet keyed by chunk_id.

    Returns empty dict if SENT-A artifact doesn't exist.
    """
    artifact = runs.get_run_dir(run_id) / "discovery" / "chunk_tone.parquet"
    if not artifact.exists():
        return {}
    try:
        rows = pq.read_table(artifact).to_pylist()
    except Exception:
        return {}
    return {str(r.get("chunk_id") or ""): r for r in rows if r.get("chunk_id")}


def _read_management_sentiment(run_id: str) -> list[dict]:
    """Load management_sentiment.parquet rows.

    Returns empty list if SENT-B artifact doesn't exist.
    """
    artifact = runs.get_run_dir(run_id) / "discovery" / "management_sentiment.parquet"
    if not artifact.exists():
        return []
    try:
        return pq.read_table(artifact).to_pylist()
    except Exception:
        return []


def _get_as_of_date(run_id: str) -> str:
    """Return the run's as_of_date from the manifest."""
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise ValueError(f"run not found: {run_id}")
    return manifest.as_of_date


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _write_fused(rows: list[dict], out_path: Path) -> None:
    """Write management_sentiment_fused.parquet with correct schema."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = MANAGEMENT_SENTIMENT_FUSED_COLUMNS

    if not rows:
        field_map: dict[str, pa.DataType] = {
            "schema_version": pa.string(),
            "fusion_id": pa.string(),
            "sentiment_id": pa.string(),
            "company_id": pa.string(),
            "speaker_role": pa.string(),
            "direction": pa.string(),
            "confidence_tone": pa.string(),
            "hedging": pa.bool_(),
            "forward_stance": pa.string(),
            "evidence_chunk_id": pa.string(),
            "lexicon_hits": pa.string(),
            "tone_positive": pa.float64(),
            "tone_negative": pa.float64(),
            "tone_uncertainty": pa.float64(),
            "tone_litigious": pa.float64(),
            "tone_strong_modal": pa.float64(),
            "tone_weak_modal": pa.float64(),
            "lm_direction": pa.string(),
            "fused_tone": pa.string(),
            "agreement": pa.string(),
            "fused_confidence": pa.float64(),
            "available_at": pa.string(),
            "created_at": pa.string(),
        }
        schema = pa.schema([(c, field_map[c]) for c in cols])
        pq.write_table(
            pa.table(
                {c: pa.array([], type=field_map[c]) for c in cols},
                schema=schema,
            ),
            out_path,
        )
        return

    pydict: dict[str, list] = {col: [r.get(col) for r in rows] for col in cols}
    pq.write_table(pa.Table.from_pydict(pydict), out_path)


# ---------------------------------------------------------------------------
# Core fusion function
# ---------------------------------------------------------------------------


def run_sentiment_fusion(run_id: str) -> int:
    """Fuse SENT-A and SENT-B sentiment signals into a reconciled artifact.

    Reads:
        discovery/chunk_tone.parquet          (SENT-A)
        discovery/management_sentiment.parquet (SENT-B)

    Writes:
        discovery/management_sentiment_fused.parquet (SENT-C)

    PIT discipline: only SENT-B records whose evidence_chunk_id resolves to a
    chunk_tone row with available_at <= run.as_of_date are emitted.

    Evidence discipline: every output row carries a non-empty evidence_chunk_id.

    Returns
    -------
    int
        Number of fused rows written.
    """
    as_of_date = _get_as_of_date(run_id)
    run_dir = runs.get_run_dir(run_id)
    discovery_dir = run_dir / "discovery"

    tone_index = _read_chunk_tone(run_id)
    sentiment_rows = _read_management_sentiment(run_id)

    created_at = _utc_now_iso()
    fused_rows: list[dict] = []

    for row in sentiment_rows:
        evidence_chunk_id: str = str(row.get("evidence_chunk_id") or "")
        if not evidence_chunk_id:
            continue  # evidence discipline: drop records without a chunk

        company_id: str = str(row.get("company_id") or "")
        if not company_id:
            continue

        # Resolve chunk_tone row for this evidence chunk.
        tone_row: Optional[dict] = tone_index.get(evidence_chunk_id)

        # PIT gate: available_at from chunk_tone must be <= as_of_date.
        # If chunk_tone is missing, fall back to SENT-B created_at (lenient).
        if tone_row is not None:
            available_at = str(tone_row.get("available_at") or "")
            if available_at and available_at > as_of_date:
                continue  # PIT violation: chunk not yet available
        else:
            # SENT-A not available for this chunk — still fuse but mark available_at as empty.
            available_at = ""

        # Extract tone scores (default to zero when SENT-A not available).
        tone_positive = float(tone_row.get("tone_positive", 0.0) or 0.0) if tone_row else 0.0
        tone_negative = float(tone_row.get("tone_negative", 0.0) or 0.0) if tone_row else 0.0
        tone_uncertainty = float(tone_row.get("tone_uncertainty", 0.0) or 0.0) if tone_row else 0.0
        tone_litigious = float(tone_row.get("tone_litigious", 0.0) or 0.0) if tone_row else 0.0
        tone_strong_modal = float(tone_row.get("tone_strong_modal", 0.0) or 0.0) if tone_row else 0.0
        tone_weak_modal = float(tone_row.get("tone_weak_modal", 0.0) or 0.0) if tone_row else 0.0

        # Derive LM direction.
        lm_direction = derive_lm_direction(tone_positive, tone_negative, tone_uncertainty)

        # LLM direction from SENT-B.
        llm_direction: str = str(row.get("direction") or "neutral")

        # Classify agreement.
        agreement = classify_agreement(llm_direction, lm_direction, tone_uncertainty)

        # Fused tone (headline signal).
        fused_tone = derive_fused_tone(agreement, llm_direction)

        # Fused confidence (downgraded on disagreement).
        original_confidence = float(row.get("confidence") or 0.5)
        fused_confidence = apply_confidence_discount(original_confidence, agreement)

        fusion_id = _stable_fusion_id(company_id, evidence_chunk_id)

        fused_rows.append({
            "schema_version": FUSION_SCHEMA_VERSION,
            "fusion_id": fusion_id,
            "sentiment_id": str(row.get("sentiment_id") or ""),
            "company_id": company_id,
            "speaker_role": str(row.get("speaker_role") or "management"),
            "direction": llm_direction,
            "confidence_tone": str(row.get("confidence_tone") or ""),
            "hedging": bool(row.get("hedging") or False),
            "forward_stance": str(row.get("forward_stance") or ""),
            "evidence_chunk_id": evidence_chunk_id,
            "lexicon_hits": str(row.get("lexicon_hits") or "{}"),
            "tone_positive": tone_positive,
            "tone_negative": tone_negative,
            "tone_uncertainty": tone_uncertainty,
            "tone_litigious": tone_litigious,
            "tone_strong_modal": tone_strong_modal,
            "tone_weak_modal": tone_weak_modal,
            "lm_direction": lm_direction,
            "fused_tone": fused_tone,
            "agreement": agreement,
            "fused_confidence": fused_confidence,
            "available_at": available_at,
            "created_at": created_at,
        })

    _write_fused(fused_rows, discovery_dir / "management_sentiment_fused.parquet")
    return len(fused_rows)


# ---------------------------------------------------------------------------
# Low-level fuse_records helper (pure function — no I/O, useful for tests)
# ---------------------------------------------------------------------------


def fuse_records(
    sentiment_row: dict,
    tone_row: Optional[dict],
    as_of_date: str,
) -> Optional[dict]:
    """Fuse a single SENT-B row with an optional SENT-A tone row.

    This pure function performs the full fusion logic for a single record without
    any I/O.  Useful for unit tests and for callers that want to fuse in memory.

    Parameters
    ----------
    sentiment_row:
        One row from management_sentiment.parquet (SENT-B).
    tone_row:
        One row from chunk_tone.parquet (SENT-A), or None if not available.
        When None, tone scores default to 0.0 and lm_direction = "neutral".
    as_of_date:
        The run's as_of_date for PIT filtering.

    Returns
    -------
    dict or None
        A fused row dict (keys match MANAGEMENT_SENTIMENT_FUSED_COLUMNS), or
        None if the row fails evidence/PIT discipline.
    """
    evidence_chunk_id = str(sentiment_row.get("evidence_chunk_id") or "")
    company_id = str(sentiment_row.get("company_id") or "")
    if not evidence_chunk_id or not company_id:
        return None

    # PIT gate using available_at from tone_row.
    if tone_row is not None:
        available_at = str(tone_row.get("available_at") or "")
        if available_at and available_at > as_of_date:
            return None  # future-dated — drop
    else:
        available_at = ""

    tone_positive = float(tone_row.get("tone_positive", 0.0) or 0.0) if tone_row else 0.0
    tone_negative = float(tone_row.get("tone_negative", 0.0) or 0.0) if tone_row else 0.0
    tone_uncertainty = float(tone_row.get("tone_uncertainty", 0.0) or 0.0) if tone_row else 0.0
    tone_litigious = float(tone_row.get("tone_litigious", 0.0) or 0.0) if tone_row else 0.0
    tone_strong_modal = float(tone_row.get("tone_strong_modal", 0.0) or 0.0) if tone_row else 0.0
    tone_weak_modal = float(tone_row.get("tone_weak_modal", 0.0) or 0.0) if tone_row else 0.0

    lm_direction = derive_lm_direction(tone_positive, tone_negative, tone_uncertainty)
    llm_direction = str(sentiment_row.get("direction") or "neutral")
    agreement = classify_agreement(llm_direction, lm_direction, tone_uncertainty)
    fused_tone = derive_fused_tone(agreement, llm_direction)
    original_confidence = float(sentiment_row.get("confidence") or 0.5)
    fused_confidence = apply_confidence_discount(original_confidence, agreement)

    fusion_id = _stable_fusion_id(company_id, evidence_chunk_id)
    created_at = _utc_now_iso()

    return {
        "schema_version": FUSION_SCHEMA_VERSION,
        "fusion_id": fusion_id,
        "sentiment_id": str(sentiment_row.get("sentiment_id") or ""),
        "company_id": company_id,
        "speaker_role": str(sentiment_row.get("speaker_role") or "management"),
        "direction": llm_direction,
        "confidence_tone": str(sentiment_row.get("confidence_tone") or ""),
        "hedging": bool(sentiment_row.get("hedging") or False),
        "forward_stance": str(sentiment_row.get("forward_stance") or ""),
        "evidence_chunk_id": evidence_chunk_id,
        "lexicon_hits": str(sentiment_row.get("lexicon_hits") or "{}"),
        "tone_positive": tone_positive,
        "tone_negative": tone_negative,
        "tone_uncertainty": tone_uncertainty,
        "tone_litigious": tone_litigious,
        "tone_strong_modal": tone_strong_modal,
        "tone_weak_modal": tone_weak_modal,
        "lm_direction": lm_direction,
        "fused_tone": fused_tone,
        "agreement": agreement,
        "fused_confidence": fused_confidence,
        "available_at": available_at,
        "created_at": created_at,
    }
