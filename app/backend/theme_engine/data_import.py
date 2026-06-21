"""Data import service.

Validates a source manifest and writes a normalized `raw_documents.parquet`
artifact into the run directory for Milestone 1 readiness.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

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
    "source_vintage",
    "language",
    "source_url",
    "license",
    "confidentiality",
    "notes",
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

        header = set(reader.fieldnames)
        missing = [col for col in REQUIRED_MANIFEST_COLUMNS if col not in header]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"source manifest missing required columns: {missing}",
            )

        return [dict(row) for row in reader]


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


def _write_raw_documents(run_dir: Path, rows: Iterable[dict[str, str]]) -> None:
    out_path = run_dir / "raw_documents.parquet"
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=REQUIRED_MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in REQUIRED_MANIFEST_COLUMNS})


def import_manifest(
    run_id: str,
    documents_dir: str,
    source_manifest_path: str,
) -> tuple[int, int]:
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
    included = 0
    rejected = 0

    accepted_rows: list[dict[str, str]] = []
    for row in manifest_rows:
        ok, _ = _validate_row(row, documents_root, manifest.as_of_date)
        if not ok:
            rejected += 1
            continue
        accepted_rows.append(row)
        included += 1

    run_dir = runs.get_run_dir(run_id)
    _write_raw_documents(run_dir, accepted_rows)
    return included, rejected
