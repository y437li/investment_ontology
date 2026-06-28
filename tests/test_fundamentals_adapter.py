"""Tests for the XBRL as-reported fundamentals adapter (EG-B1).

Acceptance criteria verified here:
 1. Hermetic: all tests use committed fixture files; no network calls occur.
 2. >=5 metrics across >=2 periods for the AAPL fixture company.
 3. available_at = filing_date (PIT rule).
 4. Empty-but-schema-valid artifact when a company has no XBRL.
 5. Leakage gate: the adapter never reads io_contracts §20
    validation/fundamentals.parquet.
 6. Metric names come from configs/fundamentals.yml (none hardcoded).
 7. Deduplication: (company_id, period_end, metric_name) is unique in output.
 8. Margin computation: gross_margin and operating_margin are derived.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from theme_engine import fundamentals_adapter as fa  # noqa: E402
from theme_engine.config import settings  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "xbrl"
AAPL_FACTS = FIXTURES / "aapl_facts.json"
CONFIG_PATH = REPO_ROOT / "configs" / "fundamentals.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(tmp_path: Path) -> tuple[str, Path]:
    """Create a minimal run directory with a run_manifest.json.

    The run is created inside ``settings.run_output_dir`` (as set by conftest.py
    via ``RUN_OUTPUT_DIR`` before theme_engine is imported), so
    ``runs.get_run_dir()`` resolves correctly. ``tmp_path`` provides a unique
    suffix for the run_id.

    Returns ``(run_id, run_dir)``.
    """
    import json as _json

    run_id = f"run_test_fundamentals_{uuid.uuid4().hex[:8]}"
    run_dir = settings.run_output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "discovery").mkdir(exist_ok=True)

    manifest = {
        "run_id": run_id,
        "as_of_date": "2024-06-30",
        "created_at": "2024-06-30T00:00:00Z",
        "discovery_frozen": False,
    }
    (run_dir / "run_manifest.json").write_text(
        _json.dumps(manifest), encoding="utf-8"
    )
    return run_id, run_dir


# ---------------------------------------------------------------------------
# 1. Hermetic fixture parsing
# ---------------------------------------------------------------------------

class TestHermeticXBRLParsing:
    """All tests parse the committed AAPL fixture; no network occurs."""

    def test_fixture_file_exists(self) -> None:
        assert AAPL_FACTS.exists(), f"XBRL fixture missing: {AAPL_FACTS}"

    def test_config_file_exists(self) -> None:
        assert CONFIG_PATH.exists(), f"fundamentals config missing: {CONFIG_PATH}"

    def test_parse_returns_rows(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        metric_idx = fa._metric_index(cfg)
        derived = fa._derived_index(cfg)
        facts = json.loads(AAPL_FACTS.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(facts, "AAPL", None, metric_idx, derived)
        assert len(rows) > 0, "Expected at least one row from AAPL fixture"

    def test_at_least_five_metrics(self) -> None:
        """Acceptance: >=5 distinct metric_names from the fixture."""
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        metrics = {r["metric_name"] for r in rows}
        assert len(metrics) >= 5, (
            f"Expected >=5 distinct metrics; got {sorted(metrics)}"
        )

    def test_at_least_two_periods(self) -> None:
        """Acceptance: >=2 distinct period_end values from the fixture."""
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        periods = {r["period_end"] for r in rows}
        assert len(periods) >= 2, (
            f"Expected >=2 distinct period_end values; got {sorted(periods)}"
        )


# ---------------------------------------------------------------------------
# 2. PIT discipline: available_at = filing_date
# ---------------------------------------------------------------------------

class TestPITDiscipline:
    def test_available_at_equals_filing_date(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        for row in rows:
            assert row["available_at"] == row["filing_date"], (
                f"PIT violation: available_at={row['available_at']} "
                f"!= filing_date={row['filing_date']} for {row['metric_name']}"
            )

    def test_available_at_is_not_period_end(self) -> None:
        """available_at must be the filing date, never the period end."""
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        for row in rows:
            # period_end and filing_date are different; available_at = filing_date
            if row["period_end"] != row["filing_date"]:
                assert row["available_at"] != row["period_end"], (
                    f"available_at should be filing_date, not period_end: {row}"
                )

    def test_pit_filter_excludes_future_filings(self) -> None:
        """Rows with filing_date > as_of must be excluded."""
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        # Use as_of before any AAPL filing date in the fixture
        as_of = date(2022, 10, 1)
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", as_of,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        for row in rows:
            assert row["available_at"] <= as_of.strftime("%Y-%m-%d"), (
                f"Leakage: row available_at {row['available_at']} > as_of {as_of}"
            )


# ---------------------------------------------------------------------------
# 3. Units and currency
# ---------------------------------------------------------------------------

class TestUnitsAndCurrency:
    def test_usd_metrics_have_currency(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        usd_metrics = {"revenue", "net_income", "operating_cash_flow", "total_debt"}
        for row in rows:
            if row["metric_name"] in usd_metrics:
                assert row["currency"] is not None and "USD" in row["currency"], (
                    f"Expected USD currency for {row['metric_name']}: {row}"
                )

    def test_ratio_metrics_have_no_currency(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        ratio_metrics = {"gross_margin", "operating_margin", "ebitda_margin"}
        for row in rows:
            if row["metric_name"] in ratio_metrics:
                assert row["currency"] is None, (
                    f"Margin should have no currency: {row}"
                )
                assert row["unit"] == "ratio", (
                    f"Margin unit should be 'ratio': {row}"
                )


# ---------------------------------------------------------------------------
# 4. Margin computation
# ---------------------------------------------------------------------------

class TestMarginComputation:
    def test_gross_margin_derived(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        gm_rows = [r for r in rows if r["metric_name"] == "gross_margin"]
        assert gm_rows, "Expected gross_margin rows from derived computation"
        for r in gm_rows:
            # GrossProfit / Revenues for AAPL FY2023: 169148M / 383285M ≈ 0.44
            assert 0.0 < r["metric_value"] < 1.0, (
                f"gross_margin out of range: {r['metric_value']}"
            )

    def test_operating_margin_derived(self) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        om_rows = [r for r in rows if r["metric_name"] == "operating_margin"]
        assert om_rows, "Expected operating_margin rows from derived computation"
        for r in om_rows:
            assert 0.0 < r["metric_value"] < 1.0, (
                f"operating_margin out of range: {r['metric_value']}"
            )

    def test_gross_margin_value_approx(self) -> None:
        """AAPL FY2023 gross margin: 169148M / 383285M ≈ 0.441."""
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = fa._parse_company_facts(
            json.loads(AAPL_FACTS.read_text(encoding="utf-8")),
            "AAPL", None,
            fa._metric_index(cfg), fa._derived_index(cfg),
        )
        fy23_gm = [
            r for r in rows
            if r["metric_name"] == "gross_margin" and r["period_end"] == "2023-09-30"
        ]
        assert fy23_gm, "No FY2023 gross_margin row found"
        expected = 169148000000 / 383285000000
        assert abs(fy23_gm[0]["metric_value"] - expected) < 0.001, (
            f"gross_margin value {fy23_gm[0]['metric_value']} differs from expected {expected:.4f}"
        )


# ---------------------------------------------------------------------------
# 5. Empty-but-schema-valid artifact
# ---------------------------------------------------------------------------

class TestEmptyArtifact:
    def test_empty_table_has_correct_schema(self) -> None:
        table = fa._empty_table()
        assert set(table.schema.names) == set(fa.FUNDAMENTALS_COLUMNS), (
            f"Empty table schema mismatch: {table.schema.names}"
        )
        assert len(table) == 0

    def test_ingest_no_xbrl_writes_schema_valid_artifact(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        result = fa.ingest_xbrl(
            run_id=run_id,
            company_id="NODATA",
            facts_json_path=None,
            config_path=CONFIG_PATH,
        )
        assert result["rows_written"] == 0

        artifact_path = run_dir / "discovery" / fa.FUNDAMENTALS_ARTIFACT
        assert artifact_path.exists(), "Artifact not written for company with no XBRL"

        table = pq.read_table(artifact_path)
        assert set(table.schema.names) == set(fa.FUNDAMENTALS_COLUMNS), (
            "Empty artifact missing required columns"
        )
        assert len(table) == 0

    def test_ingest_missing_facts_file_writes_schema_valid_artifact(
        self, tmp_path: Path
    ) -> None:
        run_id, run_dir = _make_run(tmp_path)
        missing_path = tmp_path / "nonexistent_facts.json"
        result = fa.ingest_xbrl(
            run_id=run_id,
            company_id="GHOST",
            facts_json_path=missing_path,
            config_path=CONFIG_PATH,
        )
        assert result["rows_written"] == 0

        artifact_path = run_dir / "discovery" / fa.FUNDAMENTALS_ARTIFACT
        assert artifact_path.exists()
        table = pq.read_table(artifact_path)
        assert set(table.schema.names) == set(fa.FUNDAMENTALS_COLUMNS)
        assert len(table) == 0


# ---------------------------------------------------------------------------
# 6. Full end-to-end ingest via ingest_xbrl()
# ---------------------------------------------------------------------------

class TestIngestXBRL:
    def test_ingest_aapl_five_metrics_two_periods(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        result = fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        assert len(result["metrics_found"]) >= 5, (
            f"Expected >=5 metrics; got {result['metrics_found']}"
        )
        assert len(result["periods_found"]) >= 2, (
            f"Expected >=2 periods; got {result['periods_found']}"
        )
        assert result["rows_written"] >= 5

    def test_artifact_written_to_discovery(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        artifact_path = run_dir / "discovery" / fa.FUNDAMENTALS_ARTIFACT
        assert artifact_path.exists(), "fundamentals_asreported.parquet not written"

        table = pq.read_table(artifact_path)
        assert set(table.schema.names) == set(fa.FUNDAMENTALS_COLUMNS)
        assert len(table) >= 5

    def test_source_is_edgar_xbrl(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        rows = fa.read_fundamentals(run_id)
        for row in rows:
            assert row["source"] == "edgar_xbrl", (
                f"Expected source='edgar_xbrl'; got {row['source']!r}"
            )

    def test_idempotent_reingest(self, tmp_path: Path) -> None:
        """Running ingest_xbrl twice for the same company produces no duplicates."""
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        count_first = len(fa.read_fundamentals(run_id))

        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        count_second = len(fa.read_fundamentals(run_id))
        assert count_first == count_second, (
            f"Duplicate rows after re-ingest: {count_first} -> {count_second}"
        )


# ---------------------------------------------------------------------------
# 7. Deduplication: (company_id, period_end, metric_name) is unique
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicate_keys(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        rows = fa.read_fundamentals(run_id)
        keys = [(r["company_id"], r["period_end"], r["metric_name"]) for r in rows]
        assert len(keys) == len(set(keys)), (
            f"Duplicate reconciliation keys found in output"
        )


# ---------------------------------------------------------------------------
# 8. Metric names from config (no hardcoding)
# ---------------------------------------------------------------------------

class TestMetricNameConfig:
    def test_metric_names_in_config_whitelist(self, tmp_path: Path) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        allowed = {m["metric_name"] for m in cfg.get("metrics", [])}

        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        rows = fa.read_fundamentals(run_id)
        for row in rows:
            assert row["metric_name"] in allowed, (
                f"metric_name {row['metric_name']!r} not in config whitelist {allowed}"
            )


# ---------------------------------------------------------------------------
# 9. Leakage gate: adapter never reads validation/fundamentals.parquet
# ---------------------------------------------------------------------------

class TestLeakageGate:
    def test_adapter_does_not_open_validation_fundamentals_path(self) -> None:
        """The fundamentals_adapter source code must not open the §20 validation
        fundamentals path (discovery code may not read validation-only artifacts)."""
        src = Path(fa.__file__).read_text(encoding="utf-8")
        # Check for code patterns that would open the §20 path.
        # The docstring is allowed to mention it for documentation; the gate is
        # on executable path strings that would actually open the file.
        code_lines = [
            ln for ln in src.splitlines()
            if not ln.strip().startswith(("#", '"""', "'''"))
        ]
        code_text = "\n".join(code_lines)
        # These patterns would indicate actual file reads of the §20 artifact:
        forbidden_code_patterns = [
            '"validation/fundamentals"',
            "'validation/fundamentals'",
            'validation" / "fundamentals',
            "validation' / 'fundamentals",
            "read_table.*validation.*fundamentals",
        ]
        for pattern in forbidden_code_patterns:
            assert pattern not in code_text, (
                f"Leakage: fundamentals_adapter code references §20 path: {pattern!r}"
            )

    def test_adapter_source_has_no_network_imports(self) -> None:
        """The adapter must not import HTTP libraries (hermetic)."""
        src = Path(fa.__file__).read_text(encoding="utf-8")
        for banned in ("import requests", "import urllib", "import httpx", "import socket"):
            assert banned not in src, (
                f"Network import found in fundamentals_adapter: {banned!r}"
            )

    def test_ingest_does_not_write_validation_fundamentals(
        self, tmp_path: Path
    ) -> None:
        """ingest_xbrl() must write to discovery/, never to validation/."""
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        # The §20 validation artifact must not exist after discovery ingest.
        validation_path = run_dir / "validation" / "fundamentals.parquet"
        assert not validation_path.exists(), (
            "fundamentals_adapter must not write to validation/fundamentals.parquet"
        )
        # The discovery artifact must exist.
        discovery_path = run_dir / "discovery" / fa.FUNDAMENTALS_ARTIFACT
        assert discovery_path.exists(), "discovery/fundamentals_asreported.parquet not found"

    def test_discovery_artifact_name_is_not_validation_name(self) -> None:
        """The discovery artifact constant must not collide with the §20 name."""
        assert fa.FUNDAMENTALS_ARTIFACT != "fundamentals.parquet", (
            "FUNDAMENTALS_ARTIFACT must not equal 'fundamentals.parquet' (§20 conflict)"
        )
        assert "asreported" in fa.FUNDAMENTALS_ARTIFACT, (
            f"FUNDAMENTALS_ARTIFACT name should contain 'asreported': {fa.FUNDAMENTALS_ARTIFACT!r}"
        )


# ---------------------------------------------------------------------------
# 10. read_company_fundamentals PIT filter
# ---------------------------------------------------------------------------

class TestReadCompanyFundamentals:
    def test_pit_filter_applied(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        # Only rows filed before 2023-01-01 (before FY2023 filing Nov 2023)
        as_of = date(2023, 1, 1)
        rows = fa.read_company_fundamentals(run_id, "AAPL", as_of=as_of)
        for r in rows:
            assert r["available_at"] <= "2023-01-01", (
                f"PIT violation: available_at={r['available_at']} > 2023-01-01"
            )

    def test_returns_all_rows_without_as_of(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id="AAPL",
            facts_json_path=AAPL_FACTS,
            config_path=CONFIG_PATH,
        )
        all_rows = fa.read_company_fundamentals(run_id, "AAPL", as_of=None)
        assert len(all_rows) >= 5
