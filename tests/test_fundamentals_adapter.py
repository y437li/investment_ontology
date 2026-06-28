"""Tests for the XBRL as-reported fundamentals adapter (EG-B1).

Universe: S&P/TSX 60 (Canadian companies; currency CAD; IFRS taxonomy).
Fixture: Royal Bank of Canada (sec_cik 0001000275), IFRS ifrs-full concepts
in CAD, across FY2022 (filed 2022-12-02) and FY2023 (filed 2023-12-01).

Acceptance criteria verified here:
 1. Hermetic: all tests use committed fixture files; no network calls occur.
 2. >=5 metrics across >=2 periods for the RY fixture company.
 3. available_at = filing_date (PIT rule).
 4. Empty-but-schema-valid artifact when a company has no XBRL (null sec_cik).
 5. Leakage gate: the adapter never reads io_contracts §20
    validation/fundamentals.parquet.
 6. Metric names come from configs/fundamentals.yml (none hardcoded).
 7. Deduplication: (company_id, period_end, metric_name) is unique in output.
 8. Margin computation: gross_margin and operating_margin are derived.
 9. IFRS taxonomy: ifrs-full concepts parsed; us-gaap is fallback only.
10. Currency: read from XBRL unit (CAD/shares -> currency "CAD"); never assumed.
11. Null sec_cik path: tested with a real universe constituent (Hydro One).
12. PIT test is non-vacuous: as_of is between two filing dates so the
    assertion loop actually executes on the rows that pass.
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
# Royal Bank of Canada fixture: IFRS ifrs-full concepts, currency CAD.
# CIK: 0001000275. Two periods: FY2022 (filed 2022-12-02), FY2023 (filed 2023-12-01).
RY_FACTS = FIXTURES / "ry_facts.json"
CONFIG_PATH = REPO_ROOT / "configs" / "fundamentals.yml"
UNIVERSE_PATH = REPO_ROOT / "configs" / "universe.tsx60.yml"

# Company identifier used throughout tests (matches fixture entityName).
RY_COMPANY_ID = "RY.TO"

# PIT date between FY2022 filing (2022-12-02) and FY2023 filing (2023-12-01).
# Using this as_of ensures the FY2022 rows pass the filter and FY2023 rows
# are excluded — making the assertion loop non-vacuous.
PIT_BETWEEN = date(2023, 6, 15)


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


def _load_cfg():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _parse_ry(as_of=None):
    """Parse RY fixture and return rows."""
    cfg = _load_cfg()
    return fa._parse_company_facts(
        json.loads(RY_FACTS.read_text(encoding="utf-8")),
        RY_COMPANY_ID, as_of,
        fa._metric_index(cfg), fa._derived_index(cfg),
    )


def _load_universe_companies() -> list[dict]:
    """Load companies from the TSX 60 universe config."""
    import yaml
    data = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))
    return data.get("companies", [])


# ---------------------------------------------------------------------------
# 1. Hermetic fixture parsing
# ---------------------------------------------------------------------------

class TestHermeticXBRLParsing:
    """All tests parse the committed RY fixture; no network occurs."""

    def test_fixture_file_exists(self) -> None:
        assert RY_FACTS.exists(), f"XBRL fixture missing: {RY_FACTS}"

    def test_config_file_exists(self) -> None:
        assert CONFIG_PATH.exists(), f"fundamentals config missing: {CONFIG_PATH}"

    def test_universe_file_exists(self) -> None:
        assert UNIVERSE_PATH.exists(), f"universe config missing: {UNIVERSE_PATH}"

    def test_parse_returns_rows(self) -> None:
        rows = _parse_ry()
        assert len(rows) > 0, "Expected at least one row from RY fixture"

    def test_at_least_five_metrics(self) -> None:
        """Acceptance: >=5 distinct metric_names from the fixture."""
        rows = _parse_ry()
        metrics = {r["metric_name"] for r in rows}
        assert len(metrics) >= 5, (
            f"Expected >=5 distinct metrics; got {sorted(metrics)}"
        )

    def test_at_least_two_periods(self) -> None:
        """Acceptance: >=2 distinct period_end values from the fixture."""
        rows = _parse_ry()
        periods = {r["period_end"] for r in rows}
        assert len(periods) >= 2, (
            f"Expected >=2 distinct period_end values; got {sorted(periods)}"
        )


# ---------------------------------------------------------------------------
# 2. IFRS taxonomy: ifrs-full concepts parsed correctly
# ---------------------------------------------------------------------------

class TestIFRSTaxonomy:
    """Verify that IFRS ifrs-full concepts map to the expected metric_names."""

    def test_ifrs_revenue_maps_to_revenue(self) -> None:
        """ifrs-full:Revenue -> metric_name 'revenue'."""
        rows = _parse_ry()
        rev_rows = [r for r in rows if r["metric_name"] == "revenue"]
        assert rev_rows, "Expected 'revenue' rows from ifrs-full:Revenue concept"

    def test_ifrs_profitloss_maps_to_net_income(self) -> None:
        """ifrs-full:ProfitLoss -> metric_name 'net_income'."""
        rows = _parse_ry()
        ni_rows = [r for r in rows if r["metric_name"] == "net_income"]
        assert ni_rows, "Expected 'net_income' rows from ifrs-full:ProfitLoss"

    def test_ifrs_eps_maps_to_eps(self) -> None:
        """ifrs-full:BasicEarningsLossPerShare -> metric_name 'eps'."""
        rows = _parse_ry()
        eps_rows = [r for r in rows if r["metric_name"] == "eps"]
        assert eps_rows, "Expected 'eps' rows from ifrs-full:BasicEarningsLossPerShare"

    def test_ifrs_ocf_maps_to_operating_cash_flow(self) -> None:
        """ifrs-full:CashFlowsFromUsedInOperatingActivities -> 'operating_cash_flow'."""
        rows = _parse_ry()
        ocf_rows = [r for r in rows if r["metric_name"] == "operating_cash_flow"]
        assert ocf_rows, (
            "Expected 'operating_cash_flow' rows from "
            "ifrs-full:CashFlowsFromUsedInOperatingActivities"
        )

    def test_ifrs_borrowings_maps_to_total_debt(self) -> None:
        """ifrs-full:LongtermBorrowings -> metric_name 'total_debt'."""
        rows = _parse_ry()
        debt_rows = [r for r in rows if r["metric_name"] == "total_debt"]
        assert debt_rows, "Expected 'total_debt' rows from ifrs-full:LongtermBorrowings"

    def test_fixture_has_ifrs_full_namespace(self) -> None:
        """Verify the fixture actually carries data in the ifrs-full namespace."""
        facts = json.loads(RY_FACTS.read_text(encoding="utf-8"))
        assert "ifrs-full" in (facts.get("facts") or {}), (
            "RY fixture must contain ifrs-full namespace"
        )
        assert "us-gaap" not in (facts.get("facts") or {}), (
            "RY fixture should not contain us-gaap (it's an IFRS filer)"
        )


# ---------------------------------------------------------------------------
# 3. PIT discipline: available_at = filing_date (non-vacuous)
# ---------------------------------------------------------------------------

class TestPITDiscipline:
    def test_available_at_equals_filing_date(self) -> None:
        rows = _parse_ry()
        for row in rows:
            assert row["available_at"] == row["filing_date"], (
                f"PIT violation: available_at={row['available_at']} "
                f"!= filing_date={row['filing_date']} for {row['metric_name']}"
            )

    def test_available_at_is_not_period_end(self) -> None:
        """available_at must be the filing date, never the period end."""
        rows = _parse_ry()
        for row in rows:
            # period_end (e.g. 2023-10-31) != filing_date (e.g. 2023-12-01)
            if row["period_end"] != row["filing_date"]:
                assert row["available_at"] != row["period_end"], (
                    f"available_at should be filing_date, not period_end: {row}"
                )

    def test_pit_filter_excludes_future_filings(self) -> None:
        """PIT test is non-vacuous: as_of between two filing dates.

        RY fixture:
          FY2022 period filed 2022-12-02  (before PIT_BETWEEN = 2023-06-15)
          FY2023 period filed 2023-12-01  (after  PIT_BETWEEN = 2023-06-15)

        Rows for FY2022 MUST be returned; rows for FY2023 MUST be excluded.
        The assertion loop below is guaranteed to run (non-vacuous).
        """
        rows = _parse_ry(as_of=PIT_BETWEEN)

        # Non-vacuousness: assert the filter actually returns some rows.
        assert len(rows) > 0, (
            f"PIT test is vacuous: as_of={PIT_BETWEEN} excluded ALL rows. "
            "Pick an as_of BETWEEN two filing dates in the fixture."
        )

        # All returned rows must satisfy the PIT constraint.
        as_of_str = PIT_BETWEEN.strftime("%Y-%m-%d")
        for row in rows:
            assert row["available_at"] <= as_of_str, (
                f"Leakage: row available_at {row['available_at']} > as_of {as_of_str}"
            )

        # Verify that FY2023 rows are actually excluded (filed 2023-12-01 > 2023-06-15).
        fy2023_rows = [r for r in rows if r["period_end"] == "2023-10-31"]
        assert len(fy2023_rows) == 0, (
            f"FY2023 rows should be excluded by PIT filter (filed 2023-12-01 > "
            f"{as_of_str}), but found: {fy2023_rows}"
        )

        # Verify FY2022 rows ARE present (filed 2022-12-02 <= 2023-06-15).
        fy2022_rows = [r for r in rows if r["period_end"] == "2022-10-31"]
        assert len(fy2022_rows) > 0, (
            f"Expected FY2022 rows (filed 2022-12-02 <= {as_of_str}) to be present"
        )


# ---------------------------------------------------------------------------
# 4. Units and currency — CAD for Canadian filers
# ---------------------------------------------------------------------------

class TestUnitsAndCurrency:
    def test_monetary_metrics_have_cad_currency(self) -> None:
        """Monetary metrics from a Canadian IFRS filer must have currency='CAD'."""
        rows = _parse_ry()
        monetary_metrics = {"revenue", "net_income", "operating_cash_flow", "total_debt"}
        for row in rows:
            if row["metric_name"] in monetary_metrics:
                assert row["currency"] == "CAD", (
                    f"Expected CAD currency for {row['metric_name']}: {row}"
                )

    def test_eps_currency_extracted_from_unit(self) -> None:
        """EPS unit 'CAD/shares' must yield currency='CAD', not None.

        This tests the fix for the EPS currency nit: 'CAD/shares' -> 'CAD'.
        """
        rows = _parse_ry()
        eps_rows = [r for r in rows if r["metric_name"] == "eps"]
        assert eps_rows, "No eps rows found"
        for r in eps_rows:
            assert r["currency"] == "CAD", (
                f"EPS currency should be 'CAD' (extracted from 'CAD/shares'): {r}"
            )
            assert r["unit"] == "CAD/shares", (
                f"EPS unit should be 'CAD/shares': {r}"
            )

    def test_ratio_metrics_have_no_currency(self) -> None:
        """Derived ratio metrics (margins) must have currency=None."""
        rows = _parse_ry()
        ratio_metrics = {"gross_margin", "operating_margin", "ebitda_margin"}
        for row in rows:
            if row["metric_name"] in ratio_metrics:
                assert row["currency"] is None, (
                    f"Margin should have no currency: {row}"
                )
                assert row["unit"] == "ratio", (
                    f"Margin unit should be 'ratio': {row}"
                )

    def test_currency_extraction_helper(self) -> None:
        """Unit test _extract_currency for various XBRL unit strings."""
        assert fa._extract_currency("CAD") == "CAD"
        assert fa._extract_currency("USD") == "USD"
        assert fa._extract_currency("CAD/shares") == "CAD"
        assert fa._extract_currency("USD/shares") == "USD"
        assert fa._extract_currency("pure") is None
        assert fa._extract_currency("ratio") is None
        assert fa._extract_currency("shares") is None


# ---------------------------------------------------------------------------
# 5. Margin computation (derived from IFRS components)
# ---------------------------------------------------------------------------

class TestMarginComputation:
    def test_gross_margin_derived(self) -> None:
        """gross_margin is computed from GrossProfit / Revenue (IFRS concepts)."""
        rows = _parse_ry()
        gm_rows = [r for r in rows if r["metric_name"] == "gross_margin"]
        assert gm_rows, "Expected gross_margin rows from derived computation"
        for r in gm_rows:
            assert 0.0 < r["metric_value"] < 1.0, (
                f"gross_margin out of range: {r['metric_value']}"
            )

    def test_operating_margin_derived(self) -> None:
        """operating_margin is computed from ProfitFromOperatingActivities / Revenue."""
        rows = _parse_ry()
        om_rows = [r for r in rows if r["metric_name"] == "operating_margin"]
        assert om_rows, "Expected operating_margin rows from derived computation"
        for r in om_rows:
            assert 0.0 < r["metric_value"] < 1.0, (
                f"operating_margin out of range: {r['metric_value']}"
            )

    def test_gross_margin_fy2023_value(self) -> None:
        """RY FY2023 gross margin: 25000M / 57290M ≈ 0.4364."""
        rows = _parse_ry()
        fy23_gm = [
            r for r in rows
            if r["metric_name"] == "gross_margin" and r["period_end"] == "2023-10-31"
        ]
        assert fy23_gm, "No FY2023 gross_margin row found"
        expected = 25_000_000_000 / 57_290_000_000
        assert abs(fy23_gm[0]["metric_value"] - expected) < 0.001, (
            f"gross_margin value {fy23_gm[0]['metric_value']} differs from "
            f"expected {expected:.4f}"
        )


# ---------------------------------------------------------------------------
# 6. Empty-but-schema-valid artifact (null sec_cik path)
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
            company_id="H.TO",     # Hydro One — null sec_cik in universe
            facts_json_path=None,  # no XBRL = sec_cik is null
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

    def test_null_cik_constituent_from_universe(self, tmp_path: Path) -> None:
        """Test the null-sec_cik path with a real TSX 60 constituent.

        Hydro One (H.TO) has sec_cik=null in universe.tsx60.yml — it does not
        file with the SEC. The adapter must write a schema-valid empty artifact.
        """
        companies = _load_universe_companies()
        null_cik_companies = [c for c in companies if c.get("sec_cik") is None]
        assert null_cik_companies, (
            "Expected at least one null-sec_cik company in universe.tsx60.yml"
        )

        # Use Hydro One (H.TO) as the representative null-CIK constituent.
        hydro_one = next(
            (c for c in null_cik_companies if c["tsx_ticker"] == "H.TO"), None
        )
        assert hydro_one is not None, "Hydro One (H.TO) not found in universe config"
        assert hydro_one["sec_cik"] is None, "H.TO should have null sec_cik"

        run_id, run_dir = _make_run(tmp_path)
        result = fa.ingest_xbrl(
            run_id=run_id,
            company_id=hydro_one["tsx_ticker"],
            facts_json_path=None,  # sec_cik is null; no EDGAR file
            config_path=CONFIG_PATH,
        )
        assert result["rows_written"] == 0

        artifact_path = run_dir / "discovery" / fa.FUNDAMENTALS_ARTIFACT
        assert artifact_path.exists(), (
            "Adapter must write empty artifact even for null-sec_cik companies"
        )
        table = pq.read_table(artifact_path)
        assert set(table.schema.names) == set(fa.FUNDAMENTALS_COLUMNS)
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

    def test_all_none_currency_rows_keep_string_schema(self, tmp_path: Path) -> None:
        """When all rows have currency=None (e.g. ratio-only ingestion), the
        Arrow schema must still have 'currency' as string, not null type.

        This tests the _rows_to_table schema-pinning fix.
        """
        # Build rows where all currencies are None (ratio metrics only).
        ratio_rows = [
            {
                "company_id": "TEST.TO",
                "period_end": "2023-10-31",
                "metric_name": "gross_margin",
                "metric_value": 0.43,
                "unit": "ratio",
                "currency": None,   # all-None column
                "filing_date": "2023-12-01",
                "available_at": "2023-12-01",
                "source": "edgar_xbrl",
                "source_id": "abc123",
            }
        ]
        table = fa._rows_to_table(ratio_rows)
        currency_field = table.schema.field("currency")
        assert currency_field.type == pa.string(), (
            f"currency column must be string even when all values are None; "
            f"got {currency_field.type}"
        )


# ---------------------------------------------------------------------------
# 7. Full end-to-end ingest via ingest_xbrl()
# ---------------------------------------------------------------------------

class TestIngestXBRL:
    def test_ingest_ry_five_metrics_two_periods(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        result = fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
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
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
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
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
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
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        count_first = len(fa.read_fundamentals(run_id))

        fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        count_second = len(fa.read_fundamentals(run_id))
        assert count_first == count_second, (
            f"Duplicate rows after re-ingest: {count_first} -> {count_second}"
        )


# ---------------------------------------------------------------------------
# 8. Deduplication: (company_id, period_end, metric_name) is unique
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicate_keys(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        rows = fa.read_fundamentals(run_id)
        keys = [(r["company_id"], r["period_end"], r["metric_name"]) for r in rows]
        assert len(keys) == len(set(keys)), (
            f"Duplicate reconciliation keys found in output"
        )


# ---------------------------------------------------------------------------
# 9. Metric names from config (no hardcoding)
# ---------------------------------------------------------------------------

class TestMetricNameConfig:
    def test_metric_names_in_config_whitelist(self, tmp_path: Path) -> None:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        allowed = {m["metric_name"] for m in cfg.get("metrics", [])}

        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        rows = fa.read_fundamentals(run_id)
        for row in rows:
            assert row["metric_name"] in allowed, (
                f"metric_name {row['metric_name']!r} not in config whitelist {allowed}"
            )


# ---------------------------------------------------------------------------
# 10. Leakage gate: adapter never reads validation/fundamentals.parquet
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
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
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
# 11. read_company_fundamentals PIT filter
# ---------------------------------------------------------------------------

class TestReadCompanyFundamentals:
    def test_pit_filter_applied(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        # PIT_BETWEEN (2023-06-15) is between FY2022 filing (2022-12-02) and
        # FY2023 filing (2023-12-01), so some rows will pass and some will not.
        rows = fa.read_company_fundamentals(run_id, RY_COMPANY_ID, as_of=PIT_BETWEEN)
        assert len(rows) > 0, (
            "PIT-filtered read should return some rows (FY2022 rows must pass)"
        )
        for r in rows:
            assert r["available_at"] <= PIT_BETWEEN.strftime("%Y-%m-%d"), (
                f"PIT violation: available_at={r['available_at']} > {PIT_BETWEEN}"
            )

    def test_returns_all_rows_without_as_of(self, tmp_path: Path) -> None:
        run_id, run_dir = _make_run(tmp_path)
        fa.ingest_xbrl(
            run_id=run_id,
            company_id=RY_COMPANY_ID,
            facts_json_path=RY_FACTS,
            config_path=CONFIG_PATH,
        )
        all_rows = fa.read_company_fundamentals(run_id, RY_COMPANY_ID, as_of=None)
        assert len(all_rows) >= 5


# ---------------------------------------------------------------------------
# 12. Universe integration: sec_cik lookup and null-CIK coverage
# ---------------------------------------------------------------------------

class TestUniverseIntegration:
    def test_universe_has_companies_with_sec_cik(self) -> None:
        """Universe must contain companies with sec_cik (cross-listed filers)."""
        companies = _load_universe_companies()
        with_cik = [c for c in companies if c.get("sec_cik") is not None]
        assert len(with_cik) >= 10, (
            f"Expected >=10 companies with sec_cik; got {len(with_cik)}"
        )

    def test_universe_has_null_cik_companies(self) -> None:
        """Universe must contain companies with sec_cik=null (TSX-only filers)."""
        companies = _load_universe_companies()
        null_cik = [c for c in companies if c.get("sec_cik") is None]
        assert len(null_cik) >= 1, (
            "Expected at least one null-sec_cik company in universe.tsx60.yml"
        )

    def test_ry_cik_in_universe(self) -> None:
        """Royal Bank of Canada (our fixture company) must have CIK in universe."""
        companies = _load_universe_companies()
        ry = next((c for c in companies if c["tsx_ticker"] == "RY.TO"), None)
        assert ry is not None, "RY.TO not found in universe config"
        assert ry["sec_cik"] == "0001000275", (
            f"RY.TO CIK mismatch: {ry['sec_cik']!r}"
        )

    def test_constellation_software_has_null_cik(self) -> None:
        """Constellation Software (CSU.TO) also has null sec_cik."""
        companies = _load_universe_companies()
        csu = next((c for c in companies if c["tsx_ticker"] == "CSU.TO"), None)
        assert csu is not None, "CSU.TO not found in universe config"
        assert csu["sec_cik"] is None, (
            f"CSU.TO should have null sec_cik; got {csu['sec_cik']!r}"
        )


# Import pa for schema type check in TestEmptyArtifact
import pyarrow as pa  # noqa: E402
