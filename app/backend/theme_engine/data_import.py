"""Data import service.

Validates a source manifest and writes a normalized `raw_documents.parquet`
artifact into the run directory.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException

from .config import REPO_ROOT
from . import runs


REQUIRED_MANIFEST_COLUMNS: list[str] = [
    "source",
    "source_id",
    "title",
    "document_type",
    "company_id",
    "raw_path",
    "published_at",
    "available_at",
    "vintage",
    "language",
    "source_url",
    "license",
    "confidentiality",
    "notes",
]

REQUIRED_RAW_COLUMNS = REQUIRED_MANIFEST_COLUMNS + [
    "document_id",
    "content_hash",
    "ingested_at",
]

TIME_COLUMNS: tuple[str, str] = ("published_at", "available_at")


def _resolve_input_path(documents_dir: str) -> Path:
    p = Path(documents_dir)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _resolve_manifest_path(path_text: str) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _read_rows(manifest_path: Path) -> list[dict[str, str]]:
    if not manifest_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"source manifest not found: {manifest_path}",
        )

    with manifest_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None:
            raise HTTPException(status_code=422, detail="source manifest missing header row")

        required = set(REQUIRED_MANIFEST_COLUMNS)
        header = set(reader.fieldnames)
        if "source_vintage" in header and "vintage" not in header:
            header.add("vintage")

        missing = [col for col in REQUIRED_MANIFEST_COLUMNS if col not in header]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"source manifest missing required columns: {missing}",
            )

        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = dict(row)
            if not normalized.get("vintage") and normalized.get("source_vintage"):
                normalized["vintage"] = normalized["source_vintage"]
            rows.append(normalized)
        return rows


def _is_valid_date(v: str) -> bool:
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return True
    except Exception:
        return False


def _validate_row(
    row: dict[str, str],
    documents_root: Path,
    as_of_date: str,
) -> tuple[bool, str]:
    for col in REQUIRED_MANIFEST_COLUMNS:
        if not row.get(col):
            return False, f"missing required field: {col}"

    raw_path = documents_root / row["raw_path"]
    if not raw_path.exists():
        return False, "raw_path missing"

    for col in TIME_COLUMNS:
        if not _is_valid_date(row[col]):
            return False, f"invalid date format in {col}"

    if row["available_at"] > as_of_date:
        return False, "available_at is after run as_of_date"

    return True, ""


def _content_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _row_to_parquet_row(row: dict[str, str], idx: int, documents_root: Path) -> dict[str, str]:
    raw_path = Path(row["raw_path"])
    abs_raw_path = raw_path if raw_path.is_absolute() else documents_root / raw_path
    document_id = f"{row['source_id']}::{row['company_id']}::{idx}"
    return {
        **{k: row.get(k, "") for k in REQUIRED_MANIFEST_COLUMNS},
        "document_id": document_id,
        "content_hash": _content_sha256(abs_raw_path),
        "ingested_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _write_raw_documents(run_dir: Path, rows: list[dict[str, str]], documents_root: Path) -> None:
    discovery_dir = run_dir / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    out_path = discovery_dir / "raw_documents.parquet"
    if not rows:
        table = pa.Table.from_pydict({col: [] for col in REQUIRED_RAW_COLUMNS})
    else:
        parquet_rows = [_row_to_parquet_row(row, i, documents_root) for i, row in enumerate(rows)]
        table = pa.Table.from_pylist(parquet_rows)
    pq.write_table(table, out_path)


def import_manifest(
    run_id: str,
    documents_dir: str,
    source_manifest_path: str,
) -> tuple[int, int, list[str]]:
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    documents_root = _resolve_input_path(documents_dir)
    if not documents_root.exists():
        raise HTTPException(
            status_code=404,
            detail=f"documents_dir not found: {documents_root}",
        )

    manifest_rows = _read_rows(_resolve_manifest_path(source_manifest_path))
    accepted_rows: list[dict[str, str]] = []
    quarantine_reasons: list[str] = []

    for row_num, row in enumerate(manifest_rows, start=1):
        ok, reason = _validate_row(row, documents_root, manifest.as_of_date)
        if not ok:
            quarantine_reasons.append(f"row {row_num}: {reason}")
            continue
        accepted_rows.append(row)

    included = len(accepted_rows)
    rejected = len(quarantine_reasons)
    quarantined = rejected

    run_dir = runs.get_run_dir(run_id)
    _write_raw_documents(run_dir, accepted_rows, documents_root)
    return included, quarantined, quarantine_reasons
