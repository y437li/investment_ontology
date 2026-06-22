"""SEC EDGAR filings adapter (offline).

Parses a locally stored EDGAR *submissions* JSON (the "recent filings" listing
returned by ``data.sec.gov/submissions/CIK##########.json``) together with the
locally stored filing text files, and emits ``source_manifest.csv`` rows that
are compatible with the existing data-import endpoint
(``data_import.REQUIRED_MANIFEST_COLUMNS``, which includes ``vintage``).

Design constraints (hard project conventions):

- POINT-IN-TIME is rule #1. EDGAR exposes a ``filingDate`` for every filing,
  which is the date the document became knowable to the market. That date maps
  to BOTH ``available_at`` and ``published_at``. We never use a reporting-period
  end date here.
- NO NETWORK. This module only reads local files; it imports no HTTP libraries.
- Deterministic & re-runnable. ``vintage`` (the as-of/retrieval stamp of the
  retrieved source version) is passed in by the caller, not derived from the
  wall clock, so the same inputs always reproduce the same rows.
- Output rows feed ``discovery/`` (raw documents) through the existing import
  endpoint, which writes ``discovery/raw_documents.parquet``.

The submissions JSON is expected to have the EDGAR shape::

    {
      "cik": "0000320193",
      "name": "Apple Inc.",
      "tickers": ["AAPL"],
      "filings": {
        "recent": {
          "accessionNumber":     [...],
          "filingDate":          [...],   # YYYY-MM-DD, -> available_at/published_at
          "form":                [...],   # 10-K, 8-K, ...  -> document_type
          "primaryDocument":     [...],   # local file name under filings_dir
          "primaryDocDescription": [...], # used for title when present
          "reportDate":          [...],   # period end; NOT used as available_at
          ...
        }
      }
    }
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..data_import import REQUIRED_MANIFEST_COLUMNS

# EDGAR is a public-domain US government source.
_DEFAULT_LICENSE = "public-domain"
_DEFAULT_LANGUAGE = "en"
_DEFAULT_CONFIDENTIALITY = "public"
_SEC_BASE_URL = "https://www.sec.gov/Archives/edgar/data"


def _normalize_cik(raw_cik: object) -> str:
    """Return the bare integer CIK as a string (no zero padding, no prefix)."""
    text = str(raw_cik).strip()
    if text.upper().startswith("CIK"):
        text = text[3:]
    text = text.lstrip("0")
    return text or "0"


def _accession_nodash(accession_number: str) -> str:
    """EDGAR archive paths use the accession number without dashes."""
    return accession_number.replace("-", "")


def _source_url(cik: str, accession_number: str, primary_document: str) -> str:
    """Best-effort canonical EDGAR document URL.

    This is a provenance string only; it is never fetched. The import endpoint
    requires ``source_url`` to be non-empty.
    """
    nodash = _accession_nodash(accession_number)
    doc = primary_document or "index.json"
    return f"{_SEC_BASE_URL}/{cik}/{nodash}/{doc}"


def _resolve_raw_path(
    filings_dir: Path,
    primary_document: str,
    accession_number: str,
) -> tuple[str, Path]:
    """Resolve the local filing text file.

    Tries the declared ``primaryDocument`` name first, then a few deterministic
    fallbacks keyed on the accession number, so tiny fixtures using ``.txt``
    stand-ins resolve cleanly. Returns ``(relative_path, absolute_path)`` where
    the relative path is relative to ``filings_dir`` (so it can be combined with
    the import endpoint's ``documents_dir`` root).
    """
    candidates: list[str] = []
    if primary_document:
        candidates.append(primary_document)
        # Common case: fixture stores a .txt next to a declared .htm document.
        stem = Path(primary_document).stem
        candidates.append(f"{stem}.txt")
    nodash = _accession_nodash(accession_number)
    candidates.append(f"{accession_number}.txt")
    candidates.append(f"{nodash}.txt")

    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        abs_path = filings_dir / name
        if abs_path.exists():
            return name, abs_path

    # Nothing on disk: keep the declared name so the import endpoint quarantines
    # the row (its raw_path-existence check) rather than silently dropping it.
    fallback = primary_document or f"{accession_number}.txt"
    return fallback, filings_dir / fallback


def _title_for(
    issuer_name: str,
    form: str,
    description: str,
    report_date: str,
) -> str:
    parts: list[str] = []
    if issuer_name:
        parts.append(issuer_name)
    if form:
        parts.append(form)
    label = description.strip() if description else ""
    if label and label.upper() != form.upper():
        parts.append(label)
    if report_date:
        parts.append(f"({report_date})")
    return " ".join(parts).strip() or (form or "filing")


def build_source_manifest(
    submissions_json_path: str | Path,
    filings_dir: str | Path,
    vintage: str,
) -> list[dict]:
    """Parse an EDGAR submissions JSON into source-manifest rows.

    Parameters
    ----------
    submissions_json_path:
        Path to a local EDGAR submissions JSON file.
    filings_dir:
        Local directory containing the filing text files referenced by the
        submissions JSON. ``raw_path`` values in the returned rows are relative
        to this directory.
    vintage:
        Caller-supplied retrieval / as-of stamp for this batch. Recorded as the
        ``vintage`` of every row so later restatements become new vintages
        rather than overwrites. Must be non-empty for deterministic replay.

    Returns
    -------
    list[dict]
        One dict per recent filing, each containing exactly the columns in
        ``data_import.REQUIRED_MANIFEST_COLUMNS``.
    """
    if not vintage:
        raise ValueError("vintage is required and must be non-empty")

    submissions_path = Path(submissions_json_path)
    filings_root = Path(filings_dir)

    with submissions_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    cik = _normalize_cik(data.get("cik", ""))
    issuer_name = str(data.get("name", "")).strip()
    tickers = data.get("tickers") or []
    company_id = ""
    if isinstance(tickers, list) and tickers:
        company_id = str(tickers[0]).strip()
    if not company_id:
        company_id = f"CIK{cik}" if cik else "UNKNOWN"

    recent = ((data.get("filings") or {}).get("recent")) or {}

    accession_numbers = recent.get("accessionNumber") or []
    filing_dates = recent.get("filingDate") or []
    forms = recent.get("form") or []
    primary_documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []
    report_dates = recent.get("reportDate") or []

    n = len(accession_numbers)

    def _at(seq: list, i: int) -> str:
        if i < len(seq) and seq[i] is not None:
            return str(seq[i]).strip()
        return ""

    rows: list[dict] = []
    for i in range(n):
        accession = _at(accession_numbers, i)
        filing_date = _at(filing_dates, i)
        form = _at(forms, i)
        primary_document = _at(primary_documents, i)
        description = _at(descriptions, i)
        report_date = _at(report_dates, i)

        raw_path, _abs = _resolve_raw_path(filings_root, primary_document, accession)

        notes_bits = [f"cik={cik}", f"accession={accession}"]
        if report_date:
            # Period end is preserved as provenance only; it is intentionally
            # NOT used for available_at (point-in-time rule #1).
            notes_bits.append(f"report_date={report_date}")

        row = {
            "source": "sec_edgar",
            "source_id": accession or f"{cik}-{i}",
            "title": _title_for(issuer_name, form, description, report_date),
            "document_type": form or "filing",
            "company_id": company_id,
            "raw_path": raw_path,
            # filingDate is the date the document became knowable -> both stamps.
            "published_at": filing_date,
            "available_at": filing_date,
            "vintage": vintage,
            "language": _DEFAULT_LANGUAGE,
            "source_url": _source_url(cik, accession, primary_document),
            "license": _DEFAULT_LICENSE,
            "confidentiality": _DEFAULT_CONFIDENTIALITY,
            "notes": "; ".join(notes_bits),
        }
        # Guarantee exactly the required column set, in canonical order.
        rows.append({col: row.get(col, "") for col in REQUIRED_MANIFEST_COLUMNS})

    return rows


def write_source_manifest(rows: list[dict], out_csv_path: str | Path) -> Path:
    """Write manifest rows to a CSV using the required column header.

    The header is exactly ``REQUIRED_MANIFEST_COLUMNS`` so the output is
    directly consumable by the existing ``/api/data/import`` endpoint.
    """
    out_path = Path(out_csv_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=REQUIRED_MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in REQUIRED_MANIFEST_COLUMNS})
    return out_path
