#!/usr/bin/env python3
"""Operator tool: fetch point-in-time-clean news from Alpha Vantage NEWS_SENTIMENT
into data/inputs/documents/news/<ticker>/, plus a combined source manifest.

POINT-IN-TIME: each article's time_published is used as available_at; only
articles with time_published <= AS_OF are kept. Reads ALPHAVANTAGE_API_KEY from
the environment (load .env first). Writes only to data/inputs/ (gitignored).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "inputs" / "documents" / "news"
VINTAGE = "2026-06-22T00:00:00Z"
AS_OF = "2024-06-30"
TIME_FROM, TIME_TO = "20240101T0000", "20240630T2359"
TICKERS = ["SU", "ENB", "CNQ", "RY", "BNS"]
MAX_PER_TICKER = 50
RELEVANCE_MIN = 0.3  # drop tangential multi-tag mentions
# Auto-generated price-move boilerplate (no narrative value).
_BOILERPLATE = re.compile(
    r"stock (rises|falls|gains|drops|climbs|declines|jumps|sinks).*(Monday|Tuesday|"
    r"Wednesday|Thursday|Friday)|(under|out)performs market",
    re.IGNORECASE,
)

MANIFEST_COLUMNS = [
    "source", "source_id", "title", "document_type", "company_id", "raw_path",
    "published_at", "available_at", "vintage", "language", "source_url",
    "license", "confidentiality", "notes",
]


def _key() -> str:
    k = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not k:
        raise SystemExit("ALPHAVANTAGE_API_KEY not set (run: set -a && source .env && set +a)")
    return k


def _fetch(ticker: str, key: str) -> list[dict]:
    q = urllib.parse.urlencode({
        "function": "NEWS_SENTIMENT", "tickers": ticker,
        "time_from": TIME_FROM, "time_to": TIME_TO,
        "limit": MAX_PER_TICKER, "sort": "EARLIEST", "apikey": key,
    })
    with urllib.request.urlopen(f"https://www.alphavantage.co/query?{q}", timeout=30) as r:
        return json.load(r).get("feed", [])


def _to_date(t: str) -> str:  # 20240606T132146 -> 2024-06-06
    return f"{t[0:4]}-{t[4:6]}-{t[6:8]}"


def main() -> None:
    key = _key()
    shutil.rmtree(OUT, ignore_errors=True)  # fresh collection
    rows: list[dict] = []
    for ticker in TICKERS:
        feed = _fetch(ticker, key)
        (OUT / ticker).mkdir(parents=True, exist_ok=True)
        kept = dropped = 0
        for art in feed:
            pub = _to_date(art["time_published"])
            if pub > AS_OF:  # point-in-time guard
                continue
            rs = next((float(t["relevance_score"]) for t in art.get("ticker_sentiment", [])
                       if t["ticker"] == ticker), 0.0)
            if rs < RELEVANCE_MIN or _BOILERPLATE.search(art["title"]):
                dropped += 1
                continue
            doc_id = hashlib.sha256(art["url"].encode()).hexdigest()[:16]
            text = f"{art['title']}\n\n{art.get('summary', '')}"
            rel = f"data/inputs/documents/news/{ticker}/{doc_id}.txt"
            (REPO / rel).write_text(text, encoding="utf-8")
            rows.append({
                "source": art.get("source", "AlphaVantage"), "source_id": doc_id,
                "title": art["title"][:300], "document_type": "news",
                "company_id": ticker, "raw_path": rel,
                "published_at": pub, "available_at": pub, "vintage": VINTAGE,
                "language": "en", "source_url": art["url"],
                "license": "news", "confidentiality": "public",
                "notes": f"sentiment={art.get('overall_sentiment_label', '')};relevance={rs:.3f}",
            })
            kept += 1
        print(f"  {ticker}: kept {kept}, dropped {dropped} (relevance<{RELEVANCE_MIN}/boilerplate)")
        time.sleep(15)  # free tier: 5 req/min

    man = OUT / "source_manifest.csv"
    with man.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} news docs -> {OUT}\nmanifest: {man}")


if __name__ == "__main__":
    main()
