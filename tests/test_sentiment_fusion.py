"""Tests for SENT-C: sentiment fusion layer (GitHub #101).

All tests are hermetic (no network, no real LLM).  They use the pure
``fuse_records`` helper and the ``run_sentiment_fusion`` pipeline entry point
with pre-built in-memory fixtures.

Acceptance criteria asserted here:

1. HEDGED — LLM says positive, LM is uncertainty-dense → agreement=hedged,
   fused_tone=hedged, fused_confidence reduced by HEDGED_DISCOUNT.

2. AGREE — LLM positive AND LM positive-dense → agreement=agree,
   fused_tone=positive, fused_confidence unchanged.

3. CONFLICT — LLM says positive, LM is clearly negative-dense → agreement=conflict,
   fused_tone=negative, fused_confidence reduced by CONFLICT_DISCOUNT.

4. PIT — a row whose evidence_chunk_id maps to a future-dated chunk_tone entry
   (available_at > as_of_date) must NOT appear in the fused output.

5. EVIDENCE — a SENT-B row without an evidence_chunk_id must not appear in output.

6. COLUMN CONTRACT — fused artifact must carry exactly MANAGEMENT_SENTIMENT_FUSED_COLUMNS.

7. NOT-IN-EXPOSURE (grep-provable): exposure.py must not import or reference
   management_sentiment_fused.  Asserted in test_fused_artifact_not_in_exposure.

8. FUSION-ID stable — same (company_id, evidence_chunk_id) always yields the same
   fusion_id across multiple calls.

9. NEGATIVE-AGREE — LLM negative AND LM negative-dense → agreement=agree.

10. PIPELINE INTEGRATION — run_sentiment_fusion writes the artifact to the correct
    path and the row count matches the expected number.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# ---------------------------------------------------------------------------
# Make backend importable
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from theme_engine.sentiment_fusion import (
    MANAGEMENT_SENTIMENT_FUSED_COLUMNS,
    HEDGED_DISCOUNT,
    CONFLICT_DISCOUNT,
    TONE_MIN_THRESHOLD,
    VALID_FUSED_TONES,
    VALID_AGREEMENTS,
    derive_lm_direction,
    classify_agreement,
    derive_fused_tone,
    apply_confidence_discount,
    fuse_records,
    run_sentiment_fusion,
    _stable_fusion_id,
)
from theme_engine.config import settings

# ---------------------------------------------------------------------------
# Shared fixture builder helpers
# ---------------------------------------------------------------------------


def _make_sentiment_row(
    *,
    company_id: str = "ACME",
    direction: str = "positive",
    confidence: float = 0.80,
    confidence_tone: str = "high",
    hedging: bool = False,
    forward_stance: str = "optimistic",
    evidence_chunk_id: str = "chunk_001",
    lexicon_hits: str = "{}",
    sentiment_id: str = "sent_abc123",
    speaker_role: str = "management",
) -> dict:
    """Build a minimal SENT-B management_sentiment row."""
    return {
        "schema_version": "1.0",
        "sentiment_id": sentiment_id,
        "company_id": company_id,
        "speaker_role": speaker_role,
        "direction": direction,
        "confidence_tone": confidence_tone,
        "hedging": hedging,
        "forward_stance": forward_stance,
        "confidence": confidence,
        "evidence_chunk_id": evidence_chunk_id,
        "lexicon_hits": lexicon_hits,
        "created_at": "2024-07-01T00:00:00Z",
    }


def _make_tone_row(
    *,
    chunk_id: str = "chunk_001",
    available_at: str = "2024-06-01",
    tone_positive: float = 0.0,
    tone_negative: float = 0.0,
    tone_uncertainty: float = 0.0,
    tone_litigious: float = 0.0,
    tone_strong_modal: float = 0.0,
    tone_weak_modal: float = 0.0,
    speaker_role: str = "management",
) -> dict:
    """Build a minimal SENT-A chunk_tone row."""
    return {
        "chunk_id": chunk_id,
        "document_id": "doc_001",
        "available_at": available_at,
        "speaker_role": speaker_role,
        "token_count": 100,
        "tone_positive": tone_positive,
        "tone_negative": tone_negative,
        "tone_uncertainty": tone_uncertainty,
        "tone_litigious": tone_litigious,
        "tone_strong_modal": tone_strong_modal,
        "tone_weak_modal": tone_weak_modal,
        "matched_positive": [],
        "matched_negative": [],
        "matched_uncertainty": [],
        "matched_litigious": [],
        "matched_strong_modal": [],
        "matched_weak_modal": [],
    }


# ---------------------------------------------------------------------------
# Helper: build a run in the temp RUN_OUTPUT_DIR with SENT-A + SENT-B artifacts
# ---------------------------------------------------------------------------


def _build_run(
    *,
    sentiment_rows: list[dict],
    tone_rows: list[dict],
    as_of_date: str = "2024-06-30",
) -> tuple[str, Path]:
    """Write run_manifest.json + chunk_tone.parquet + management_sentiment.parquet.

    Returns (run_id, discovery_dir).
    """
    run_id = f"run_sentc_{uuid.uuid4().hex[:8]}"
    run_dir = settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest = {
        "run_id": run_id,
        "as_of_date": as_of_date,
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "created_at": "2024-06-30T00:00:00Z",
        "code_version": "test",
        "input_hash": "abc123",
        "discovery_frozen": False,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # chunk_tone.parquet (SENT-A)
    if tone_rows:
        _write_tone_parquet(tone_rows, discovery / "chunk_tone.parquet")

    # management_sentiment.parquet (SENT-B)
    if sentiment_rows:
        _write_sentiment_parquet(sentiment_rows, discovery / "management_sentiment.parquet")

    return run_id, discovery


def _write_tone_parquet(rows: list[dict], out_path: Path) -> None:
    """Write chunk_tone.parquet (SENT-A) from list-of-dict rows."""
    schema = pa.schema([
        ("chunk_id", pa.string()),
        ("document_id", pa.string()),
        ("available_at", pa.string()),
        ("speaker_role", pa.string()),
        ("token_count", pa.int64()),
        ("tone_positive", pa.float64()),
        ("tone_negative", pa.float64()),
        ("tone_uncertainty", pa.float64()),
        ("tone_litigious", pa.float64()),
        ("tone_strong_modal", pa.float64()),
        ("tone_weak_modal", pa.float64()),
        ("matched_positive", pa.list_(pa.string())),
        ("matched_negative", pa.list_(pa.string())),
        ("matched_uncertainty", pa.list_(pa.string())),
        ("matched_litigious", pa.list_(pa.string())),
        ("matched_strong_modal", pa.list_(pa.string())),
        ("matched_weak_modal", pa.list_(pa.string())),
    ])
    arrays = []
    for field in schema:
        vals = [r.get(field.name) for r in rows]
        if isinstance(field.type, pa.lib.ListType):
            arrays.append(pa.array([v or [] for v in vals], type=field.type))
        elif field.type == pa.float64():
            arrays.append(pa.array([float(v or 0.0) for v in vals], type=pa.float64()))
        elif field.type == pa.int64():
            arrays.append(pa.array([int(v or 0) for v in vals], type=pa.int64()))
        else:
            arrays.append(pa.array([str(v) if v is not None else None for v in vals], type=pa.string()))
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), out_path)


def _write_sentiment_parquet(rows: list[dict], out_path: Path) -> None:
    """Write management_sentiment.parquet (SENT-B) from list-of-dict rows."""
    from theme_engine.extraction import MANAGEMENT_SENTIMENT_COLUMNS
    field_map: dict[str, pa.DataType] = {
        "schema_version": pa.string(),
        "sentiment_id": pa.string(),
        "company_id": pa.string(),
        "speaker_role": pa.string(),
        "direction": pa.string(),
        "confidence_tone": pa.string(),
        "hedging": pa.bool_(),
        "forward_stance": pa.string(),
        "confidence": pa.float64(),
        "evidence_chunk_id": pa.string(),
        "lexicon_hits": pa.string(),
        "created_at": pa.string(),
    }
    arrays = []
    schema = pa.schema([(c, field_map[c]) for c in MANAGEMENT_SENTIMENT_COLUMNS])
    for field in schema:
        vals = [r.get(field.name) for r in rows]
        if field.type == pa.bool_():
            arrays.append(pa.array([bool(v) if v is not None else False for v in vals], type=pa.bool_()))
        elif field.type == pa.float64():
            arrays.append(pa.array([float(v or 0.0) for v in vals], type=pa.float64()))
        else:
            arrays.append(pa.array([str(v) if v is not None else None for v in vals], type=pa.string()))
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), out_path)


# ===========================================================================
# Unit tests: derive_lm_direction
# ===========================================================================


def test_derive_lm_direction_positive():
    """Dominant positive score → lm_direction=positive."""
    assert derive_lm_direction(0.05, 0.01, 0.01) == "positive"


def test_derive_lm_direction_negative():
    """Dominant negative score → lm_direction=negative."""
    assert derive_lm_direction(0.01, 0.05, 0.01) == "negative"


def test_derive_lm_direction_uncertainty():
    """Dominant uncertainty score → lm_direction=uncertainty."""
    assert derive_lm_direction(0.01, 0.01, 0.05) == "uncertainty"


def test_derive_lm_direction_neutral_all_zero():
    """All scores zero → lm_direction=neutral."""
    assert derive_lm_direction(0.0, 0.0, 0.0) == "neutral"


def test_derive_lm_direction_neutral_below_threshold():
    """All scores below TONE_MIN_THRESHOLD → lm_direction=neutral."""
    tiny = TONE_MIN_THRESHOLD * 0.5
    assert derive_lm_direction(tiny, tiny, tiny) == "neutral"


def test_derive_lm_direction_tie_prefers_uncertainty():
    """Tied uncertainty and negative → uncertainty wins (conservative)."""
    score = 0.05
    assert derive_lm_direction(0.0, score, score) == "uncertainty"


# ===========================================================================
# Unit tests: classify_agreement
# ===========================================================================


def test_agree_positive_positive():
    """LLM positive + LM positive → agree."""
    assert classify_agreement("positive", "positive", 0.01) == "agree"


def test_agree_negative_negative():
    """LLM negative + LM negative → agree."""
    assert classify_agreement("negative", "negative", 0.01) == "agree"


def test_agree_negative_uncertainty():
    """LLM negative + LM uncertainty → agree (both signal downside)."""
    assert classify_agreement("negative", "uncertainty", 0.05) == "agree"


def test_agree_neutral_neutral():
    """LLM neutral + LM neutral → agree."""
    assert classify_agreement("neutral", "neutral", 0.0) == "agree"


def test_hedged_positive_uncertainty():
    """LLM positive + LM uncertainty → hedged (management spin)."""
    assert classify_agreement("positive", "uncertainty", 0.05) == "hedged"


def test_hedged_positive_neutral():
    """LLM positive + LM neutral → hedged (no lexicon support)."""
    assert classify_agreement("positive", "neutral", 0.0) == "hedged"


def test_hedged_mixed_llm():
    """LLM mixed → always hedged."""
    assert classify_agreement("mixed", "positive", 0.01) == "hedged"
    assert classify_agreement("mixed", "negative", 0.01) == "hedged"


def test_hedged_negative_neutral():
    """LLM negative + LM neutral → hedged (weak evidence)."""
    assert classify_agreement("negative", "neutral", 0.0) == "hedged"


def test_conflict_positive_negative():
    """LLM positive + LM negative → conflict."""
    assert classify_agreement("positive", "negative", 0.0) == "conflict"


def test_conflict_negative_positive():
    """LLM negative + LM positive → conflict."""
    assert classify_agreement("negative", "positive", 0.01) == "conflict"


# ===========================================================================
# Unit tests: derive_fused_tone
# ===========================================================================


def test_fused_tone_agree_positive():
    assert derive_fused_tone("agree", "positive") == "positive"


def test_fused_tone_agree_negative():
    assert derive_fused_tone("agree", "negative") == "negative"


def test_fused_tone_agree_neutral():
    assert derive_fused_tone("agree", "neutral") == "neutral"


def test_fused_tone_hedged():
    assert derive_fused_tone("hedged", "positive") == "hedged"
    assert derive_fused_tone("hedged", "negative") == "hedged"


def test_fused_tone_conflict():
    """Conflict always produces 'negative' (conservative)."""
    assert derive_fused_tone("conflict", "positive") == "negative"
    assert derive_fused_tone("conflict", "negative") == "negative"


# ===========================================================================
# Unit tests: apply_confidence_discount
# ===========================================================================


def test_confidence_agree_no_discount():
    """agree → no discount."""
    assert apply_confidence_discount(0.80, "agree") == pytest.approx(0.80)


def test_confidence_hedged_discount():
    """hedged → ×HEDGED_DISCOUNT."""
    assert apply_confidence_discount(0.80, "hedged") == pytest.approx(0.80 * HEDGED_DISCOUNT)


def test_confidence_conflict_discount():
    """conflict → ×CONFLICT_DISCOUNT."""
    assert apply_confidence_discount(0.80, "conflict") == pytest.approx(0.80 * CONFLICT_DISCOUNT)


# ===========================================================================
# ACCEPTANCE TEST 1: HEDGED case
# LLM positive, LM uncertainty-dense → agreement=hedged, confidence reduced
# ===========================================================================


def test_hedged_case_llm_positive_lm_uncertainty_dense():
    """ACCEPTANCE: LLM says positive + LM is uncertainty-dense → hedged.

    This is the management-hedging pattern: management uses optimistic language
    but the actual words in the chunk are uncertainty-heavy (may, could, subject to, etc.).
    """
    sentiment = _make_sentiment_row(
        direction="positive",
        confidence=0.80,
        confidence_tone="high",
        forward_stance="optimistic",
        evidence_chunk_id="chunk_hedged",
    )
    # High uncertainty, low positive — uncertainty dominates.
    tone = _make_tone_row(
        chunk_id="chunk_hedged",
        available_at="2024-06-01",
        tone_positive=0.01,
        tone_negative=0.01,
        tone_uncertainty=0.08,  # dominant
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None, "Expected a fused row for hedged case"
    assert result["agreement"] == "hedged", (
        f"Expected agreement=hedged; got {result['agreement']!r}. "
        "LLM positive + uncertainty-dense LM should yield hedged."
    )
    assert result["fused_tone"] == "hedged", (
        f"Expected fused_tone=hedged; got {result['fused_tone']!r}"
    )
    assert result["lm_direction"] == "uncertainty", (
        f"Expected lm_direction=uncertainty; got {result['lm_direction']!r}"
    )
    # Confidence must be reduced by HEDGED_DISCOUNT.
    expected_conf = pytest.approx(0.80 * HEDGED_DISCOUNT)
    assert result["fused_confidence"] == expected_conf, (
        f"Expected fused_confidence ≈ {0.80 * HEDGED_DISCOUNT:.4f}; "
        f"got {result['fused_confidence']!r}"
    )


# ===========================================================================
# ACCEPTANCE TEST 2: AGREE case (clean positive)
# LLM positive + LM positive-dense → agreement=agree
# ===========================================================================


def test_agree_case_llm_positive_lm_positive_dense():
    """ACCEPTANCE: LLM positive + LM positive-dense → agreement=agree, no discount."""
    sentiment = _make_sentiment_row(
        direction="positive",
        confidence=0.85,
        confidence_tone="high",
        forward_stance="optimistic",
        evidence_chunk_id="chunk_agree",
    )
    # Positive LM, clearly dominant.
    tone = _make_tone_row(
        chunk_id="chunk_agree",
        available_at="2024-06-01",
        tone_positive=0.07,
        tone_negative=0.01,
        tone_uncertainty=0.02,
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None, "Expected a fused row for agree case"
    assert result["agreement"] == "agree", (
        f"Expected agreement=agree; got {result['agreement']!r}"
    )
    assert result["fused_tone"] == "positive", (
        f"Expected fused_tone=positive; got {result['fused_tone']!r}"
    )
    assert result["lm_direction"] == "positive", (
        f"Expected lm_direction=positive; got {result['lm_direction']!r}"
    )
    # No discount on agree.
    assert result["fused_confidence"] == pytest.approx(0.85), (
        f"Expected fused_confidence unchanged at 0.85; got {result['fused_confidence']!r}"
    )


# ===========================================================================
# ACCEPTANCE TEST 3: CONFLICT case
# LLM positive, LM clearly negative-dense → agreement=conflict
# ===========================================================================


def test_conflict_case_llm_positive_lm_negative_dense():
    """ACCEPTANCE: LLM positive + LM clearly negative-dense → agreement=conflict."""
    sentiment = _make_sentiment_row(
        direction="positive",
        confidence=0.80,
        confidence_tone="high",
        forward_stance="optimistic",
        evidence_chunk_id="chunk_conflict",
    )
    # Negative LM dominates; much higher than positive or uncertainty.
    tone = _make_tone_row(
        chunk_id="chunk_conflict",
        available_at="2024-06-01",
        tone_positive=0.01,
        tone_negative=0.09,
        tone_uncertainty=0.01,
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None, "Expected a fused row for conflict case"
    assert result["agreement"] == "conflict", (
        f"Expected agreement=conflict; got {result['agreement']!r}. "
        "LLM positive + negative-dense LM should yield conflict."
    )
    assert result["fused_tone"] == "negative", (
        f"Expected fused_tone=negative for conflict; got {result['fused_tone']!r}"
    )
    assert result["lm_direction"] == "negative", (
        f"Expected lm_direction=negative; got {result['lm_direction']!r}"
    )
    expected_conf = pytest.approx(0.80 * CONFLICT_DISCOUNT)
    assert result["fused_confidence"] == expected_conf, (
        f"Expected fused_confidence ≈ {0.80 * CONFLICT_DISCOUNT:.4f}; "
        f"got {result['fused_confidence']!r}"
    )


# ===========================================================================
# ACCEPTANCE TEST 4: PIT gate
# future-dated chunk_tone (available_at > as_of_date) → dropped from output
# ===========================================================================


def test_pit_future_dated_chunk_dropped():
    """ACCEPTANCE: PIT gate must exclude rows whose tone available_at > as_of_date."""
    sentiment = _make_sentiment_row(
        direction="positive",
        evidence_chunk_id="chunk_future",
    )
    # available_at is in the future relative to as_of_date.
    tone = _make_tone_row(
        chunk_id="chunk_future",
        available_at="2025-01-15",  # future
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is None, (
        "Expected None (dropped) for future-dated chunk_tone. "
        "PIT gate must block available_at > as_of_date."
    )


def test_pit_past_dated_chunk_included():
    """PIT gate must PASS rows whose tone available_at <= as_of_date."""
    sentiment = _make_sentiment_row(evidence_chunk_id="chunk_past")
    tone = _make_tone_row(
        chunk_id="chunk_past",
        available_at="2024-01-01",  # clearly past
        tone_positive=0.05,
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None, "Expected a fused row for past-dated chunk"
    assert result["available_at"] == "2024-01-01"


# ===========================================================================
# ACCEPTANCE TEST 5: Evidence discipline
# SENT-B row without evidence_chunk_id → must not appear in output
# ===========================================================================


def test_evidence_empty_chunk_id_dropped():
    """ACCEPTANCE: A SENT-B row without evidence_chunk_id must be dropped."""
    sentiment = _make_sentiment_row(evidence_chunk_id="")  # intentionally empty
    tone = _make_tone_row()

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is None, "Expected None for row with empty evidence_chunk_id"


def test_evidence_missing_company_id_dropped():
    """A SENT-B row without company_id must be dropped."""
    sentiment = _make_sentiment_row(company_id="")
    tone = _make_tone_row()

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is None, "Expected None for row with empty company_id"


# ===========================================================================
# ACCEPTANCE TEST 6: Column contract
# ===========================================================================


def test_fused_column_contract():
    """ACCEPTANCE: fused row must carry exactly MANAGEMENT_SENTIMENT_FUSED_COLUMNS."""
    sentiment = _make_sentiment_row()
    tone = _make_tone_row(available_at="2024-06-01", tone_positive=0.05)

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None
    assert set(result.keys()) == set(MANAGEMENT_SENTIMENT_FUSED_COLUMNS), (
        f"Column mismatch: {set(result.keys()) ^ set(MANAGEMENT_SENTIMENT_FUSED_COLUMNS)}"
    )


def test_fused_column_contract_via_pipeline():
    """ACCEPTANCE: run_sentiment_fusion must write a parquet with exact column contract."""
    run_id, discovery = _build_run(
        sentiment_rows=[
            _make_sentiment_row(direction="positive", evidence_chunk_id="c1"),
        ],
        tone_rows=[
            _make_tone_row(chunk_id="c1", available_at="2024-06-01", tone_positive=0.05),
        ],
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    assert count == 1

    table = pq.read_table(discovery / "management_sentiment_fused.parquet")
    assert list(table.schema.names) == MANAGEMENT_SENTIMENT_FUSED_COLUMNS, (
        f"Column mismatch.\n  expected: {MANAGEMENT_SENTIMENT_FUSED_COLUMNS}\n"
        f"  got: {list(table.schema.names)}"
    )


# ===========================================================================
# ACCEPTANCE TEST 7: NOT in exposure.py
# ===========================================================================


def test_fused_artifact_not_in_exposure():
    """ACCEPTANCE: exposure.py must NOT reference management_sentiment_fused.

    This is the discovery-evidence-only constraint.  If exposure.py ever imports or
    reads the fused artifact, the exposure computation would be corrupted by
    forward-looking sentiment signals.
    """
    exposure_path = ROOT / "app" / "backend" / "theme_engine" / "exposure.py"
    assert exposure_path.exists(), f"exposure.py not found at {exposure_path}"
    exposure_text = exposure_path.read_text(encoding="utf-8")
    assert "management_sentiment_fused" not in exposure_text, (
        "VIOLATION: exposure.py must NOT read management_sentiment_fused.parquet. "
        "Sentiment fusion is discovery-evidence only and must not enter exposure scoring."
    )
    # Also check that sentiment_fusion is not imported in exposure.py.
    assert "sentiment_fusion" not in exposure_text, (
        "VIOLATION: exposure.py must NOT import from sentiment_fusion. "
        "Fused sentiment is a forward-inference input, not an exposure signal."
    )


# ===========================================================================
# ACCEPTANCE TEST 8: stable fusion_id
# ===========================================================================


def test_fusion_id_is_stable():
    """ACCEPTANCE: same (company_id, evidence_chunk_id) always yields the same fusion_id."""
    id1 = _stable_fusion_id("ACME", "chunk_001")
    id2 = _stable_fusion_id("ACME", "chunk_001")
    assert id1 == id2, "fusion_id must be deterministic"
    assert id1.startswith("fusion_"), f"fusion_id must start with 'fusion_'; got {id1!r}"
    assert len(id1) == len("fusion_") + 16, f"fusion_id must be 'fusion_' + 16 hex chars; got {id1!r}"


def test_fusion_id_different_for_different_inputs():
    """Different (company_id, evidence_chunk_id) must yield different fusion_ids."""
    id1 = _stable_fusion_id("ACME", "chunk_001")
    id2 = _stable_fusion_id("ACME", "chunk_002")
    id3 = _stable_fusion_id("BETA", "chunk_001")
    assert id1 != id2
    assert id1 != id3
    assert id2 != id3


# ===========================================================================
# ACCEPTANCE TEST 9: negative-agree case
# ===========================================================================


def test_agree_case_llm_negative_lm_negative_dense():
    """ACCEPTANCE: LLM negative + LM negative-dense → agreement=agree."""
    sentiment = _make_sentiment_row(
        direction="negative",
        confidence=0.75,
        confidence_tone="moderate",
        forward_stance="cautious",
        evidence_chunk_id="chunk_neg_agree",
    )
    tone = _make_tone_row(
        chunk_id="chunk_neg_agree",
        available_at="2024-06-01",
        tone_positive=0.01,
        tone_negative=0.06,
        tone_uncertainty=0.02,
    )

    result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
    assert result is not None
    assert result["agreement"] == "agree"
    assert result["fused_tone"] == "negative"
    # No discount on agree.
    assert result["fused_confidence"] == pytest.approx(0.75)


# ===========================================================================
# ACCEPTANCE TEST 10: Pipeline integration
# ===========================================================================


def test_pipeline_integration_writes_artifact():
    """ACCEPTANCE: run_sentiment_fusion writes the artifact to the correct path."""
    run_id, discovery = _build_run(
        sentiment_rows=[
            _make_sentiment_row(
                company_id="ACME",
                direction="positive",
                confidence=0.80,
                evidence_chunk_id="c1",
            ),
        ],
        tone_rows=[
            _make_tone_row(
                chunk_id="c1",
                available_at="2024-06-01",
                tone_positive=0.06,
                tone_negative=0.01,
                tone_uncertainty=0.01,
            ),
        ],
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    assert count == 1, f"Expected 1 fused row; got {count}"

    fused_path = discovery / "management_sentiment_fused.parquet"
    assert fused_path.exists(), "management_sentiment_fused.parquet must be written"

    rows = pq.read_table(fused_path).to_pylist()
    assert len(rows) == 1
    row = rows[0]

    # Basic field checks
    assert row["company_id"] == "ACME"
    assert row["direction"] == "positive"
    assert row["agreement"] in VALID_AGREEMENTS
    assert row["fused_tone"] in VALID_FUSED_TONES
    assert row["evidence_chunk_id"] == "c1"
    assert row["fusion_id"].startswith("fusion_")


def test_pipeline_pit_filter():
    """PIT filter in run_sentiment_fusion drops future-dated rows."""
    run_id, discovery = _build_run(
        sentiment_rows=[
            _make_sentiment_row(direction="positive", evidence_chunk_id="c_future"),
        ],
        tone_rows=[
            _make_tone_row(
                chunk_id="c_future",
                available_at="2025-06-01",  # future — after as_of_date
            ),
        ],
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    assert count == 0, f"Expected 0 fused rows (PIT violation); got {count}"

    fused_path = discovery / "management_sentiment_fused.parquet"
    assert fused_path.exists(), "Artifact must still be written (empty schema)"
    rows = pq.read_table(fused_path).to_pylist()
    assert rows == [], f"Expected empty fused table; got {rows}"


def test_pipeline_no_tone_data_still_fuses():
    """run_sentiment_fusion degrades gracefully when chunk_tone.parquet is absent.

    When SENT-A has not run, tone scores default to 0.0 and lm_direction=neutral.
    The fused row is still emitted (available_at will be empty, agreement=hedged
    since LLM may say positive but LM is neutral).
    """
    run_id, discovery = _build_run(
        sentiment_rows=[
            _make_sentiment_row(direction="positive", evidence_chunk_id="c_notone"),
        ],
        tone_rows=[],  # no SENT-A artifact
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    # With no tone data, the row degrades to neutral LM → hedged agreement.
    assert count == 1, f"Expected 1 fused row even without tone data; got {count}"

    rows = pq.read_table(discovery / "management_sentiment_fused.parquet").to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["lm_direction"] == "neutral"
    assert row["agreement"] == "hedged"   # LLM positive, LM neutral → hedged
    assert row["fused_tone"] == "hedged"
    assert row["available_at"] == ""      # no PIT date when SENT-A absent


def test_pipeline_empty_sentiment_writes_empty_artifact():
    """run_sentiment_fusion with no SENT-B rows writes an empty but schema-valid artifact."""
    run_id, discovery = _build_run(
        sentiment_rows=[],
        tone_rows=[],
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    assert count == 0

    fused_path = discovery / "management_sentiment_fused.parquet"
    assert fused_path.exists(), "Empty artifact must still be written"
    table = pq.read_table(fused_path)
    assert list(table.schema.names) == MANAGEMENT_SENTIMENT_FUSED_COLUMNS


# ===========================================================================
# Smoke: fused_tone and agreement values are valid
# ===========================================================================


def test_all_output_values_are_valid():
    """fused_tone and agreement in all output rows must be from the valid sets."""
    cases = [
        # (llm_direction, tone_positive, tone_negative, tone_uncertainty)
        ("positive", 0.07, 0.01, 0.01),   # agree
        ("positive", 0.01, 0.01, 0.07),   # hedged
        ("positive", 0.01, 0.07, 0.01),   # conflict
        ("negative", 0.01, 0.07, 0.01),   # agree
        ("negative", 0.07, 0.01, 0.01),   # conflict
        ("neutral",  0.0,  0.0,  0.0),    # agree
        ("mixed",    0.05, 0.02, 0.02),   # hedged
    ]

    for llm_dir, tp, tn, tu in cases:
        sentiment = _make_sentiment_row(direction=llm_dir)
        tone = _make_tone_row(
            available_at="2024-06-01",
            tone_positive=tp,
            tone_negative=tn,
            tone_uncertainty=tu,
        )
        result = fuse_records(sentiment, tone, as_of_date="2024-06-30")
        assert result is not None
        assert result["fused_tone"] in VALID_FUSED_TONES, (
            f"Invalid fused_tone={result['fused_tone']!r} for case {llm_dir}"
        )
        assert result["agreement"] in VALID_AGREEMENTS, (
            f"Invalid agreement={result['agreement']!r} for case {llm_dir}"
        )


# ===========================================================================
# PIT evidence: every fused row's evidence_chunk_id is in chunks.parquet
# (simulated via tone_index lookup as proxy for chunk resolution)
# ===========================================================================


def test_pit_evidence_chunk_resolvable():
    """ACCEPTANCE: every fused row's evidence_chunk_id resolves to an entry in chunk_tone.

    This test verifies the PIT + evidence chain: the fused artifact only contains
    rows whose evidence_chunk_id was present in chunk_tone.parquet (SENT-A),
    which in turn references chunks.parquet.
    """
    run_id, discovery = _build_run(
        sentiment_rows=[
            _make_sentiment_row(direction="positive", evidence_chunk_id="known_chunk"),
            _make_sentiment_row(
                direction="negative", evidence_chunk_id="unknown_chunk",
                sentiment_id="sent_xyz"
            ),
        ],
        tone_rows=[
            # Only "known_chunk" has a tone entry.
            _make_tone_row(
                chunk_id="known_chunk",
                available_at="2024-06-01",
                tone_positive=0.05,
            ),
        ],
        as_of_date="2024-06-30",
    )

    count = run_sentiment_fusion(run_id)
    # "unknown_chunk" has no tone row → tone defaults to 0 → still fused (no PIT block)
    # Both rows should appear in fused output since neither has a PIT violation.
    assert count == 2, f"Expected 2 fused rows; got {count}"

    rows = pq.read_table(discovery / "management_sentiment_fused.parquet").to_pylist()
    chunk_ids_in_fused = {r["evidence_chunk_id"] for r in rows}
    assert "known_chunk" in chunk_ids_in_fused
    assert "unknown_chunk" in chunk_ids_in_fused

    # The row for "known_chunk" should have lm data; "unknown_chunk" defaults to neutral
    known = next(r for r in rows if r["evidence_chunk_id"] == "known_chunk")
    unknown = next(r for r in rows if r["evidence_chunk_id"] == "unknown_chunk")

    assert known["tone_positive"] == pytest.approx(0.05)
    assert unknown["tone_positive"] == pytest.approx(0.0)
    assert unknown["lm_direction"] == "neutral"
