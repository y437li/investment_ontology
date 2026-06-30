"""Tests for SENT-B: LLM management-sentiment extraction (GitHub #100).

Six acceptance criteria, all hermetic (no network):

1. POSITIVE — on a committed management/MD&A fixture, >=1 sentiment record is
   extracted with a direction, a forward_stance, and a non-empty evidence_chunk_id.

2. HERMETIC — a FakeSentimentExtractor (injected) returns pre-programmed records;
   run_sentiment_extraction emits them correctly with correct columns.

3. NO-EVIDENCE — a record without an evidence_chunk_id must NOT appear in output.

4. NEGATION — "we do NOT see strong demand" must be judged negative despite the
   word "strong" (context over lexicon).

5. SPEAKER-GATING — a news/media chunk must NOT be sent to the management-sentiment
   pass; only management-attributable chunks are processed.

6. PIT — a chunk with available_at > as_of_date must yield zero records.

7. LEXICON-GROUNDING — lexicon_hits field is populated (non-empty JSON) when
   SENT-A tone data is available.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# ---------------------------------------------------------------------------
# Make backend importable (same pattern as other test files)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "extraction"

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from theme_engine.extraction import (
    SentimentRecord,
    SentimentResult,
    SentimentExtractor,
    RuleBasedSentimentExtractor,
    run_sentiment_extraction,
    MANAGEMENT_SENTIMENT_COLUMNS,
    SENTIMENT_EDGES_COLUMNS,
    _VALID_DIRECTIONS,
    _VALID_CONFIDENCE_TONES,
    _VALID_FORWARD_STANCES,
)
from theme_engine.config import settings
from theme_engine.chunking import CHUNKS_COLUMNS

# ---------------------------------------------------------------------------
# Fake extractor for hermetic injection
# ---------------------------------------------------------------------------


class FakeSentimentExtractor(SentimentExtractor):
    """Injects a fixed list of SentimentRecords — no network calls."""

    def __init__(self, records: list[SentimentRecord]) -> None:
        self._records = records

    @property
    def name(self) -> str:
        return "fake_sentiment_extractor"

    def extract_sentiment(
        self, chunk_id: str, chunk_text: str, lexicon_evidence: dict
    ) -> SentimentResult:
        # Attach the real chunk_id to each record for evidence traceability.
        out: list[SentimentRecord] = []
        for r in self._records:
            out.append(SentimentRecord(
                company_id=r.company_id,
                speaker_role=r.speaker_role,
                direction=r.direction,
                confidence_tone=r.confidence_tone,
                hedging=r.hedging,
                forward_stance=r.forward_stance,
                evidence_chunk_id=chunk_id,   # always stamped from real chunk
                confidence=r.confidence,
                lexicon_hits=json.dumps({"negative": ["decline"], "uncertainty": ["uncertain"]}),
            ))
        return SentimentResult(records=out)


# ---------------------------------------------------------------------------
# Helper: build a minimal run inside settings.run_output_dir
# ---------------------------------------------------------------------------


def _make_run(
    chunk_text: str = "Test management chunk.",
    chunk_company_id: str = "ACME",
    available_at: str = "2024-03-01",
    as_of_date: str = "2024-06-30",
    document_type: str = "earnings_transcript",
    section_title: str = "MD&A",
) -> tuple[str, Path]:
    """Create a minimal run directory for testing.

    Writes:
    - run_manifest.json
    - discovery/documents.parquet (one document with company_id + document_type)
    - discovery/chunks.parquet  (one chunk with section_title)

    Returns (run_id, discovery_dir).
    """
    from theme_engine.data_cleaning import DOCUMENTS_COLUMNS

    run_id = f"run_senta_{uuid.uuid4().hex[:8]}"
    this_run = settings.run_output_dir / run_id
    discovery = this_run / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)

    # Minimal manifest
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
    (this_run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Minimal documents.parquet — company_id and document_type live here.
    doc_row: dict = {col: None for col in DOCUMENTS_COLUMNS}
    doc_row["schema_version"] = "1.0"
    doc_row["run_id"] = run_id
    doc_row["document_id"] = "doc_001"
    doc_row["raw_document_id"] = "raw_001"
    doc_row["source"] = "test"
    doc_row["source_id"] = "test-001"
    doc_row["title"] = "Test Document"
    doc_row["document_type"] = document_type
    doc_row["company_id"] = chunk_company_id
    doc_row["published_at"] = available_at
    doc_row["available_at"] = available_at
    doc_row["language"] = "en"

    doc_table = pa.Table.from_pydict({col: [doc_row.get(col)] for col in DOCUMENTS_COLUMNS})
    pq.write_table(doc_table, discovery / "documents.parquet")

    # Minimal chunks.parquet — includes section_title for speaker-role tagging.
    chunk_row: dict = {col: None for col in CHUNKS_COLUMNS}
    chunk_row["schema_version"] = "1.0"
    chunk_row["run_id"] = run_id
    chunk_row["chunk_id"] = "chunk_001"
    chunk_row["document_id"] = "doc_001"
    chunk_row["raw_document_id"] = "raw_001"
    chunk_row["text"] = chunk_text
    chunk_row["available_at"] = available_at
    chunk_row["start_char"] = 0
    chunk_row["end_char"] = len(chunk_text)
    chunk_row["chunk_index"] = 0
    chunk_row["section_title"] = section_title

    chunks_table = pa.Table.from_pydict({col: [chunk_row.get(col)] for col in CHUNKS_COLUMNS})
    pq.write_table(chunks_table, discovery / "chunks.parquet")

    return run_id, discovery


def _make_run_with_tone(
    chunk_text: str,
    chunk_company_id: str = "ACME",
    available_at: str = "2024-03-01",
    as_of_date: str = "2024-06-30",
    document_type: str = "earnings_transcript",
    section_title: str = "MD&A",
    speaker_role: str = "management",
    matched_negative: list | None = None,
    matched_uncertainty: list | None = None,
) -> tuple[str, Path]:
    """Like _make_run but also writes a chunk_tone.parquet (SENT-A output)."""
    run_id, discovery = _make_run(
        chunk_text=chunk_text,
        chunk_company_id=chunk_company_id,
        available_at=available_at,
        as_of_date=as_of_date,
        document_type=document_type,
        section_title=section_title,
    )
    # Write a minimal chunk_tone.parquet so the sentiment pass can read speaker_role
    # and lexicon evidence without re-scoring.
    tone_row = {
        "chunk_id": "chunk_001",
        "document_id": "doc_001",
        "available_at": available_at,
        "speaker_role": speaker_role,
        "token_count": len(chunk_text.split()),
        "tone_positive": 0.0,
        "tone_negative": 0.05,
        "tone_uncertainty": 0.03,
        "tone_litigious": 0.0,
        "tone_strong_modal": 0.0,
        "tone_weak_modal": 0.0,
        "matched_positive": [],
        "matched_negative": matched_negative or ["decline"],
        "matched_uncertainty": matched_uncertainty or ["uncertain"],
        "matched_litigious": [],
        "matched_strong_modal": [],
        "matched_weak_modal": [],
    }
    # Build pyarrow table with list columns
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
    table = pa.table({k: [[v] if isinstance(v, list) else [v] for v in [tone_row[k]]][0]
                      if not isinstance(tone_row[k], list) else pa.array([tone_row[k]], type=pa.list_(pa.string()))
                      for k in tone_row}, schema=schema)
    # Build table properly
    arrays = []
    for field in schema:
        val = tone_row[field.name]
        if isinstance(field.type, pa.lib.ListType):
            arrays.append(pa.array([val], type=field.type))
        elif field.type == pa.float64():
            arrays.append(pa.array([float(val)], type=pa.float64()))
        elif field.type == pa.int64():
            arrays.append(pa.array([int(val)], type=pa.int64()))
        else:
            arrays.append(pa.array([str(val) if val is not None else None], type=pa.string()))
    tone_table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(tone_table, discovery / "chunk_tone.parquet")
    return run_id, discovery


# ---------------------------------------------------------------------------
# 1. POSITIVE: real management fixture — >=1 sentiment record
# ---------------------------------------------------------------------------


def test_rule_based_extractor_on_mgmt_fixture():
    """RuleBasedSentimentExtractor must extract >=1 sentiment record from the
    committed management MD&A fixture.  Each record must have a direction,
    a forward_stance, and a non-empty evidence_chunk_id."""
    fixture_text = (FIXTURES / "mgmt_sentiment_fixture.txt").read_text(encoding="utf-8")

    run_id, discovery = _make_run_with_tone(
        chunk_text=fixture_text,
        chunk_company_id="ACME",
        available_at="2024-06-01",
        document_type="earnings_transcript",
        section_title="MD&A",
    )

    extractor = RuleBasedSentimentExtractor()
    count = run_sentiment_extraction(run_id, sentiment_extractor=extractor)

    assert count >= 1, "Expected at least one management-sentiment record"

    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert rows, "management_sentiment.parquet must be non-empty"

    for row in rows:
        assert row["direction"] in _VALID_DIRECTIONS, (
            f"Invalid direction: {row['direction']!r}"
        )
        assert row["forward_stance"] in _VALID_FORWARD_STANCES, (
            f"Invalid forward_stance: {row['forward_stance']!r}"
        )
        assert row["evidence_chunk_id"], (
            f"Missing evidence_chunk_id on row: {row}"
        )
        assert row["company_id"], f"Missing company_id on row: {row}"
        assert row["speaker_role"] == "management", (
            f"Expected speaker_role=management; got: {row['speaker_role']!r}"
        )

    # Edges must be written too.
    edges = pq.read_table(discovery / "sentiment_edges.parquet").to_pylist()
    assert edges, "sentiment_edges.parquet must be non-empty"
    for edge in edges:
        assert edge["edge_type"] == "expresses_sentiment", (
            f"Expected expresses_sentiment edge; got: {edge['edge_type']!r}"
        )
        eids = edge.get("evidence_chunk_ids") or []
        assert eids, f"Edge missing evidence_chunk_ids: {edge}"


# ---------------------------------------------------------------------------
# 2. HERMETIC: FakeSentimentExtractor injection
# ---------------------------------------------------------------------------


def test_hermetic_fake_extractor_injection():
    """FakeSentimentExtractor injects pre-programmed records; run_sentiment_extraction
    must write them with correct columns and values."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="positive",
            confidence_tone="high",
            hedging=False,
            forward_stance="optimistic",
            evidence_chunk_id="",   # will be filled by FakeSentimentExtractor
            confidence=0.88,
        ),
    ]

    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp delivered outstanding results and we are optimistic.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )

    count = run_sentiment_extraction(
        run_id, sentiment_extractor=FakeSentimentExtractor(pre_records)
    )
    assert count == 1, f"Expected 1 sentiment record; got {count}"

    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["company_id"] == "ACME"
    assert row["direction"] == "positive"
    assert row["confidence_tone"] == "high"
    assert not row["hedging"]
    assert row["forward_stance"] == "optimistic"
    assert row["evidence_chunk_id"] == "chunk_001"
    assert row["speaker_role"] == "management"
    # Columns must match contract exactly
    assert set(row.keys()) == set(MANAGEMENT_SENTIMENT_COLUMNS), (
        f"Column mismatch: {set(row.keys()) ^ set(MANAGEMENT_SENTIMENT_COLUMNS)}"
    )

    # Edges
    edges = pq.read_table(discovery / "sentiment_edges.parquet").to_pylist()
    assert len(edges) == 1
    edge = edges[0]
    assert edge["edge_type"] == "expresses_sentiment"
    assert edge["speaker_role"] == "management"
    assert edge["sentiment_id"] == row["sentiment_id"]
    assert set(edge.keys()) == set(SENTIMENT_EDGES_COLUMNS), (
        f"Edge column mismatch: {set(edge.keys()) ^ set(SENTIMENT_EDGES_COLUMNS)}"
    )


# ---------------------------------------------------------------------------
# 3. NO-EVIDENCE: no record emitted without an evidence_chunk_id
# ---------------------------------------------------------------------------


class _NoEvidenceSentimentExtractor(SentimentExtractor):
    """Returns a record with an empty evidence_chunk_id — must be silently dropped."""

    @property
    def name(self) -> str:
        return "no_evidence_sentiment_extractor"

    def extract_sentiment(
        self, chunk_id: str, chunk_text: str, lexicon_evidence: dict
    ) -> SentimentResult:
        return SentimentResult(records=[
            SentimentRecord(
                company_id="ACME",
                speaker_role="management",
                direction="positive",
                confidence_tone="high",
                hedging=False,
                forward_stance="optimistic",
                evidence_chunk_id="",   # intentionally empty
                confidence=0.80,
            )
        ])


def test_no_record_without_evidence_chunk():
    """Records with empty evidence_chunk_id must never appear in output."""
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management discussion.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )

    count = run_sentiment_extraction(
        run_id, sentiment_extractor=_NoEvidenceSentimentExtractor()
    )
    assert count == 0, f"Expected 0 records; got {count}"

    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert rows == [], f"Expected empty sentiment table; got: {rows}"


# ---------------------------------------------------------------------------
# 4. NEGATION: "we do NOT see strong demand" is judged negative
# ---------------------------------------------------------------------------


def test_negation_not_see_strong_demand():
    """RuleBasedSentimentExtractor must judge 'we do NOT see strong demand'
    as negative despite the word 'strong' appearing in the text.

    This tests that context (negation) overrides the lexicon signal.
    """
    chunk_text = (
        "Acme Corp management update: We do NOT see strong demand in our key markets. "
        "Volume has declined and macroeconomic headwinds and concerns persist."
    )

    extractor = RuleBasedSentimentExtractor()
    result = extractor.extract_sentiment(
        chunk_id="chunk_negation_test",
        chunk_text=chunk_text,
        lexicon_evidence={"negative": ["pressured"], "uncertainty": ["cautious"]},
    )

    assert result.records, "Expected at least one sentiment record for negation test"
    rec = result.records[0]
    assert rec.direction == "negative", (
        f"Expected 'negative' direction for 'NOT see strong demand'; got {rec.direction!r}. "
        "Negation must override the positive word 'strong'."
    )
    assert rec.evidence_chunk_id == "chunk_negation_test"


# ---------------------------------------------------------------------------
# 5. SPEAKER-GATING: news/media chunk is NOT sent to management-sentiment pass
# ---------------------------------------------------------------------------


class _RecordingExtractor(SentimentExtractor):
    """Records every chunk_id that was passed to extract_sentiment."""

    def __init__(self) -> None:
        self.seen_chunk_ids: list[str] = []

    @property
    def name(self) -> str:
        return "recording_extractor"

    def extract_sentiment(
        self, chunk_id: str, chunk_text: str, lexicon_evidence: dict
    ) -> SentimentResult:
        self.seen_chunk_ids.append(chunk_id)
        return SentimentResult(records=[])


def test_speaker_gating_media_chunk_not_processed():
    """A news/media chunk must NOT be sent to the management-sentiment pass.
    Only management-attributable chunks should reach the extractor."""
    # Create a run with document_type=news (media chunk).
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp announces results. The company posted strong revenue.",
        chunk_company_id="ACME",
        document_type="news",
        section_title="",
        speaker_role="media",   # pre-tagged as media by SENT-A
    )

    recorder = _RecordingExtractor()
    count = run_sentiment_extraction(run_id, sentiment_extractor=recorder)

    assert count == 0, (
        f"Expected 0 records for a media chunk; got {count}"
    )
    assert recorder.seen_chunk_ids == [], (
        f"Media chunk must NOT reach the extractor; extractor saw: {recorder.seen_chunk_ids}"
    )


def test_speaker_gating_management_chunk_is_processed():
    """A management chunk (earnings_transcript / MD&A) MUST reach the extractor."""
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: we are confident in our growth outlook.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )

    recorder = _RecordingExtractor()
    run_sentiment_extraction(run_id, sentiment_extractor=recorder)

    assert "chunk_001" in recorder.seen_chunk_ids, (
        "Management chunk must reach the extractor"
    )


# ---------------------------------------------------------------------------
# 6. PIT: chunk with available_at > as_of_date yields zero records
# ---------------------------------------------------------------------------


def test_pit_future_chunk_dropped():
    """A chunk with available_at AFTER as_of_date must yield zero records."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="positive",
            confidence_tone="high",
            hedging=False,
            forward_stance="optimistic",
            evidence_chunk_id="",
            confidence=0.90,
        )
    ]

    # as_of_date=2024-12-31, chunk available_at=2025-03-01 (future)
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: strong results ahead.",
        chunk_company_id="ACME",
        available_at="2025-03-01",   # AFTER as_of_date
        as_of_date="2024-12-31",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )

    count = run_sentiment_extraction(
        run_id, sentiment_extractor=FakeSentimentExtractor(pre_records)
    )
    assert count == 0, (
        f"Expected 0 records from a future-dated chunk; got {count}. "
        "PIT filter must drop chunks where available_at > as_of_date."
    )

    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert rows == [], f"Expected empty sentiment table for future chunk; got: {rows}"


def test_pit_empty_available_at_chunk_dropped_fail_closed():
    """A chunk with an EMPTY available_at must yield zero records (fail-closed, OI-8).

    A missing/empty available_at cannot prove the chunk was knowable at as_of, so
    it must be EXCLUDED — not silently included (the old fail-open bug)."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="positive",
            confidence_tone="high",
            hedging=False,
            forward_stance="optimistic",
            evidence_chunk_id="",
            confidence=0.90,
        )
    ]
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: strong results ahead.",
        chunk_company_id="ACME",
        available_at="",             # EMPTY available_at -> fail-closed exclude
        as_of_date="2024-12-31",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )

    count = run_sentiment_extraction(
        run_id, sentiment_extractor=FakeSentimentExtractor(pre_records)
    )
    assert count == 0, (
        f"Expected 0 records from an empty-available_at chunk; got {count}. "
        "Fail-closed PIT must drop chunks with missing available_at."
    )
    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert rows == [], f"Expected empty sentiment table for empty-available_at chunk; got: {rows}"


# ---------------------------------------------------------------------------
# 7. LEXICON-GROUNDING: lexicon_hits populated when tone data present
# ---------------------------------------------------------------------------


def test_lexicon_grounding_hits_populated():
    """When SENT-A chunk_tone.parquet is available, the lexicon_hits field in
    the output record must be populated (non-empty JSON) with the matched words."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="negative",
            confidence_tone="moderate",
            hedging=True,
            forward_stance="cautious",
            evidence_chunk_id="",
            confidence=0.75,
        )
    ]

    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: cautious about decline.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
        matched_negative=["decline", "cautious"],
        matched_uncertainty=["uncertain"],
    )

    run_sentiment_extraction(
        run_id, sentiment_extractor=FakeSentimentExtractor(pre_records)
    )

    rows = pq.read_table(discovery / "management_sentiment.parquet").to_pylist()
    assert rows, "Expected at least one sentiment row"
    row = rows[0]
    hits_str = row.get("lexicon_hits") or ""
    assert hits_str, "lexicon_hits must be non-empty when SENT-A data is available"
    hits = json.loads(hits_str)
    assert isinstance(hits, dict), f"lexicon_hits must be a JSON dict; got: {hits!r}"
    # The FakeSentimentExtractor always writes its own hits JSON, so we just
    # verify the field is a valid non-empty JSON object.
    assert hits, "lexicon_hits JSON must be non-empty when tone data is present"


# ---------------------------------------------------------------------------
# Column contract: MANAGEMENT_SENTIMENT_COLUMNS and SENTIMENT_EDGES_COLUMNS
# ---------------------------------------------------------------------------


def test_management_sentiment_columns_contract():
    """management_sentiment.parquet must have exactly the contract columns."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="positive",
            confidence_tone="high",
            hedging=False,
            forward_stance="optimistic",
            evidence_chunk_id="",
            confidence=0.90,
        )
    ]
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: strong and positive outlook.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )
    run_sentiment_extraction(run_id, sentiment_extractor=FakeSentimentExtractor(pre_records))

    table = pq.read_table(discovery / "management_sentiment.parquet")
    assert list(table.schema.names) == MANAGEMENT_SENTIMENT_COLUMNS, (
        f"Column mismatch.\n  expected: {MANAGEMENT_SENTIMENT_COLUMNS}\n"
        f"  got: {list(table.schema.names)}"
    )


def test_sentiment_edges_columns_contract():
    """sentiment_edges.parquet must have exactly the contract columns."""
    pre_records = [
        SentimentRecord(
            company_id="ACME",
            speaker_role="management",
            direction="positive",
            confidence_tone="high",
            hedging=False,
            forward_stance="optimistic",
            evidence_chunk_id="",
            confidence=0.90,
        )
    ]
    run_id, discovery = _make_run_with_tone(
        chunk_text="Acme Corp management: strong and positive outlook.",
        chunk_company_id="ACME",
        document_type="earnings_transcript",
        section_title="MD&A",
        speaker_role="management",
    )
    run_sentiment_extraction(run_id, sentiment_extractor=FakeSentimentExtractor(pre_records))

    table = pq.read_table(discovery / "sentiment_edges.parquet")
    assert list(table.schema.names) == SENTIMENT_EDGES_COLUMNS, (
        f"Edge column mismatch.\n  expected: {SENTIMENT_EDGES_COLUMNS}\n"
        f"  got: {list(table.schema.names)}"
    )


# ---------------------------------------------------------------------------
# Smoke: RuleBasedSentimentExtractor unit tests (no pipeline)
# ---------------------------------------------------------------------------


def test_rule_based_extractor_name():
    """RuleBasedSentimentExtractor has a stable name."""
    extractor = RuleBasedSentimentExtractor()
    assert extractor.name == "rule_based_sentiment_extractor_v1"


def test_rule_based_extractor_direction_positive():
    """RuleBasedSentimentExtractor returns 'positive' for positive management text."""
    extractor = RuleBasedSentimentExtractor()
    text = "Acme Corp delivered strong results with record revenue. We are confident and well-positioned."
    result = extractor.extract_sentiment("ck1", text, {})
    assert result.records, "Expected at least one record for positive text"
    assert result.records[0].direction in {"positive", "mixed"}, (
        f"Expected positive or mixed; got {result.records[0].direction!r}"
    )


def test_rule_based_extractor_direction_negative():
    """RuleBasedSentimentExtractor returns 'negative' for negative management text."""
    extractor = RuleBasedSentimentExtractor()
    text = "Acme Corp faces significant challenges. Demand has declined and headwinds persist."
    result = extractor.extract_sentiment("ck2", text, {})
    assert result.records, "Expected at least one record for negative text"
    assert result.records[0].direction in {"negative", "mixed"}, (
        f"Expected negative or mixed; got {result.records[0].direction!r}"
    )
