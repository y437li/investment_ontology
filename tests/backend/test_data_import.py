"""Milestone 1 data import contract.

Validates manifest-driven raw-document ingestion and source-time metadata checks.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app

client = TestClient(app)


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    header = [
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
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_data_import_honors_point_in_time_manifest_rules():
    run = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    run_id = run["run_id"]

    with tempfile.TemporaryDirectory(prefix="theme_inputs_") as docs_root:
        docs_dir = Path(docs_root)
        raw_file = docs_dir / "filing.txt"
        raw_file.write_text("sample filing", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        _write_manifest(
            manifest_path,
            [
                {
                    "source": "sec",
                    "source_id": "f-1",
                    "title": "sample filing",
                    "document_type": "10-k",
                    "company_id": "ABC",
                    "raw_path": "filing.txt",
                    "published_at": "2024-01-02",
                    "available_at": "2024-01-02",
                    "source_vintage": "2024-01-03T00:00:00Z",
                    "language": "en",
                    "source_url": "https://example.com/f-1",
                    "license": "public",
                    "confidentiality": "public",
                    "notes": "seed",
                },
            ],
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["raw_documents"] == 1
        assert resp.json()["quarantined"] == 0
        assert resp.json()["quarantine_reasons"] == []

        artifact = Path(settings.run_output_dir) / run_id / "raw_documents.parquet"
        assert artifact.exists()
        table = pq.read_table(artifact)
        column_names = table.column_names
        for col in ["document_id", "content_hash", "ingested_at", "vintage"]:
            assert col in column_names


def test_data_import_rejects_invalid_manifest_rows():
    run = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    run_id = run["run_id"]

    with tempfile.TemporaryDirectory(prefix="theme_inputs_") as docs_root:
        docs_dir = Path(docs_root)
        raw_file = docs_dir / "filing.txt"
        raw_file.write_text("sample filing", encoding="utf-8")

        manifest_path = docs_dir / "source_manifest.csv"
        _write_manifest(
            manifest_path,
            [
                {
                    "source": "sec",
                    "source_id": "f-1",
                    "title": "sample filing",
                    "document_type": "10-k",
                    "company_id": "ABC",
                    "raw_path": "filing.txt",
                    "published_at": "2024-01-02",
                    "available_at": "",
                    "source_vintage": "2024-01-03T00:00:00Z",
                    "language": "en",
                    "source_url": "https://example.com/f-1",
                    "license": "public",
                    "confidentiality": "public",
                    "notes": "seed",
                },
            ],
        )

        resp = client.post(
            "/api/data/import",
            json={
                "run_id": run_id,
                "documents_dir": str(docs_dir),
                "source_manifest_path": str(manifest_path),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["raw_documents"] == 0
        assert resp.json()["quarantined"] == 1
        assert len(resp.json()["quarantine_reasons"]) == 1
