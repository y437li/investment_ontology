"""Tests for the offline SEC EDGAR filings adapter."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

# Make the backend package importable: repo_root/app/backend on sys.path so that
# ``theme_engine`` resolves the same way the FastAPI app imports it.
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from theme_engine.adapters import filings  # noqa: E402
from theme_engine.data_import import REQUIRED_MANIFEST_COLUMNS  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "filings"
SUBMISSIONS = FIXTURES / "submissions_aapl.json"
VINTAGE = "2026-06-21T00:00:00Z"


@pytest.fixture()
def rows() -> list[dict]:
    return filings.build_source_manifest(SUBMISSIONS, FIXTURES, VINTAGE)


def _filing_dates_from_fixture() -> list[str]:
    data = json.loads(SUBMISSIONS.read_text(encoding="utf-8"))
    return list(data["filings"]["recent"]["filingDate"])


def test_one_row_per_recent_filing(rows: list[dict]) -> None:
    assert len(rows) == 2


def test_all_required_columns_present(rows: list[dict]) -> None:
    for row in rows:
        assert set(row.keys()) == set(REQUIRED_MANIFEST_COLUMNS)


def test_available_at_equals_filing_date(rows: list[dict]) -> None:
    filing_dates = _filing_dates_from_fixture()
    for row, filing_date in zip(rows, filing_dates):
        # Point-in-time rule #1: available_at is the filingDate, never reportDate.
        assert row["available_at"] == filing_date
        assert row["published_at"] == filing_date


def test_available_at_is_not_report_date(rows: list[dict]) -> None:
    data = json.loads(SUBMISSIONS.read_text(encoding="utf-8"))
    report_dates = data["filings"]["recent"]["reportDate"]
    for row, report_date in zip(rows, report_dates):
        assert row["available_at"] != report_date


def test_vintage_set_on_every_row(rows: list[dict]) -> None:
    for row in rows:
        assert row["vintage"] == VINTAGE


def test_vintage_required() -> None:
    with pytest.raises(ValueError):
        filings.build_source_manifest(SUBMISSIONS, FIXTURES, "")


def test_source_and_identity(rows: list[dict]) -> None:
    for row in rows:
        assert row["source"] == "sec_edgar"
        assert row["source_id"]
        assert row["company_id"] == "AAPL"
        assert row["document_type"] in {"10-K", "10-Q"}


def test_raw_path_resolves_to_local_file(rows: list[dict]) -> None:
    # Includes the .htm -> .txt fallback for the second filing.
    for row in rows:
        assert (FIXTURES / row["raw_path"]).exists()


def test_write_source_manifest_roundtrip(rows: list[dict], tmp_path: Path) -> None:
    out = tmp_path / "source_manifest.csv"
    written = filings.write_source_manifest(rows, out)
    assert written == out

    with out.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        assert reader.fieldnames == REQUIRED_MANIFEST_COLUMNS
        read_rows = list(reader)

    assert len(read_rows) == len(rows)
    filing_dates = _filing_dates_from_fixture()
    for read_row, filing_date in zip(read_rows, filing_dates):
        assert read_row["available_at"] == filing_date
        assert read_row["vintage"] == VINTAGE


def test_no_network_imports() -> None:
    src = Path(filings.__file__).read_text(encoding="utf-8")
    for banned in ("requests", "urllib", "httpx", "socket"):
        assert f"import {banned}" not in src
