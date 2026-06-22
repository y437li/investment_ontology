#!/usr/bin/env python3
"""One-off operator tool: fetch a small point-in-time-clean batch of SEC EDGAR
filings into data/inputs/documents/<ticker>/.

POINT-IN-TIME: only filings with filingDate <= AS_OF are kept (filingDate is the
date the document became public = available_at; historical filings are immutable).
This is collection tooling, NOT part of the hermetic pipeline — it lives under
scripts/ and writes only to data/inputs/ (gitignored).
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "inputs" / "documents"
UA = "investment_ontology research y437landis@gmail.com"

AS_OF = "2024-06-30"
FORMS = {"6-K", "40-F", "8-K", "10-K", "20-F"}
MAX_PER_COMPANY = 3

# (ticker, cik) — EDGAR-registered TSX 60 names, banks + energy.
COMPANIES = [
    ("RY", "0001000275"),
    ("BNS", "0000009631"),
    ("SU", "0000311337"),
    ("CNQ", "0001017413"),
    ("ENB", "0000895728"),
]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _cik_int(cik: str) -> str:
    return str(int(cik))


def fetch_company(ticker: str, cik: str) -> dict:
    subs = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    recent = subs["filings"]["recent"]
    cols = ("accessionNumber", "filingDate", "form", "primaryDocument")
    rows = list(zip(*(recent[c] for c in cols)))

    selected = []
    for acc, fdate, form, prim in rows:
        if fdate <= AS_OF and form in FORMS and prim:
            selected.append((acc, fdate, form, prim))
        if len(selected) >= MAX_PER_COMPANY:
            break

    comp_dir = OUT / ticker
    comp_dir.mkdir(parents=True, exist_ok=True)
    kept = {"accessionNumber": [], "filingDate": [], "form": [], "primaryDocument": []}
    for acc, fdate, form, prim in selected:
        acc_nodash = acc.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{_cik_int(cik)}/{acc_nodash}/{prim}"
        try:
            data = _get(url)
        except Exception as e:  # skip a doc that 404s; keep the batch moving
            print(f"  ! {ticker} {form} {prim}: {e}")
            continue
        (comp_dir / prim).write_bytes(data)
        for k, v in zip(cols, (acc, fdate, form, prim)):
            kept[k].append(v)
        print(f"  + {ticker} {form} {fdate} {prim} ({len(data)//1024} KB)")
        time.sleep(0.3)  # be polite to EDGAR

    # Write a trimmed submissions JSON (same EDGAR shape) for the adapter.
    trimmed = {"cik": cik, "name": subs.get("name", ticker), "filings": {"recent": kept}}
    (comp_dir / "submissions.json").write_text(json.dumps(trimmed, indent=2))
    return {"ticker": ticker, "name": subs.get("name"), "kept": len(kept["form"])}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Fetching EDGAR filings (filingDate <= {AS_OF}) for {len(COMPANIES)} companies")
    summary = [fetch_company(t, c) for t, c in COMPANIES]
    total = sum(s["kept"] for s in summary)
    print(f"\nDone: {total} filings across {len(summary)} companies -> {OUT}")
    for s in summary:
        print(f"  {s['ticker']:5} {s['kept']} filings  ({s['name']})")


if __name__ == "__main__":
    main()
