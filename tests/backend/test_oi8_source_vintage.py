"""OI-8 — Source vintage & available_at ownership.

Locked rule (docs/design_temporal_projection.md):
  available_at = the source's publication time (filing date, published_at, etc.).
  Ingest is read-only on the timestamp; it never invents or shifts it.
  A source with NO determinable publish time is QUARANTINED (fail-closed).
  available_at is set ONCE at ingest and is IMMUTABLE downstream.

Tests:
  1. Publish time used: available_at in raw_documents.parquet equals the
     source's published/available timestamp from the manifest, NOT the import
     time (ingested_at).
  2. No publish time quarantined: a row whose published_at is absent is
     quarantined with reason "no_determinable_publish_time"; it is NOT admitted.
  3. No available_at quarantined: a row whose available_at is absent is likewise
     quarantined; it is NOT admitted with a guessed/default date.
  4. Immutability in cleaning: available_at in documents.parquet equals the
     value that was stamped at import (inherited verbatim; cleaning does not
     shift it).
  5. Both fields missing: a row missing both published_at and available_at is
     quarantined, not admitted.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app
from theme_engine.data_import import QUARANTINE_NO_PUBLISH_TIME

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MANIFEST_HEADER = [
    "source",
    "source_id",
    "title",
    "document_type",
    "company_id",
    "raw_path",
    "published_at",
    "available_at",
    "source_vintage",
    "language",
    "source_url",
    "license",
    "confidentiality",
    "notes",
]


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=_MANIFEST_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _good_row(docs_dir: Path, **overrides) -> dict[str, str]:
    """Return a minimal valid manifest row with optional field overrides."""
    base = {
        "source": "sec",
        "source_id": "f-oi8",
        "title": "OI-8 Test Filing",
        "document_type": "10-k",
        "company_id": "TEST",
        "raw_path": "test_doc.txt",
        "published_at": "2024-01-15",
        "available_at": "2024-01-20",
        "source_vintage": "2024-01-21T00:00:00Z",
        "language": "en",
        "source_url": "https://example.com/oi8",
        "license": "public",
        "confidentiality": "public",
        "notes": "oi8 seed",
    }
    base.update(overrides)
    return base


def _create_run(as_of_date: str = "2024-06-30") -> str:
    resp = client.post("/api/runs/create", json={"as_of_date": as_of_date})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


# ---------------------------------------------------------------------------
# Test 1: publish time used (not import time)
# ---------------------------------------------------------------------------


def test_oi8_publish_time_used_not_import_time():
    """available_at in raw_documents.parquet must equal the manifest's
    available_at value, not the ingested_at (import clock) value.

    This verifies the read-only rule: ingest stamps the SOURCE's publish time,
    never the current wall-clock time.
    """
    run_id = _create_run()

    with tempfile.TemporaryDirectory(prefix="oi8_vintage_") as docs_root:
        docs_dir = Path(docs_root)
        (docs_dir / "test_doc.txt").write_text("Sample filing text.", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        expected_published_at = "2024-01-15"
        expected_available_at = "2024-01-20"  # differs from published_at (filing lag)
        _write_manifest(
            manifest_path,
            [_good_row(docs_dir,
                       published_at=expected_published_at,
                       available_at=expected_available_at)],
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["raw_documents"] == 1
        assert resp.json()["quarantined"] == 0

        artifact = (
            Path(settings.run_output_dir)
            / run_id / "discovery" / "raw_documents.parquet"
        )
        assert artifact.exists()
        rows = pq.read_table(artifact).to_pylist()
        assert len(rows) == 1
        row = rows[0]

        # OI-8 rule: available_at = source's publication timestamp from manifest
        assert row["available_at"] == expected_available_at, (
            f"available_at should be the source's publish time "
            f"({expected_available_at!r}), not the import time. "
            f"Got: {row['available_at']!r}"
        )
        assert row["published_at"] == expected_published_at, (
            f"published_at should be {expected_published_at!r}; got {row['published_at']!r}"
        )

        # ingested_at is the import clock time — it must NOT be used as available_at
        ingested_at = row.get("ingested_at", "")
        assert ingested_at != expected_available_at, (
            "ingested_at must be a different timestamp from available_at "
            "(they record different things: import time vs. source publish time)"
        )


# ---------------------------------------------------------------------------
# Test 2: missing published_at → quarantined (fail-closed)
# ---------------------------------------------------------------------------


def test_oi8_missing_published_at_quarantined():
    """A document whose published_at is absent must be quarantined (fail-closed).

    It must NOT be admitted with a default/guessed/current date for available_at.
    """
    run_id = _create_run()

    with tempfile.TemporaryDirectory(prefix="oi8_nopub_") as docs_root:
        docs_dir = Path(docs_root)
        (docs_dir / "test_doc.txt").write_text("Sample filing text.", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        _write_manifest(
            manifest_path,
            [_good_row(docs_dir, published_at="")],  # no determinable publish time
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()

        # Fail-closed: document with no publish time must NOT be admitted
        assert result["raw_documents"] == 0, (
            "A document with no determinable publish time must not be admitted. "
            f"Got raw_documents={result['raw_documents']!r}"
        )
        assert result["quarantined"] == 1, (
            f"Expected 1 quarantined document; got {result['quarantined']!r}"
        )
        assert len(result["quarantine_reasons"]) == 1

        # The quarantine reason must name the vintage failure
        reason = result["quarantine_reasons"][0]
        assert QUARANTINE_NO_PUBLISH_TIME in reason, (
            f"Quarantine reason should contain {QUARANTINE_NO_PUBLISH_TIME!r}; "
            f"got: {reason!r}"
        )

        # Verify raw_documents.parquet has zero rows (not admitted)
        artifact = (
            Path(settings.run_output_dir)
            / run_id / "discovery" / "raw_documents.parquet"
        )
        assert artifact.exists()
        rows = pq.read_table(artifact).to_pylist()
        assert len(rows) == 0, (
            f"raw_documents.parquet must be empty for a no-publish-time source; "
            f"found {len(rows)} row(s)"
        )


# ---------------------------------------------------------------------------
# Test 3: missing available_at → quarantined (fail-closed)
# ---------------------------------------------------------------------------


def test_oi8_missing_available_at_quarantined():
    """A document whose available_at is absent must be quarantined (fail-closed).

    The ingest must NOT default available_at to the current time or any other
    surrogate date.
    """
    run_id = _create_run()

    with tempfile.TemporaryDirectory(prefix="oi8_noavail_") as docs_root:
        docs_dir = Path(docs_root)
        (docs_dir / "test_doc.txt").write_text("Sample filing text.", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        _write_manifest(
            manifest_path,
            [_good_row(docs_dir, available_at="")],  # no determinable publish time
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()

        assert result["raw_documents"] == 0, (
            "A document with no available_at must not be admitted; "
            f"got raw_documents={result['raw_documents']!r}"
        )
        assert result["quarantined"] == 1, (
            f"Expected 1 quarantined document; got {result['quarantined']!r}"
        )

        reason = result["quarantine_reasons"][0]
        assert QUARANTINE_NO_PUBLISH_TIME in reason, (
            f"Quarantine reason should contain {QUARANTINE_NO_PUBLISH_TIME!r}; "
            f"got: {reason!r}"
        )

        artifact = (
            Path(settings.run_output_dir)
            / run_id / "discovery" / "raw_documents.parquet"
        )
        rows = pq.read_table(artifact).to_pylist()
        assert len(rows) == 0, (
            "raw_documents.parquet must be empty when available_at is absent"
        )


# ---------------------------------------------------------------------------
# Test 4: available_at is immutable in cleaning stage
# ---------------------------------------------------------------------------


def test_oi8_available_at_immutable_in_cleaning():
    """Cleaning must not alter available_at.

    The value in documents.parquet must equal the value stamped at import
    (from raw_documents.parquet), not the cleaned_at timestamp.
    """
    run_id = _create_run()

    with tempfile.TemporaryDirectory(prefix="oi8_immut_") as docs_root:
        docs_dir = Path(docs_root)
        (docs_dir / "test_doc.txt").write_text(
            "Annual report content. Revenue grew 10%.", encoding="utf-8"
        )

        manifest_path = docs_dir / "source_manifest.csv"
        expected_available_at = "2024-02-15"
        _write_manifest(
            manifest_path,
            [_good_row(docs_dir,
                       published_at="2023-12-31",
                       available_at=expected_available_at)],
        )

        # Import
        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["raw_documents"] == 1

        # Clean
        resp = client.post(
            "/api/data/clean",
            json={"run_id": run_id, "documents_dir": str(docs_dir)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["included_documents"] == 1

        # Verify available_at is inherited verbatim in documents.parquet
        discovery_dir = (
            Path(settings.run_output_dir) / run_id / "discovery"
        )
        raw_rows = pq.read_table(discovery_dir / "raw_documents.parquet").to_pylist()
        doc_rows = pq.read_table(discovery_dir / "documents.parquet").to_pylist()

        assert len(raw_rows) == 1
        assert len(doc_rows) == 1

        raw_available_at = raw_rows[0]["available_at"]
        doc_available_at = doc_rows[0]["available_at"]

        # Both should equal the source publish time from the manifest
        assert raw_available_at == expected_available_at, (
            f"raw_documents available_at should be {expected_available_at!r}; "
            f"got {raw_available_at!r}"
        )
        assert doc_available_at == expected_available_at, (
            f"documents available_at should equal the import value "
            f"({expected_available_at!r}); cleaning must not shift it. "
            f"Got: {doc_available_at!r}"
        )

        # Cleaning stage has its own clock (cleaned_at); it must differ from available_at
        cleaned_at = doc_rows[0].get("cleaned_at", "")
        assert cleaned_at != expected_available_at, (
            "cleaned_at must be distinct from available_at — they record "
            "different things (stage clock vs. source publish time)"
        )


# ---------------------------------------------------------------------------
# Test 5: both published_at and available_at missing → quarantined
# ---------------------------------------------------------------------------


def test_oi8_both_timestamp_fields_missing_quarantined():
    """A row with both published_at and available_at absent must be quarantined.

    Only one row in the batch; confirms zero documents are admitted.
    """
    run_id = _create_run()

    with tempfile.TemporaryDirectory(prefix="oi8_both_") as docs_root:
        docs_dir = Path(docs_root)
        (docs_dir / "test_doc.txt").write_text("Content.", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        _write_manifest(
            manifest_path,
            [_good_row(docs_dir, published_at="", available_at="")],
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()

        assert result["raw_documents"] == 0
        assert result["quarantined"] == 1
        reason = result["quarantine_reasons"][0]
        assert QUARANTINE_NO_PUBLISH_TIME in reason, (
            f"Expected {QUARANTINE_NO_PUBLISH_TIME!r} in quarantine reason; got: {reason!r}"
        )
