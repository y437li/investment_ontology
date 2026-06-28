"""LM tone scorer: Loughran-McDonald lexicon loader, chunk scorer, and speaker-role tagger.

Workstream SENT-A (GitHub #99). Substrate for SENT-B (company-level aggregation)
and SENT-C (temporal trending). This module is BACKEND + DATA ONLY; no frontend code.

Design constraints (load-bearing):
- HERMETIC: no network calls; reads only a local CSV and a local YAML config.
- DETERMINISTIC: same text + config + CSV => identical scores.
- CONFIG-DRIVEN: the category set (positive / negative / uncertainty / litigious /
  strong_modal / weak_modal) comes from ``configs/sentiment.yml``, not from
  hardcoded strings here.
- DROP-IN CSV: the loader reads whatever rows are in the CSV so replacing the
  curated subset with the full LM Master Dictionary requires no code change.
- TOKEN-NORMALIZED: tone scores are count / token_count, not raw counts.
- AUDITABILITY: matched_words per category is always returned alongside the scores.

Lexicon source:
  Loughran, T. and McDonald, B. (2011), "When Is a Liability Not a Liability?
  Textual Analysis, Dictionaries, and 10-Ks", Journal of Finance 66(1), pp. 35-65.
  Full dictionary: https://sraf.nd.edu/loughranmcdonald/master-dictionary/
  Committed file: data/lexicons/loughran_mcdonald.csv (curated representative subset).

Key LM vs. Harvard-GI distinction:
  Finance-neutral terms like "liability", "cost", "depreciation" carry NO negative
  flag in the LM dictionary (though they do in generic lists). Tests assert this.

Speaker role:
  Each chunk is tagged with a speaker_role (management | analyst | media | unknown)
  derived from document_type and section_title. The attribution rules come exclusively
  from ``configs/sentiment.yml`` (no hardcoding).

Output contract (chunk-tone artifact):
  See docs/io_contracts.md §S-A and docs/data_schema.md §SENT-A for the full schema
  of ``discovery/chunk_tone.parquet``.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------

# Default paths (relative to repo root, overrideable via env vars).
_DEFAULT_LEXICON_PATH = "data/lexicons/loughran_mcdonald.csv"
_DEFAULT_CONFIG_PATH = "configs/sentiment.yml"

# Tokenisation: strip punctuation, lowercase, split on whitespace.
# Keeps hyphens within words (e.g. "write-off" stays whole) but strips
# surrounding punctuation.
_TOKEN_RE = re.compile(r"[^\w\-]")


def _config_path() -> Path:
    p = os.environ.get("SENTIMENT_CONFIG_PATH", _DEFAULT_CONFIG_PATH)
    return Path(p)


def _lexicon_path_from_env() -> Path | None:
    p = os.environ.get("LEXICON_PATH")
    return Path(p) if p else None


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_sentiment_config(config_path: str | Path | None = None) -> dict:
    """Load sentiment.yml.  Returns the parsed dict.

    Parameters
    ----------
    config_path:
        Path to the config file.  Defaults to ``configs/sentiment.yml``
        (or the ``SENTIMENT_CONFIG_PATH`` env var).
    """
    if config_path is None:
        config_path = _config_path()
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"Sentiment config not found: {p}")
    import yaml  # noqa: PLC0415
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Lexicon loader
# ---------------------------------------------------------------------------

def load_lexicon(
    csv_path: str | Path | None = None,
    config: dict | None = None,
) -> dict[str, dict[str, int]]:
    """Load the Loughran-McDonald lexicon CSV into a word → category-flags dict.

    Parameters
    ----------
    csv_path:
        Path to the LM CSV file.  Resolution order:
        1. Explicit ``csv_path`` argument.
        2. ``lexicon_path`` key in ``config`` (relative to repo root).
        3. ``LEXICON_PATH`` environment variable.
        4. Default: ``data/lexicons/loughran_mcdonald.csv``.
    config:
        Parsed sentiment.yml dict.  Used to resolve ``lexicon_path`` when
        ``csv_path`` is None.

    Returns
    -------
    dict[str, dict[str, int]]
        ``{lowercase_word: {category: 1, ...}}``.
        Only categories with value "1" are stored; 0-value categories are
        omitted to keep memory footprint small for large dictionaries.

    Raises
    ------
    FileNotFoundError
        If the CSV file cannot be located.

    Notes
    -----
    - Lines starting with ``#`` (after stripping) are treated as comments and
      skipped.  This allows SOURCE / LICENSE headers in the CSV.
    - The loader reads whatever rows and columns are present; adding columns or
      rows to the CSV requires no code change.
    - Words are lowercased on load; the scorer also lowercases tokens before
      lookup, so casing in the CSV is irrelevant.
    """
    # Resolve the CSV path.
    if csv_path is not None:
        resolved = Path(csv_path)
    elif config is not None and config.get("lexicon_path"):
        resolved = Path(config["lexicon_path"])
    elif _lexicon_path_from_env() is not None:
        resolved = _lexicon_path_from_env()
    else:
        resolved = Path(_DEFAULT_LEXICON_PATH)

    if not resolved.exists():
        raise FileNotFoundError(f"LM lexicon CSV not found: {resolved}")

    lexicon: dict[str, dict[str, int]] = {}

    with resolved.open(encoding="utf-8", newline="") as fh:
        # Filter comment lines before passing to csv.DictReader.
        non_comment_lines = (
            line for line in fh if not line.lstrip().startswith("#")
        )
        reader = csv.DictReader(non_comment_lines)
        if reader.fieldnames is None:
            return lexicon  # Empty file — still valid (drop-in).

        # Category columns are everything except "word".
        category_cols = [c for c in reader.fieldnames if c and c != "word"]

        for row in reader:
            word = (row.get("word") or "").strip().lower()
            if not word or word.startswith("#"):
                continue  # blank or stray comment token
            flags: dict[str, int] = {}
            for col in category_cols:
                try:
                    val = int(row.get(col, 0) or 0)
                except (ValueError, TypeError):
                    val = 0
                if val:
                    flags[col] = val
            # Store even if all flags are 0 (e.g. finance-neutral anchor words).
            # This makes it possible to assert "liability not in negative" by
            # checking the lexicon entry exists but has no 'negative' flag.
            lexicon[word] = flags

    return lexicon


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation (except intra-word hyphens), split on whitespace.

    This intentionally simple tokeniser matches the spirit of LM (bag-of-words,
    no stemming).  Keeps compound words like "write-off" together so they can
    appear as a single token in the CSV.
    """
    # Replace all non-word-or-hyphen characters with spaces, then lowercase.
    cleaned = _TOKEN_RE.sub(" ", text).lower()
    return [t for t in cleaned.split() if t]


# ---------------------------------------------------------------------------
# Chunk scorer
# ---------------------------------------------------------------------------

def score_chunk(
    text: str,
    token_count: int | None = None,
    *,
    lexicon: dict[str, dict[str, int]] | None = None,
    categories: list[str] | None = None,
    csv_path: str | Path | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Score a text chunk against the LM lexicon.

    Parameters
    ----------
    text:
        The raw chunk text.
    token_count:
        Pre-computed token count from ``chunks.parquet``.  If None (or 0), the
        tokens are counted from ``text`` directly.  Passing the stored count
        ensures the normalisation denominator is consistent with the pipeline.
    lexicon:
        Pre-loaded lexicon dict (from ``load_lexicon``).  Pass this when
        scoring many chunks to avoid reloading the CSV on every call.
        If None, the lexicon is loaded lazily using ``csv_path`` / ``config``.
    categories:
        Ordered list of category names.  Defaults to the list in
        ``configs/sentiment.yml`` (or all columns found in the CSV).
    csv_path:
        Passed to ``load_lexicon`` when ``lexicon`` is None.
    config:
        Parsed sentiment.yml dict.  Used to resolve categories and lexicon path.

    Returns
    -------
    dict with keys:
        ``tone_vector``   — dict[category, float]  token-normalised count
        ``matched_words`` — dict[category, list[str]]  matched tokens (order of occurrence)
        ``token_count``   — int  actual token count used for normalisation
        ``raw_counts``    — dict[category, int]  unnormalised hit counts

    Notes
    -----
    - Token normalisation: score = raw_count / max(token_count, 1).
    - A word appearing N times in the text contributes N to the raw count.
    - matched_words preserves order of first occurrence (duplicates included).
    """
    if lexicon is None:
        if config is None:
            try:
                config = load_sentiment_config()
            except FileNotFoundError:
                config = {}
        lexicon = load_lexicon(csv_path=csv_path, config=config)

    # Determine categories: prefer explicit arg, then config, then CSV columns.
    if categories is None:
        if config and config.get("categories"):
            categories = list(config["categories"])
        else:
            # Infer from lexicon entries.
            all_cats: set[str] = set()
            for flags in lexicon.values():
                all_cats.update(flags.keys())
            categories = sorted(all_cats)

    # Tokenise.
    tokens = _tokenise(text)
    actual_token_count = int(token_count or 0) or len(tokens)

    # Accumulate counts and matched words.
    raw_counts: dict[str, int] = {cat: 0 for cat in categories}
    matched_words: dict[str, list[str]] = {cat: [] for cat in categories}

    for token in tokens:
        flags = lexicon.get(token)
        if flags is None:
            continue
        for cat in categories:
            if flags.get(cat):
                raw_counts[cat] += 1
                matched_words[cat].append(token)

    # Normalise.
    denom = max(actual_token_count, 1)
    tone_vector = {cat: raw_counts[cat] / denom for cat in categories}

    return {
        "tone_vector": tone_vector,
        "matched_words": matched_words,
        "raw_counts": raw_counts,
        "token_count": actual_token_count,
    }


# ---------------------------------------------------------------------------
# Speaker-role tagger
# ---------------------------------------------------------------------------

def tag_speaker_role(
    chunk: dict[str, Any],
    attribution_cfg: list[dict] | None = None,
    config: dict | None = None,
) -> str:
    """Tag the speaker_role for a chunk from its document context.

    Parameters
    ----------
    chunk:
        A dict representing one chunk row, enriched with document context.
        Expected keys (all optional; missing keys are treated as None):
            ``document_type``  — from documents.parquet (e.g. "10-k", "news")
            ``section_title``  — from chunks.parquet (e.g. "MD&A", "Item 7")
            ``block_type``     — from chunks.parquet ("prose" | "table")
    attribution_cfg:
        The parsed ``attribution`` list from sentiment.yml.  Rules are
        evaluated top-to-bottom; the first match wins.  If None, the rules
        are read from ``config`` or from ``configs/sentiment.yml``.
    config:
        Parsed sentiment.yml dict (used when ``attribution_cfg`` is None).

    Returns
    -------
    str
        One of: "management", "analyst", "media", "unknown".

    Attribution logic:
    - A rule matches when EITHER its document_types list OR its section_keywords
      list produces a hit (any match in the respective list suffices).
    - document_type match: case-insensitive substring check.
    - section_keywords match: case-insensitive substring check against section_title.
    - Empty lists (``[]`` or missing) never produce a match for that criterion.
    - A rule with BOTH lists non-empty matches when at least one criterion hits.
    """
    if attribution_cfg is None:
        if config is None:
            try:
                config = load_sentiment_config()
            except FileNotFoundError:
                config = {}
        attribution_cfg = config.get("attribution") or []

    doc_type = (chunk.get("document_type") or "").lower()
    section = (chunk.get("section_title") or "").lower()

    for rule in attribution_cfg:
        role = rule.get("role") or "unknown"
        doc_types = rule.get("document_types") or []
        sec_keywords = rule.get("section_keywords") or []

        # Check document_type match (substring, case-insensitive).
        doc_match = any(dt.lower() in doc_type for dt in doc_types if dt)
        # Check section_title keyword match.
        sec_match = any(kw.lower() in section for kw in sec_keywords if kw)

        if doc_types and sec_keywords:
            # Both lists present: match if EITHER criterion hits.
            if doc_match or sec_match:
                return role
        elif doc_types:
            if doc_match:
                return role
        elif sec_keywords:
            if sec_match:
                return role
        # Rule with neither list → skip (degenerate config).

    return "unknown"


# ---------------------------------------------------------------------------
# Batch scorer (chunk_tone artifact)
# ---------------------------------------------------------------------------

def score_chunks(
    chunks: list[dict[str, Any]],
    *,
    lexicon: dict[str, dict[str, int]] | None = None,
    config: dict | None = None,
    csv_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Score a list of chunk dicts and return chunk-tone rows.

    This is the main entry point for producing ``discovery/chunk_tone.parquet``
    (see docs/io_contracts.md §S-A).

    Parameters
    ----------
    chunks:
        List of chunk dicts (from ``chunks.parquet`` joined with document context).
        Each dict should carry at minimum:
            ``chunk_id``       — stable chunk identifier
            ``document_id``    — document identifier
            ``text``           — chunk text
            ``token_count``    — pre-computed token count (or None)
            ``section_title``  — section heading (may be None)
            ``block_type``     — "prose" | "table"
            ``document_type``  — from documents.parquet (optional but recommended)
            ``available_at``   — point-in-time date (inherited from document)

    Returns
    -------
    list[dict]
        One row per chunk with keys:
            ``chunk_id``        — str
            ``document_id``     — str
            ``available_at``    — str | None
            ``speaker_role``    — str
            ``tone_positive``   — float
            ``tone_negative``   — float
            ``tone_uncertainty``— float
            ``tone_litigious``  — float
            ``tone_strong_modal`` — float
            ``tone_weak_modal`` — float
            ``matched_positive`` — list[str]
            ``matched_negative`` — list[str]
            ``matched_uncertainty`` — list[str]
            ``matched_litigious``   — list[str]
            ``matched_strong_modal`` — list[str]
            ``matched_weak_modal``   — list[str]
            ``token_count``     — int
    """
    if config is None:
        try:
            config = load_sentiment_config()
        except FileNotFoundError:
            config = {}

    if lexicon is None:
        lexicon = load_lexicon(csv_path=csv_path, config=config)

    categories: list[str] = list(config.get("categories") or [])
    attribution_cfg = config.get("attribution") or []

    rows: list[dict[str, Any]] = []

    for chunk in chunks:
        text = chunk.get("text") or ""
        token_count = chunk.get("token_count")

        result = score_chunk(
            text,
            token_count,
            lexicon=lexicon,
            categories=categories if categories else None,
            config=config,
        )
        tone = result["tone_vector"]
        matched = result["matched_words"]
        actual_cats = list(tone.keys())

        row: dict[str, Any] = {
            "chunk_id": chunk.get("chunk_id"),
            "document_id": chunk.get("document_id"),
            "available_at": chunk.get("available_at"),
            "speaker_role": tag_speaker_role(
                chunk, attribution_cfg=attribution_cfg
            ),
            "token_count": result["token_count"],
        }
        # Flatten tone vector and matched words into named columns.
        for cat in actual_cats:
            row[f"tone_{cat}"] = tone.get(cat, 0.0)
            row[f"matched_{cat}"] = matched.get(cat, [])

        rows.append(row)

    return rows
