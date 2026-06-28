"""Tests for EG-B2: LLM quantified-fact extraction (GitHub #92).

Four acceptance criteria, all hermetic (no network):

1. POSITIVE — on the committed fixture (acme_financials.txt), >=1 guidance claim
   and >=1 reported-actual claim are extracted; each has a correct value, unit,
   period, and a non-empty evidence_chunk_id.

2. HERMETIC — a FakeFactExtractor (injected) returns pre-programmed claims;
   run_fact_extraction emits them correctly.

3. NEGATIVE — a claim without an evidence_chunk_id must NOT appear in output.

4. RECONCILIATION — an XBRL (B1) fixture row is preferred over a duplicate
   LLM as-reported claim: when B1 covers (company_id, period, metric_name),
   the LLM as-reported claim is dropped; guidance claims survive regardless.
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
# Make backend importable (same pattern as conftest.py)
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
    QuantifiedClaim,
    QuantifiedClaimResult,
    FactExtractor,
    RuleBasedFactExtractor,
    run_fact_extraction,
    FINANCIAL_METRICS_COLUMNS,
    FINANCIAL_METRIC_EDGES_COLUMNS,
    _B1_ARTIFACT_NAME,
    load_metric_vocabulary,
)
from theme_engine.config import settings
from theme_engine.chunking import CHUNKS_COLUMNS

# ---------------------------------------------------------------------------
# Fake extractor for hermetic injection
# ---------------------------------------------------------------------------


class FakeFactExtractor(FactExtractor):
    """Injects a fixed list of QuantifiedClaims — no network calls."""

    def __init__(self, claims: list[QuantifiedClaim]) -> None:
        self._claims = claims

    @property
    def name(self) -> str:
        return "fake_fact_extractor"

    def extract_facts(self, chunk_id: str, chunk_text: str) -> QuantifiedClaimResult:
        # Attach the real chunk_id to each claim so evidence is traceable.
        out: list[QuantifiedClaim] = []
        for c in self._claims:
            out.append(QuantifiedClaim(
                company_id=c.company_id,
                metric_name=c.metric_name,
                value=c.value,
                unit=c.unit,
                period=c.period,
                direction=c.direction,
                is_guidance=c.is_guidance,
                evidence_chunk_id=chunk_id,  # always stamped from real chunk
                confidence=c.confidence,
                source=self.name,
            ))
        return QuantifiedClaimResult(claims=out)


# ---------------------------------------------------------------------------
# Helpers: build a minimal run inside settings.run_output_dir
# ---------------------------------------------------------------------------

def _make_run(
    chunk_text: str = "Test chunk.",
    chunk_company_id: str = "ACME",
    available_at: str = "2024-03-01",
    as_of_date: str = "2024-06-30",
) -> tuple[str, Path]:
    """Create a minimal run directory inside settings.run_output_dir.

    Writes:
    - run_manifest.json
    - discovery/chunks.parquet  (one chunk)
    - discovery/documents.parquet (one document, carrying company_id)

    Returns (run_id, discovery_dir).
    """
    from theme_engine.data_cleaning import DOCUMENTS_COLUMNS

    run_id = f"run_test_{uuid.uuid4().hex[:8]}"
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

    # Minimal documents.parquet — company_id lives here.
    doc_row: dict = {col: None for col in DOCUMENTS_COLUMNS}
    doc_row["schema_version"] = "1.0"
    doc_row["run_id"] = run_id
    doc_row["document_id"] = "doc_001"
    doc_row["raw_document_id"] = "raw_001"
    doc_row["source"] = "test"
    doc_row["source_id"] = "test-001"
    doc_row["title"] = "Test Document"
    doc_row["document_type"] = "transcript"
    doc_row["company_id"] = chunk_company_id
    doc_row["published_at"] = available_at
    doc_row["available_at"] = available_at
    doc_row["language"] = "en"

    doc_table = pa.Table.from_pydict({col: [doc_row.get(col)] for col in DOCUMENTS_COLUMNS})
    pq.write_table(doc_table, discovery / "documents.parquet")

    # Minimal chunks.parquet — no company_id column (it's in documents).
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

    chunks_table = pa.Table.from_pydict({col: [chunk_row.get(col)] for col in CHUNKS_COLUMNS})
    pq.write_table(chunks_table, discovery / "chunks.parquet")

    return run_id, discovery


def _write_b1_fixture(
    discovery_dir: Path,
    company_id: str,
    period_end: str,
    metric_name: str,
    metric_value: float = 3.5,
) -> None:
    """Write a minimal B1 fundamentals artifact with one row."""
    row: dict = {
        "company_id": company_id,
        "period_end": period_end,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "unit": "USD_billions",
        "currency": "USD",
        "filing_date": "2024-04-15",
        "available_at": "2024-04-15",
        "source": "xbrl",
        "source_id": "xbrl-001",
    }
    table = pa.Table.from_pydict({k: [v] for k, v in row.items()})
    pq.write_table(table, discovery_dir / _B1_ARTIFACT_NAME)


# ---------------------------------------------------------------------------
# 1. POSITIVE: real fixture corpus — >=1 guidance and >=1 actuals claim
# ---------------------------------------------------------------------------


def test_rule_based_extractor_on_fixture_corpus():
    """RuleBasedFactExtractor must extract >=1 guidance and >=1 actual claim
    from acme_financials.txt with correct fields."""
    fixture_text = (FIXTURES / "acme_financials.txt").read_text(encoding="utf-8")

    run_id, discovery = _make_run(
        chunk_text=fixture_text,
        chunk_company_id="ACME",
        available_at="2024-06-01",
    )

    extractor = RuleBasedFactExtractor()
    count = run_fact_extraction(run_id, fact_extractor=extractor)

    assert count >= 1, "Expected at least one FinancialMetric row"

    rows = pq.read_table(discovery / "financial_metrics.parquet").to_pylist()
    assert rows, "financial_metrics.parquet must be non-empty"

    guidance_rows = [r for r in rows if r["is_guidance"]]
    actual_rows = [r for r in rows if not r["is_guidance"]]

    assert guidance_rows, (
        "Expected >=1 guidance claim; got rows: "
        + str([(r["metric_name"], r["is_guidance"]) for r in rows])
    )
    assert actual_rows, (
        "Expected >=1 as-reported claim; got rows: "
        + str([(r["metric_name"], r["is_guidance"]) for r in rows])
    )

    # Every row must have a non-empty evidence_chunk_id.
    for row in rows:
        assert row["evidence_chunk_id"], f"Missing evidence_chunk_id on row: {row}"
        assert isinstance(row["value"], (float, int)), (
            f"Expected numeric value; got: {row['value']!r}"
        )
        assert row["unit"], f"Missing unit on row: {row}"

    # Guidance claim must have a concrete positive value, unit, and period.
    g = guidance_rows[0]
    assert g["value"] > 0, f"Guidance value must be positive; got: {g['value']}"
    assert g["unit"], "Guidance unit must be non-empty"

    # Edges must be written too.
    edges = pq.read_table(discovery / "financial_metric_edges.parquet").to_pylist()
    assert edges, "financial_metric_edges.parquet must be non-empty"
    edge_types = {e["edge_type"] for e in edges}
    assert edge_types & {"guides_to", "reports"}, (
        f"Expected 'guides_to' or 'reports' edge; got: {edge_types}"
    )

    # Every edge must carry at least one evidence_chunk_id.
    for edge in edges:
        eids = edge.get("evidence_chunk_ids") or []
        assert eids, f"Edge missing evidence_chunk_ids: {edge}"


# ---------------------------------------------------------------------------
# 2. HERMETIC: FakeFactExtractor injection
# ---------------------------------------------------------------------------


def test_hermetic_fake_extractor_injection():
    """FakeFactExtractor injects pre-programmed claims; run_fact_extraction
    must write them to parquet with the correct fields."""
    pre_claims = [
        QuantifiedClaim(
            company_id="BETA",
            metric_name="revenue",
            value=1.5,
            unit="USD_billions",
            period="Q1 2024",
            direction="rose",
            is_guidance=False,
            evidence_chunk_id="",  # will be filled by FakeFactExtractor
            confidence=0.90,
        ),
        QuantifiedClaim(
            company_id="BETA",
            metric_name="eps",
            value=2.35,
            unit="USD_per_share",
            period="FY 2024",
            direction="",
            is_guidance=True,
            evidence_chunk_id="",
            confidence=0.85,
        ),
    ]

    run_id, discovery = _make_run(
        chunk_text="Beta Industries Q1 revenue rose to $1.5 billion.",
        chunk_company_id="BETA",
    )

    count = run_fact_extraction(run_id, fact_extractor=FakeFactExtractor(pre_claims))
    assert count == 2, f"Expected 2 metrics; got {count}"

    rows = pq.read_table(discovery / "financial_metrics.parquet").to_pylist()
    assert len(rows) == 2

    rev = next(r for r in rows if r["metric_name"] == "revenue")
    assert rev["value"] == 1.5
    assert rev["unit"] == "USD_billions"
    assert rev["period"] == "Q1 2024"
    assert not rev["is_guidance"]
    assert rev["evidence_chunk_id"] == "chunk_001"

    eps = next(r for r in rows if r["metric_name"] == "eps")
    assert eps["value"] == 2.35
    assert eps["is_guidance"]
    assert eps["evidence_chunk_id"] == "chunk_001"

    # Edge types must match guidance flag.
    edges = pq.read_table(discovery / "financial_metric_edges.parquet").to_pylist()
    edge_map = {e["metric_id"]: e["edge_type"] for e in edges}
    assert edge_map[rev["metric_id"]] == "reports"
    assert edge_map[eps["metric_id"]] == "guides_to"


# ---------------------------------------------------------------------------
# 3. NEGATIVE: no claim emitted without an evidence_chunk_id
# ---------------------------------------------------------------------------


class _NoEvidenceFactExtractor(FactExtractor):
    """Returns a claim with an empty evidence_chunk_id — must be silently dropped."""

    @property
    def name(self) -> str:
        return "no_evidence_extractor"

    def extract_facts(self, chunk_id: str, chunk_text: str) -> QuantifiedClaimResult:
        return QuantifiedClaimResult(claims=[
            QuantifiedClaim(
                company_id="ACME",
                metric_name="revenue",
                value=5.0,
                unit="USD_billions",
                period="Q2 2024",
                direction="",
                is_guidance=False,
                evidence_chunk_id="",  # intentionally empty
                confidence=0.80,
            )
        ])


def test_no_claim_without_evidence_chunk():
    """Claims with empty evidence_chunk_id must never appear in output."""
    run_id, discovery = _make_run(
        chunk_text="Acme Corp revenue was $5 billion."
    )
    count = run_fact_extraction(run_id, fact_extractor=_NoEvidenceFactExtractor())
    assert count == 0, "Expected 0 claims; got claims without evidence"

    rows = pq.read_table(discovery / "financial_metrics.parquet").to_pylist()
    assert rows == [], f"Expected empty metrics table; got: {rows}"


# ---------------------------------------------------------------------------
# 4. RECONCILIATION: XBRL (B1) wins over LLM as-reported; guidance survives
# ---------------------------------------------------------------------------


def test_reconciliation_xbrl_wins_for_as_reported():
    """When B1 XBRL covers (company_id, period, metric_name), the LLM as-reported
    claim must be dropped; a guidance claim on the same company+metric must survive."""
    both_claims = [
        QuantifiedClaim(
            company_id="ACME",
            metric_name="revenue",
            value=3.0,       # different from XBRL's 3.5 — LLM should lose
            unit="USD_billions",
            period="Q2 2024",   # matches B1 period_end key
            direction="rose",
            is_guidance=False,
            evidence_chunk_id="",
            confidence=0.85,
        ),
        QuantifiedClaim(
            company_id="ACME",
            metric_name="revenue",
            value=13.0,
            unit="USD_billions",
            period="FY 2024",   # guidance — different period, is_guidance=True
            direction="",
            is_guidance=True,
            evidence_chunk_id="",
            confidence=0.80,
        ),
    ]

    run_id, discovery = _make_run(
        chunk_text="Acme Corp revenue rose to $3.0 billion in Q2 2024.",
        chunk_company_id="ACME",
    )
    # Write B1 XBRL row for ("ACME", "Q2 2024", "revenue")
    _write_b1_fixture(discovery, "ACME", "Q2 2024", "revenue")

    run_fact_extraction(run_id, fact_extractor=FakeFactExtractor(both_claims))

    rows = pq.read_table(discovery / "financial_metrics.parquet").to_pylist()

    # LLM as-reported revenue for Q2 2024 must be absent (XBRL wins).
    actual_revenue = [
        r for r in rows if r["metric_name"] == "revenue" and not r["is_guidance"]
    ]
    assert actual_revenue == [], (
        "LLM as-reported revenue should be suppressed by XBRL; got: "
        + str(actual_revenue)
    )

    # Guidance claim (FY 2024) must survive.
    guidance_revenue = [
        r for r in rows if r["metric_name"] == "revenue" and r["is_guidance"]
    ]
    assert guidance_revenue, "Guidance revenue claim must survive reconciliation"
    assert guidance_revenue[0]["value"] == 13.0


# ---------------------------------------------------------------------------
# Smoke: metric vocabulary loaded correctly
# ---------------------------------------------------------------------------


def test_metric_vocabulary_contains_required_names():
    """The metric vocabulary must contain all shared-contract metric_names."""
    vocab = load_metric_vocabulary()
    required = {"revenue", "net_income", "eps", "gross_margin", "operating_margin",
                "ebitda_margin", "operating_cash_flow", "total_debt"}
    missing = required - vocab
    assert not missing, f"Missing metric_names in vocabulary: {missing}"
