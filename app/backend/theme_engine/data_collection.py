"""Raw document collection pipeline.

Collects source material from local files or URLs and materializes:
- immutable source copies under data inputs corpus
- a manifest CSV suitable for the existing /api/data/import stage

This module keeps only deterministic, auditable actions and writes
schema-compatible metadata for downstream run ingestion.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException

from .config import REPO_ROOT

REQUIRED_SOURCE_SPEC_COLUMNS = (
    "source",
    "source_id",
    "title",
    "document_type",
    "published_at",
    "available_at",
    "source_vintage",
)

REQUIRED_SOURCE_VALUES = (
    "source",
    "source_id",
    "title",
    "document_type",
    "published_at",
    "available_at",
    "source_vintage",
)

REQUIRED_MANIFEST_COLUMNS = (
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
)

COLLECTION_REPORT_NAME = "data_collection_report.json"
HTTP_USER_AGENT = "theme-discovery-collection/0.1 (+local-pipeline)"


def _resolve_path(path_text: str) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _safe_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return token or "item"


def _parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[:10][0].isdigit():
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt)
            except Exception:
                pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _sha256_bytes(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def _read_spec_rows(spec_path: Path) -> list[dict[str, str]]:
    if not spec_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"collection spec not found: {spec_path}",
        )

    with spec_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None:
            raise HTTPException(status_code=422, detail="collection spec missing header row")

        missing_columns = [
            c for c in REQUIRED_SOURCE_SPEC_COLUMNS if c not in reader.fieldnames
        ]
        if missing_columns:
            raise HTTPException(
                status_code=422,
                detail=f"collection spec missing required columns: {missing_columns}",
            )

        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def _row_source(row: dict[str, str]) -> tuple[bool, str, str]:
    source_file = row.get("source_file", "").strip()
    source_url = row.get("source_url", "").strip()
    if not source_file and not source_url:
        return False, "", "missing source_file and source_url"
    if source_file:
        return True, "source_file", source_file
    return True, "source_url", source_url


def _read_source_file(source_file: str, spec_dir: Path) -> tuple[bytes, str]:
    resolved = Path(source_file)
    if not resolved.is_absolute():
        resolved = spec_dir / resolved
    if not resolved.exists():
        raise ValueError(f"source_file not found: {source_file}")
    payload = resolved.read_bytes()
    if not payload:
        raise ValueError(f"source_file empty: {source_file}")
    return payload, resolved.name


def _read_source_url(source_url: str, timeout_seconds: int = 20) -> tuple[bytes, str]:
    request = Request(source_url, headers={"User-Agent": HTTP_USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
    except urllib.error.URLError as exc:
        raise ValueError(f"failed to fetch source_url: {source_url}: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"failed to fetch source_url: {source_url}: {exc}") from exc

    if not payload:
        raise ValueError(f"empty payload from source_url: {source_url}")

    parsed = urlparse(source_url)
    filename = Path(parsed.path or "").name or "download.bin"
    return payload, filename


def _build_dest_path(
    documents_root: Path,
    row: dict[str, str],
    content_hash: str,
    source_filename: str,
) -> Path:
    source = _safe_token(row["source"] or "source")
    source_id = _safe_token(row["source_id"] or "unknown")
    vintage = _safe_token(row.get("source_vintage") or "vintage")
    available_at = _parse_timestamp(row["available_at"])
    date_part = available_at.date().isoformat().replace("-", "/") if available_at else "unknown-date"

    stem = _safe_token(Path(source_filename).stem)[:32]
    ext = Path(source_filename).suffix or ".txt"
    hashed = content_hash[:12]
    filename = f"{stem}__{hashed}{ext}"
    return documents_root / source / source_id / vintage / date_part / filename


def _write_report(report_dir: Path, payload: dict[str, object], run_id: str | None) -> Path:
    if run_id:
        report_dir = report_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / COLLECTION_REPORT_NAME
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report_path


def _write_manifest(manifest_path: Path, manifest_rows: list[dict[str, str]], append: bool) -> None:
    if manifest_path.exists() and append:
        existing: list[dict[str, str]] = []
        with manifest_path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            if reader.fieldnames:
                for row in reader:
                    existing.append(dict(row))
        manifest_rows = existing + manifest_rows

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=REQUIRED_MANIFEST_COLUMNS)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow({col: row.get(col, "") for col in REQUIRED_MANIFEST_COLUMNS})


def collect_sources(
    spec_path: str,
    documents_dir: str = "data/inputs/documents",
    source_manifest_path: str = "data/inputs/documents/source_manifest.csv",
    run_id: str | None = None,
    append_manifest: bool = False,
) -> tuple[int, int, int, int, list[str], str, str]:
    spec_file = _resolve_path(spec_path)
    spec_rows = _read_spec_rows(spec_file)

    seen = len(spec_rows)
    collected_rows: list[dict[str, str]] = []
    quarantine_reasons: list[str] = []

    documents_root = _resolve_path(documents_dir)
    documents_root.mkdir(parents=True, exist_ok=True)
    manifest_path = _resolve_path(source_manifest_path)
    spec_dir = spec_file.parent

    for row_num, row in enumerate(spec_rows, start=1):
        try:
            for col in REQUIRED_SOURCE_VALUES:
                if not row.get(col):
                    raise ValueError(f"missing required column value: {col}")

            published_at = _parse_timestamp(row["published_at"])
            available_at = _parse_timestamp(row["available_at"])
            if published_at is None:
                raise ValueError("invalid date format in published_at")
            if available_at is None:
                raise ValueError("invalid date format in available_at")
            if published_at > available_at:
                raise ValueError("published_at must be <= available_at")

            has_source, source_mode, source_uri = _row_source(row)
            if not has_source:
                raise ValueError(source_uri)

            if source_mode == "source_file":
                payload, incoming_name = _read_source_file(source_uri, spec_dir)
            else:
                payload, incoming_name = _read_source_url(source_uri)

            content_hash = _sha256_bytes(payload)
            target = _build_dest_path(documents_root, row, content_hash, incoming_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

            relative_raw_path = target.relative_to(documents_root).as_posix()
            collected_rows.append(
                {
                    "source": row.get("source", ""),
                    "source_id": row.get("source_id", ""),
                    "title": row.get("title", ""),
                    "document_type": row.get("document_type", ""),
                    "company_id": row.get("company_id", ""),
                    "raw_path": relative_raw_path,
                    "published_at": row.get("published_at", ""),
                    "available_at": row.get("available_at", ""),
                    "vintage": row.get("source_vintage", ""),
                    "language": row.get("language", ""),
                    "source_url": source_uri,
                    "license": row.get("license", ""),
                    "confidentiality": row.get("confidentiality", ""),
                    "notes": row.get("notes", ""),
                }
            )
        except Exception as exc:
            quarantine_reasons.append(f"row {row_num}: {exc}")

    _write_manifest(manifest_path, collected_rows, append=append_manifest)
    sources_collected = len(collected_rows)
    summary = {
        "ok": not quarantine_reasons,
        "source_spec": str(spec_file),
        "documents_dir": str(documents_root),
        "source_manifest_path": str(manifest_path),
        "sources_seen": seen,
        "sources_collected": sources_collected,
        "sources_quarantined": len(quarantine_reasons),
        "quarantine_reasons": quarantine_reasons,
    }
    report_path = _write_report(documents_root, summary, run_id)

    return (
        seen,
        sources_collected,
        len(quarantine_reasons),
        sources_collected,
        quarantine_reasons,
        str(manifest_path),
        str(report_path),
    )
