"""DuckDB cross-run SQL inspection layer (GitHub #25).

Registers run artifacts as DuckDB in-memory views over ``read_parquet()``
globs so analysts can write SQL across runs:

  - Count chunks per run
  - Join edges → chunks for evidence chains
  - Compare company theme exposure across multiple as_of snapshots

Usage example
-------------
  from theme_engine import db

  with db.open_all_runs() as conn:
      rows = conn.execute(
          "SELECT run_id, count(*) AS n FROM v_disc_chunks GROUP BY run_id"
      ).fetchall()

  with db.open_run("run_20240630_120000") as conn:
      rows = conn.execute(
          "SELECT * FROM v_disc_edges LIMIT 10"
      ).fetchall()

INSPECTION-ONLY DISCIPLINE
---------------------------
This module is for POST-HOC SQL inspection/analysis, NOT for discovery-stage
computation.  Discovery computation reads artifacts directly via ``run_cache``,
NOT through this module.

  **CRITICAL**: Discovery-stage modules — graph_build, extraction, exposure,
  themes, entity_resolution, chunking, etc. — MUST NOT import or use ``db.py``.
  Any such import constitutes a leakage violation.

This separation mirrors the global alias table (OI-4) isolation contract: a
cross-run inspection tool must never feed back into per-run discovery joins.

DISCOVERY vs VALIDATION VIEW SEPARATION
-----------------------------------------
Views are registered in two named sets derived from ``leakage.py`` (the
single source of truth for artifact classification):

  Discovery views  — prefix ``v_disc_``
      Read from ``discovery/`` sub-directories.  Safe to query at any time.
      Example: ``v_disc_chunks``, ``v_disc_entities``, ``v_disc_edges``.

  Validation views — prefix ``v_val_``
      Read from ``validation/`` sub-directories.  Contain FUTURE data (market
      prices, realized returns, post-as_of fundamentals).  For POST-HOC
      analysis only — **never** use these in any discovery-stage SQL.
      Example: ``v_val_market_prices``, ``v_val_fundamentals``.

A careless cross-run query that accidentally joins ``v_disc_*`` and ``v_val_*``
views will be explicitly joining two clearly labelled sets, making the leakage
obvious rather than silent.

READ-ONLY DATA ENFORCEMENT
---------------------------
Data read-only is enforced at the DuckDB view level:

  - ``INSERT INTO v_disc_*`` raises CatalogException ("not a table")
  - ``UPDATE v_disc_*`` raises BinderException ("can only update base table")
  - ``DELETE FROM v_disc_*`` raises BinderException ("can only delete from base table")

The underlying Parquet files cannot be written to through DuckDB views.
Users may CREATE temp tables in the in-memory connection (for intermediate
results), but cannot persist data back to the run artifacts.

RUN-ID COLUMN
-------------
Every view exposes a ``run_id`` column as the FIRST column, derived from the
artifact's file path using ``regexp_extract``.  For artifacts whose Parquet
schema already contains a ``run_id`` column, DuckDB automatically renames it
to ``run_id_1`` so there is no ambiguity.

  SELECT run_id        → always the path-derived, canonical run identifier
  SELECT run_id_1      → original run_id embedded in the Parquet row (if any)
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Generator

import duckdb

from .config import REPO_ROOT, settings
from .leakage import DISCOVERY_ARTIFACTS, VALIDATION_ONLY_ARTIFACTS
from .runs import DISCOVERY_DIR, VALIDATION_DIR

# --------------------------------------------------------------------------- #
# Artifact classification — derived from leakage.py (single source of truth) #
# --------------------------------------------------------------------------- #

# Only Parquet artifacts get SQL views; JSON / CSV artifacts are excluded
# because DuckDB's ``read_parquet()`` does not apply to them.
DISCOVERY_PARQUET: frozenset[str] = frozenset(
    a for a in DISCOVERY_ARTIFACTS if a.endswith(".parquet")
)

VALIDATION_PARQUET: frozenset[str] = frozenset(
    a for a in VALIDATION_ONLY_ARTIFACTS if a.endswith(".parquet")
)

# Human-readable sets for tests and diagnostics.
DISCOVERY_VIEW_NAMES: frozenset[str] = frozenset(
    f"v_disc_{a[:-len('.parquet')]}" for a in DISCOVERY_PARQUET
)

VALIDATION_VIEW_NAMES: frozenset[str] = frozenset(
    f"v_val_{a[:-len('.parquet')]}" for a in VALIDATION_PARQUET
)

# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

_DISC_RUN_ID_RE = r"/discovery/"
_VAL_RUN_ID_RE = r"/validation/"

# Regex to extract run_id from a discovery artifact path:
#   .../data/runs/<run_id>/discovery/<artifact>
_RUN_ID_FROM_DISCOVERY = r".*/runs/([^/]+)/discovery/"

# Same for validation artifacts:
_RUN_ID_FROM_VALIDATION = r".*/runs/([^/]+)/validation/"


def _disc_view_name(artifact: str) -> str:
    """Map a discovery artifact filename to its view name."""
    return f"v_disc_{artifact[:-len('.parquet')]}"


def _val_view_name(artifact: str) -> str:
    """Map a validation artifact filename to its view name."""
    return f"v_val_{artifact[:-len('.parquet')]}"


def _register_parquet_view(
    conn: duckdb.DuckDBPyConnection,
    view_name: str,
    glob_pattern: str,
    run_id_regex: str,
) -> bool:
    """Register a DuckDB VIEW over a Parquet glob pattern.

    Extracts ``run_id`` from the file path using ``regexp_extract``.
    The ``run_id`` column is always first; any existing ``run_id`` column in
    the Parquet schema is automatically renamed to ``run_id_1`` by DuckDB.

    Parameters
    ----------
    conn:
        In-memory DuckDB connection to register the view on.
    view_name:
        SQL identifier for the view (e.g. ``v_disc_chunks``).
    glob_pattern:
        Glob pattern passed to ``read_parquet``.  May match zero files if no
        runs have produced that artifact yet.
    run_id_regex:
        Regexp used with ``regexp_extract(filename, run_id_regex, 1)`` to
        extract the run_id component from the full file path.

    Returns
    -------
    bool
        ``True`` if the view was registered, ``False`` if no files matched the
        glob (the view is not created, but no exception is raised).
    """
    sql = (
        f"CREATE OR REPLACE VIEW {view_name} AS\n"
        f"  SELECT\n"
        f"    regexp_extract(filename, '{run_id_regex}', 1) AS run_id,\n"
        f"    * EXCLUDE (filename)\n"
        f"  FROM read_parquet('{glob_pattern}', filename=true, union_by_name=true)"
    )
    try:
        conn.execute(sql)
    except duckdb.IOException:
        # No files matched the glob — the artifact has not been produced yet
        # for any run.  Skip silently; the view will not exist.
        return False
    return True


def _make_connection() -> duckdb.DuckDBPyConnection:
    """Return a fresh in-memory DuckDB connection."""
    return duckdb.connect(":memory:")


def _register_all_views(
    conn: duckdb.DuckDBPyConnection,
    runs_dir: Path,
    run_id: str | None = None,
) -> dict[str, list[str]]:
    """Register discovery and validation views on *conn*.

    Parameters
    ----------
    conn:
        In-memory DuckDB connection.
    runs_dir:
        Base directory containing run sub-directories
        (defaults to ``settings.run_output_dir``).
    run_id:
        When provided, register views scoped to that single run directory.
        When ``None``, register views over all runs using a glob.

    Returns
    -------
    dict with keys ``"discovery"`` and ``"validation"``, each containing a
    list of registered view names.
    """
    if run_id is not None:
        # Single-run: point at the specific run directory
        run_dir = runs_dir / run_id
        disc_glob_tmpl = str(run_dir / DISCOVERY_DIR / "{artifact}")
        val_glob_tmpl = str(run_dir / VALIDATION_DIR / "{artifact}")
    else:
        # All-runs: glob over every run sub-directory
        disc_glob_tmpl = str(runs_dir / "*" / DISCOVERY_DIR / "{artifact}")
        val_glob_tmpl = str(runs_dir / "*" / VALIDATION_DIR / "{artifact}")

    registered: dict[str, list[str]] = {"discovery": [], "validation": []}

    for artifact in sorted(DISCOVERY_PARQUET):
        vname = _disc_view_name(artifact)
        glob = disc_glob_tmpl.format(artifact=artifact)
        if _register_parquet_view(conn, vname, glob, _RUN_ID_FROM_DISCOVERY):
            registered["discovery"].append(vname)

    for artifact in sorted(VALIDATION_PARQUET):
        vname = _val_view_name(artifact)
        glob = val_glob_tmpl.format(artifact=artifact)
        if _register_parquet_view(conn, vname, glob, _RUN_ID_FROM_VALIDATION):
            registered["validation"].append(vname)

    return registered


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def open_run(
    run_id: str,
    *,
    base_dir: Path | None = None,
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Context manager: yield a DuckDB connection with views for one run.

    Views are scoped to ``data/runs/<run_id>/discovery/`` and
    ``data/runs/<run_id>/validation/``.  Any artifact not yet produced is
    silently skipped (its view is not registered).

    Discovery views (``v_disc_*``) are available immediately.
    Validation views (``v_val_*``) are available only if the run is frozen
    and validation artifacts exist on disk.

    Parameters
    ----------
    run_id:
        The run identifier (e.g. ``"run_20240630_120000"``).
    base_dir:
        Override the run output directory.  Defaults to
        ``settings.run_output_dir``.  Useful in tests.

    Yields
    ------
    duckdb.DuckDBPyConnection
        In-memory connection with discovery and validation views registered.
        Data is read-only: INSERT/UPDATE/DELETE into any view will raise a
        DuckDB error.

    Example
    -------
    ::

        with db.open_run("run_20240630_120000") as conn:
            rows = conn.execute(
                "SELECT chunk_id, text FROM v_disc_chunks LIMIT 5"
            ).fetchall()
    """
    runs_dir = base_dir if base_dir is not None else settings.run_output_dir
    conn = _make_connection()
    try:
        _register_all_views(conn, runs_dir, run_id=run_id)
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def open_all_runs(
    *,
    base_dir: Path | None = None,
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Context manager: yield a DuckDB connection with cross-run views.

    Views read from ``data/runs/*/discovery/<artifact>`` (and ``validation/``)
    using Parquet glob patterns.  Each row carries a ``run_id`` column derived
    from the file path.

    Discovery views (``v_disc_*``) span all runs and are safe to query at any
    time.  Validation views (``v_val_*``) span all runs that have produced
    validation artifacts; they are for POST-HOC analysis only and must NOT be
    used in discovery-stage SQL.

    Parameters
    ----------
    base_dir:
        Override the run output directory root.  Defaults to
        ``settings.run_output_dir``.  Useful in tests.

    Yields
    ------
    duckdb.DuckDBPyConnection
        In-memory connection with cross-run discovery and validation views.
        Data is read-only: INSERT/UPDATE/DELETE into any view will raise a
        DuckDB error.

    Example
    -------
    ::

        with db.open_all_runs() as conn:
            # Count chunks per run
            rows = conn.execute(
                \"\"\"
                SELECT run_id, count(*) AS chunk_count
                FROM v_disc_chunks
                GROUP BY run_id
                ORDER BY run_id
                \"\"\"
            ).fetchall()

            # Join edges to chunks for evidence inspection
            rows = conn.execute(
                \"\"\"
                SELECT e.run_id, e.edge_id, e.edge_type,
                       c.chunk_id, c.text
                FROM v_disc_edges AS e
                JOIN v_disc_chunks AS c
                  ON e.run_id = c.run_id
                 AND list_contains(e.evidence_chunk_ids, c.chunk_id)
                LIMIT 20
                \"\"\"
            ).fetchall()
    """
    runs_dir = base_dir if base_dir is not None else settings.run_output_dir
    conn = _make_connection()
    try:
        _register_all_views(conn, runs_dir, run_id=None)
        yield conn
    finally:
        conn.close()


def registered_views(
    run_id: str | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Return the set of views that would be registered for the given scope.

    Convenience helper for diagnostics and tests.  Equivalent to opening a
    connection and querying ``SHOW TABLES``.

    Parameters
    ----------
    run_id:
        When provided, scope to one run.  When ``None``, scope to all runs.
    base_dir:
        Override the run output directory.  Defaults to
        ``settings.run_output_dir``.

    Returns
    -------
    dict with ``"discovery"`` and ``"validation"`` keys, each mapping to a
    sorted list of registered view names.
    """
    runs_dir = base_dir if base_dir is not None else settings.run_output_dir
    conn = _make_connection()
    try:
        result = _register_all_views(conn, runs_dir, run_id=run_id)
    finally:
        conn.close()
    return {k: sorted(v) for k, v in result.items()}
