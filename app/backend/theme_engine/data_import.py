"""Data import service.

Validates a source manifest and writes a normalized `raw_documents.parquet`
artifact into the run directory.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
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
    "included_in_discovery",
    "exclusion_reason",
]

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


def _parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[:10][0].isdigit():
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except Exception:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _validate_row(
    row: dict[str, str],
    documents_root: Path,
    as_of_date: str,
) -> tuple[bool, str, bool]:
    for col in REQUIRED_MANIFEST_COLUMNS:
        if not row.get(col):
            return False, f"missing required field: {col}", False

    raw_path = Path(row["raw_path"])
    if raw_path.is_absolute():
        return False, "raw_path must be relative to documents_dir", False

    abs_raw_path = documents_root / raw_path
    if not abs_raw_path.exists():
        return False, "raw_path missing", False

    published_at = _parse_timestamp(row["published_at"])
    available_at = _parse_timestamp(row["available_at"])
    if published_at is None:
        return False, "invalid date format in published_at", False
    if available_at is None:
        return False, "invalid date format in available_at", False

    if published_at > available_at:
        return False, "published_at must be <= available_at", False

    try:
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    except Exception:
        as_of = available_at
    included_in_discovery = available_at.date() <= as_of.date()
    return True, "", included_in_discovery


def _content_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _row_to_parquet_row(row: dict[str, str], idx: int, documents_root: Path) -> dict[str, str]:
    raw_path = Path(row["raw_path"])
    abs_raw_path = documents_root / raw_path
    document_id = f"{row['source_id']}::{row['company_id']}::{idx}"
    included_in_discovery = row.get("included_in_discovery") == "1"
    exclusion_reason = row.get("exclusion_reason", "")
    return {
        **{k: row.get(k, "") for k in REQUIRED_MANIFEST_COLUMNS},
        "document_id": document_id,
        "content_hash": _content_sha256(abs_raw_path),
        "ingested_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "included_in_discovery": included_in_discovery,
        "exclusion_reason": exclusion_reason,
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
) -> tuple[int, int, int, int, list[str]]:
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
    future_excluded = 0
    included_in_discovery_count = 0

    for row_num, row in enumerate(manifest_rows, start=1):
        ok, reason, included_in_discovery = _validate_row(
            row, documents_root, manifest.as_of_date
        )
        if not ok:
            quarantine_reasons.append(f"row {row_num}: {reason}")
            continue
        row = dict(row)
        row["included_in_discovery"] = "1" if included_in_discovery else "0"
        row["exclusion_reason"] = "" if included_in_discovery else "future_available_at_excludes_discovery"
        if included_in_discovery:
            included_in_discovery_count += 1
        else:
            future_excluded += 1
        accepted_rows.append(row)

    included = len(accepted_rows)
    seen = len(manifest_rows)

    run_dir = runs.get_run_dir(run_id)
    _write_raw_documents(run_dir, accepted_rows, documents_root)
    return seen, included, included_in_discovery_count, future_excluded, quarantine_reasons
