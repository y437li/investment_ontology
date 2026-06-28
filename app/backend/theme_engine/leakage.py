"""Single-source artifact classification for leakage prevention (OI-3).

Defines which run artifacts are DISCOVERY-readable vs VALIDATION-only, and
the ``LeakageError`` raised when the boundary is violated.

This module is the SINGLE SOURCE OF TRUTH for artifact classification. It is
imported by:
  - the runtime read guard in ``run_cache.py``
  - the end-to-end gate tests in ``tests/backend/test_oi3_pipeline_leakage.py``

Design
------
Discovery artifacts (``discovery/`` directory):
    Produced during the discovery stage (extraction, graph build, themes,
    exposure, etc.).  Readable at any pipeline stage.  All must be frozen
    + hashed before validation reads future data.

Validation-only artifacts (``validation/`` directory):
    Contain FUTURE data (market prices, realized returns, post-as_of
    fundamentals).  May only be read AFTER ``discovery_frozen=True``.
    Discovery-stage modules MUST NOT import or read these paths.

Spec references:
  - theme_discovery_engine_v1.md §16 (Leakage Prevention)
  - theme_discovery_engine_v1.md §18 (Discovery vs Validation)
  - docs/io_contracts.md §20 (fundamentals.parquet — validation-only)
  - docs/io_contracts.md §19 (market_prices.parquet)
  - docs/io_contracts.md §FI-E (projection_scores.parquet)
"""

from __future__ import annotations

from pathlib import Path


# --------------------------------------------------------------------------- #
# Exception                                                                   #
# --------------------------------------------------------------------------- #


class LeakageError(PermissionError):
    """Raised when a validation-only artifact is read before discovery is frozen.

    Inherits from ``PermissionError`` so callers can catch either.
    """


# --------------------------------------------------------------------------- #
# Discovery artifacts                                                         #
# --------------------------------------------------------------------------- #

# All artifacts produced by the discovery stage.  They live under the
# ``discovery/`` sub-directory of a run dir and are readable at any stage.
DISCOVERY_ARTIFACTS: frozenset[str] = frozenset({
    # Raw ingestion (M1–M2)
    "raw_documents.parquet",
    "documents.parquet",
    "document_cleaning_log.parquet",
    # Chunking (M3)
    "chunks.parquet",
    # Entity / edge extraction (M3–M4)
    "entities.parquet",
    "entity_aliases.parquet",
    "entity_aliases_global.parquet",       # optional (EG-A)
    "edges.parquet",
    "edge_explanations.parquet",           # optional
    # Graph (M4)
    "graph.json",
    # Theme discovery (M4)
    "communities.json",
    "theme_snapshots.json",
    "theme_metrics.parquet",
    "theme_lineage.json",                  # optional
    # Exposure (M5)
    "company_theme_exposure.parquet",
    # Provenance (EG-E)
    "entity_chunk_provenance.parquet",
    "theme_document_evidence.parquet",
    "company_theme_document_evidence.parquet",
    # Fundamentals ingestion (discovery-time, EG-B)
    "fundamentals_asreported.parquet",     # §20a — PIT-clean discovery artifact
    "financial_metrics.parquet",           # §20b — B2 LLM-derived metrics
    "financial_metric_edges.parquet",      # §20c — FinancialMetric edges
    # Management sentiment (SENT-B/C)
    "management_sentiment.parquet",
    "sentiment_edges.parquet",
    "management_sentiment_fused.parquet",
    # Forward inference (FI-C)
    "projected_impacts.parquet",
})

# Discovery artifacts that carry a ``available_at`` (or equivalent PIT date)
# column whose values must all be <= run.as_of_date.
# Mapping: artifact_filename -> date_column_name.
DISCOVERY_DATED_COLUMNS: dict[str, str] = {
    "raw_documents.parquet": "available_at",
    "documents.parquet": "available_at",
    "chunks.parquet": "available_at",
    "entities.parquet": "first_seen_at",
    "edges.parquet": "first_seen_at",
    "entity_chunk_provenance.parquet": "available_at",
    "fundamentals_asreported.parquet": "available_at",
    "financial_metrics.parquet": "available_at",
    "management_sentiment_fused.parquet": "available_at",
}


# --------------------------------------------------------------------------- #
# Validation-only artifacts                                                   #
# --------------------------------------------------------------------------- #

# All artifacts that contain FUTURE (post-as_of) data.  They live under the
# ``validation/`` sub-directory of a run dir and must NOT be read before
# ``discovery_frozen=True``.
VALIDATION_ONLY_ARTIFACTS: frozenset[str] = frozenset({
    "fundamentals.parquet",       # io_contracts §20 — validation-only walk-forward fundamentals
    "market_prices.parquet",      # io_contracts §19 — future realized prices
    "projection_scores.parquet",  # io_contracts §FI-E — post-freeze scoring output
    "portfolio_baskets.parquet",  # validation portfolio construction
    "validation.csv",             # validation summary
})


# --------------------------------------------------------------------------- #
# Guards                                                                      #
# --------------------------------------------------------------------------- #


def is_validation_path(path: Path) -> bool:
    """Return ``True`` if *path* resolves to a ``validation/`` sub-directory.

    Checks the resolved path parts to avoid false positives from paths that
    happen to contain the word "validation" in a directory name prefix.
    """
    return "validation" in path.resolve().parts


def assert_read_allowed(path: Path, discovery_frozen: bool) -> None:
    """Raise ``LeakageError`` if *path* is validation-only and discovery is not frozen.

    This is the one-way boundary: validation-only artifacts may only be read
    after ``discovery_frozen=True``.  Discovery reads are never blocked.

    Parameters
    ----------
    path:
        Absolute path of the artifact being read.
    discovery_frozen:
        Value of ``run_manifest.discovery_frozen`` for the owning run.

    Raises
    ------
    LeakageError
        If *path* is under a ``validation/`` directory and
        ``discovery_frozen`` is ``False``.
    """
    if is_validation_path(path) and not discovery_frozen:
        raise LeakageError(
            f"Leakage guard: cannot read validation artifact {path.name!r} "
            "before discovery is frozen (discovery_frozen=True required). "
            "Run POST /api/discovery/freeze first, then retry."
        )
