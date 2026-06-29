"""DuckDB view layer tests (GitHub #25).

Acceptance criteria verified:
  1. Cross-run SQL: count chunks per run, join edges→chunks; rows have run_id.
  2. Read-only: INSERT/UPDATE/DELETE into any view raises a DuckDB error.
  3. Separate view sets: discovery views never surface validation rows.
  4. Source-scan: no discovery-stage module imports db.py.
  5. Hermetic, fixture-backed, no network access.

All tests use a tmp_path-scoped fixture that writes tiny Parquet files in the
``runs/<run_id>/discovery/`` and ``runs/<run_id>/validation/`` layout.
"""

from __future__ import annotations

import ast
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine import db
from theme_engine.db import (
    DISCOVERY_VIEW_NAMES,
    VALIDATION_VIEW_NAMES,
    open_all_runs,
    open_run,
)

# ---------------------------------------------------------------------------
# Discovery-stage modules — no import of db.py is permitted
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THEME_ENGINE = _REPO_ROOT / "app" / "backend" / "theme_engine"

# Modules that participate in the per-run discovery pipeline.  Adding db.py
# to any of these is a leakage violation.
_DISCOVERY_STAGE_MODULES = [
    "graph_build.py",
    "extraction.py",
    "exposure.py",
    "themes.py",
    "entity_resolution.py",
    "chunking.py",
    "run_cache.py",
    "propagation.py",
    "artifacts.py",
    "slice_engine.py",
    "theme_levels.py",
    "theme_relevance.py",
    "theme_hierarchy.py",
    "concept_resolution.py",
    "registry.py",
    "freeze.py",
    "walk_forward.py",
    "runs.py",
    "source.py",
    "integrity.py",
    "provenance.py",
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_parquet(path: Path, rows: list[dict]) -> None:
    """Write rows to a Parquet file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        pq.write_table(pa.table({}), path)
        return
    names = list(rows[0].keys())
    arrays: dict[str, pa.Array] = {}
    for col in names:
        values = [r.get(col) for r in rows]
        non_null = [v for v in values if v is not None]
        if non_null and isinstance(non_null[0], float):
            arrays[col] = pa.array(values, type=pa.float64())
        elif non_null and isinstance(non_null[0], int):
            arrays[col] = pa.array(values, type=pa.int64())
        elif non_null and isinstance(non_null[0], list):
            arrays[col] = pa.array(values, type=pa.list_(pa.string()))
        else:
            arrays[col] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )
    pq.write_table(pa.table(arrays), path)


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    """Build a minimal multi-run directory tree under tmp_path.

    Two runs are created, each with:
      - discovery/chunks.parquet   (2 rows)
      - discovery/entities.parquet (1 row)
      - discovery/edges.parquet    (1 row, with evidence_chunk_ids)
      - discovery/company_theme_exposure.parquet (1 row)
      - validation/market_prices.parquet (1 row, VALIDATION-ONLY)

    The fixture-based runs are isolated under tmp_path; they never touch the
    real data/runs/ directory.
    """
    base = tmp_path / "runs"

    for run_id in ("run_alpha", "run_beta"):
        disc = base / run_id / "discovery"
        val = base / run_id / "validation"

        # --- discovery/chunks.parquet ---
        _write_parquet(disc / "chunks.parquet", [
            {"chunk_id": f"{run_id}_c1", "document_id": "doc_001",
             "text": f"{run_id} chunk one", "available_at": "2024-01-10"},
            {"chunk_id": f"{run_id}_c2", "document_id": "doc_001",
             "text": f"{run_id} chunk two", "available_at": "2024-01-15"},
        ])

        # --- discovery/entities.parquet ---
        _write_parquet(disc / "entities.parquet", [
            {"entity_id": f"{run_id}_e1", "entity_type": "Company",
             "name": "Hydro One", "confidence": 0.9},
        ])

        # --- discovery/edges.parquet ---
        _write_parquet(disc / "edges.parquet", [
            {"edge_id": f"{run_id}_edge1", "source_entity_id": f"{run_id}_e1",
             "target_entity_id": f"{run_id}_e2", "edge_type": "exposed_to",
             "confidence": 0.8,
             "evidence_chunk_ids": [f"{run_id}_c1", f"{run_id}_c2"]},
        ])

        # --- discovery/company_theme_exposure.parquet ---
        _write_parquet(disc / "company_theme_exposure.parquet", [
            {"company_id": f"{run_id}_co1", "theme_snapshot_id": "theme_001",
             "exposure_score": 0.7, "as_of_date": "2024-06-30"},
        ])

        # --- validation/market_prices.parquet (FUTURE DATA) ---
        _write_parquet(val / "market_prices.parquet", [
            {"company_id": f"{run_id}_co1", "price_date": "2024-09-30",
             "close": 52.50, "run_id": run_id},
        ])

    return base


@pytest.fixture()
def single_run_dir(tmp_path: Path) -> tuple[str, Path]:
    """Build a single-run directory.  Returns (run_id, base_dir)."""
    run_id = "run_single"
    base = tmp_path / "runs"
    disc = base / run_id / "discovery"

    _write_parquet(disc / "chunks.parquet", [
        {"chunk_id": "single_c1", "text": "single run text",
         "available_at": "2024-01-01"},
        {"chunk_id": "single_c2", "text": "another chunk",
         "available_at": "2024-01-02"},
    ])
    _write_parquet(disc / "entities.parquet", [
        {"entity_id": "single_e1", "entity_type": "Company",
         "name": "Brookfield", "confidence": 0.95},
    ])

    return run_id, base


# ---------------------------------------------------------------------------
# 1. Cross-run SQL with run_id column
# ---------------------------------------------------------------------------


class TestCrossRunSQL:
    """Open a multi-run connection and verify SQL returns correct rows."""

    def test_chunk_count_per_run(self, runs_dir: Path) -> None:
        """Count rows per run — each run has exactly 2 chunks."""
        with open_all_runs(base_dir=runs_dir) as conn:
            rows = conn.execute(
                "SELECT run_id, count(*) AS n FROM v_disc_chunks GROUP BY run_id ORDER BY run_id"
            ).fetchall()

        assert len(rows) == 2
        run_ids = {r[0] for r in rows}
        assert "run_alpha" in run_ids
        assert "run_beta" in run_ids
        counts = {r[0]: r[1] for r in rows}
        assert counts["run_alpha"] == 2
        assert counts["run_beta"] == 2

    def test_run_id_column_is_first(self, runs_dir: Path) -> None:
        """The first column of every discovery view must be run_id (path-derived)."""
        with open_all_runs(base_dir=runs_dir) as conn:
            row = conn.execute(
                "SELECT * FROM v_disc_chunks LIMIT 1"
            ).fetchone()
        # First value should be a known run_id string
        assert row[0] in ("run_alpha", "run_beta"), f"First column is not run_id: {row}"

    def test_run_id_is_canonical_path_derived(self, runs_dir: Path) -> None:
        """run_id values are derived from the directory path, not artifact content."""
        with open_all_runs(base_dir=runs_dir) as conn:
            run_ids = {
                r[0]
                for r in conn.execute("SELECT DISTINCT run_id FROM v_disc_chunks").fetchall()
            }
        assert run_ids == {"run_alpha", "run_beta"}

    def test_edge_join_chunks_for_evidence(self, runs_dir: Path) -> None:
        """Cross-run join: edges joined to chunks via evidence_chunk_ids."""
        with open_all_runs(base_dir=runs_dir) as conn:
            rows = conn.execute(
                """
                SELECT e.run_id, e.edge_id, c.chunk_id, c.text
                FROM v_disc_edges AS e
                JOIN v_disc_chunks AS c
                  ON e.run_id = c.run_id
                 AND list_contains(e.evidence_chunk_ids, c.chunk_id)
                ORDER BY e.run_id, c.chunk_id
                """
            ).fetchall()

        # 2 runs × 1 edge × 2 evidence chunks = 4 rows
        assert len(rows) == 4
        for row in rows:
            run_id, edge_id, chunk_id, text = row
            assert run_id in ("run_alpha", "run_beta")
            assert edge_id.startswith(run_id)
            assert chunk_id.startswith(run_id)

    def test_single_run_views_scoped_to_one_run(self, single_run_dir: tuple) -> None:
        """open_run() returns only rows from the specified run."""
        run_id, base = single_run_dir
        with open_run(run_id, base_dir=base) as conn:
            rows = conn.execute(
                "SELECT run_id, chunk_id FROM v_disc_chunks ORDER BY chunk_id"
            ).fetchall()

        assert len(rows) == 2
        assert all(r[0] == "run_single" for r in rows)
        chunk_ids = {r[1] for r in rows}
        assert chunk_ids == {"single_c1", "single_c2"}

    def test_entity_count_per_run(self, runs_dir: Path) -> None:
        """Each run has exactly 1 entity in the fixture."""
        with open_all_runs(base_dir=runs_dir) as conn:
            rows = conn.execute(
                "SELECT run_id, count(*) AS n FROM v_disc_entities GROUP BY run_id ORDER BY run_id"
            ).fetchall()
        assert len(rows) == 2
        assert all(r[1] == 1 for r in rows)

    def test_exposure_cross_run(self, runs_dir: Path) -> None:
        """company_theme_exposure view spans both runs."""
        with open_all_runs(base_dir=runs_dir) as conn:
            rows = conn.execute(
                "SELECT run_id, exposure_score FROM v_disc_company_theme_exposure ORDER BY run_id"
            ).fetchall()
        assert len(rows) == 2
        run_ids = {r[0] for r in rows}
        assert "run_alpha" in run_ids and "run_beta" in run_ids


# ---------------------------------------------------------------------------
# 2. Read-only enforcement
# ---------------------------------------------------------------------------


class TestReadOnly:
    """Views are read-only: data cannot be modified via INSERT/UPDATE/DELETE."""

    def test_insert_into_discovery_view_fails(self, runs_dir: Path) -> None:
        """INSERT into a discovery view raises a DuckDB error."""
        with open_all_runs(base_dir=runs_dir) as conn:
            with pytest.raises(Exception) as exc_info:
                conn.execute(
                    "INSERT INTO v_disc_chunks VALUES ('injected_run', 'hack_c1', 'doc_x', 'pwned', '2024-01-01')"
                )
        # DuckDB raises CatalogException for INSERT into a view
        assert "v_disc_chunks" in str(exc_info.value) or "not an" in str(exc_info.value).lower()

    def test_update_discovery_view_fails(self, runs_dir: Path) -> None:
        """UPDATE a discovery view raises a DuckDB BinderException."""
        with open_all_runs(base_dir=runs_dir) as conn:
            with pytest.raises(Exception) as exc_info:
                conn.execute("UPDATE v_disc_chunks SET text = 'hacked'")
        assert "base table" in str(exc_info.value).lower() or "view" in str(exc_info.value).lower()

    def test_delete_from_discovery_view_fails(self, runs_dir: Path) -> None:
        """DELETE from a discovery view raises a DuckDB BinderException."""
        with open_all_runs(base_dir=runs_dir) as conn:
            with pytest.raises(Exception) as exc_info:
                conn.execute("DELETE FROM v_disc_chunks WHERE run_id = 'run_alpha'")
        assert "base table" in str(exc_info.value).lower() or "view" in str(exc_info.value).lower()

    def test_insert_into_validation_view_fails(self, runs_dir: Path) -> None:
        """INSERT into a validation view raises a DuckDB error."""
        with open_all_runs(base_dir=runs_dir) as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO v_val_market_prices VALUES ('injected_run', 'co_x', '2025-01-01', 999.0)"
                )

    def test_read_select_still_works_after_failed_write(self, runs_dir: Path) -> None:
        """After a failed write attempt the connection still serves reads."""
        with open_all_runs(base_dir=runs_dir) as conn:
            try:
                conn.execute("INSERT INTO v_disc_chunks VALUES ('r', 'c', 'd', 't', '2024-01-01')")
            except Exception:
                pass
            # SELECT must still work
            rows = conn.execute("SELECT count(*) FROM v_disc_chunks").fetchone()
            assert rows[0] == 4  # 2 runs × 2 chunks


# ---------------------------------------------------------------------------
# 3. Discovery and validation view separation
# ---------------------------------------------------------------------------


class TestDiscoveryValidationSeparation:
    """Discovery views NEVER surface validation rows; view sets are distinct."""

    def test_discovery_views_do_not_contain_validation_data(self, runs_dir: Path) -> None:
        """Querying any discovery view must not return rows from validation/."""
        with open_all_runs(base_dir=runs_dir) as conn:
            # market_prices only exist in validation/ — must not appear in discovery views
            # The company_id in our fixture is "{run_id}_co1"
            chunk_rows = conn.execute("SELECT * FROM v_disc_chunks").fetchall()
            entity_rows = conn.execute("SELECT * FROM v_disc_entities").fetchall()

        # Confirm no validation-only data bleeds into discovery views
        for r in chunk_rows + entity_rows:
            # validation/market_prices has price_date and close columns;
            # these should not appear in discovery chunk/entity views
            assert "price_date" not in str(r)
            assert "52.50" not in str(r)

    def test_validation_view_registered_separately(self, runs_dir: Path) -> None:
        """Validation views exist and can be queried independently."""
        with open_all_runs(base_dir=runs_dir) as conn:
            rows = conn.execute(
                "SELECT run_id, company_id FROM v_val_market_prices ORDER BY run_id"
            ).fetchall()
        assert len(rows) == 2
        run_ids = {r[0] for r in rows}
        assert "run_alpha" in run_ids and "run_beta" in run_ids

    def test_discovery_view_names_are_all_v_disc_prefixed(self) -> None:
        """Every discovery view name starts with ``v_disc_``."""
        for name in DISCOVERY_VIEW_NAMES:
            assert name.startswith("v_disc_"), f"Bad discovery view name: {name}"

    def test_validation_view_names_are_all_v_val_prefixed(self) -> None:
        """Every validation view name starts with ``v_val_``."""
        for name in VALIDATION_VIEW_NAMES:
            assert name.startswith("v_val_"), f"Bad validation view name: {name}"

    def test_no_overlap_between_discovery_and_validation_view_names(self) -> None:
        """Discovery and validation view name sets are disjoint."""
        overlap = DISCOVERY_VIEW_NAMES & VALIDATION_VIEW_NAMES
        assert not overlap, f"Overlapping view names: {overlap}"

    def test_discovery_views_use_discovery_directory(self, runs_dir: Path) -> None:
        """Discovery views read from discovery/ path (not validation/)."""
        # The chunks we wrote only exist in discovery/ — they must be found.
        with open_all_runs(base_dir=runs_dir) as conn:
            count = conn.execute("SELECT count(*) FROM v_disc_chunks").fetchone()[0]
        assert count == 4  # 2 runs × 2 chunks — proves discovery/ is read

    def test_joining_disc_and_val_views_is_explicit_not_silent(self, runs_dir: Path) -> None:
        """A join between disc and val views is explicit, not silent contamination.

        The two view sets are clearly labelled; any cross-join is an intentional
        analytical choice, not an accidental leakage.
        """
        with open_all_runs(base_dir=runs_dir) as conn:
            # Explicitly joining disc + val is allowed at query time (post-hoc analysis)
            rows = conn.execute(
                """
                SELECT c.run_id, count(*) AS chunks, p.company_id
                FROM v_disc_chunks AS c
                JOIN v_val_market_prices AS p ON c.run_id = p.run_id
                GROUP BY c.run_id, p.company_id
                ORDER BY c.run_id
                """
            ).fetchall()
        assert len(rows) == 2  # 2 runs
        # Each has 2 chunks and one market_prices row
        for row in rows:
            assert row[1] == 2, f"Expected 2 chunks per run, got {row}"


# ---------------------------------------------------------------------------
# 4. No discovery-stage module imports db.py (source scan)
# ---------------------------------------------------------------------------


class TestNoDiscoveryImport:
    """Source-scan: confirm db.py is not imported by discovery modules."""

    def _parse_imports(self, source: str) -> set[str]:
        """Return all top-level module names imported by the source code."""
        imports: set[str] = set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
                    # Also capture "from . import db" style
                    for alias in node.names:
                        imports.add(alias.name)
        return imports

    @pytest.mark.parametrize("module_file", _DISCOVERY_STAGE_MODULES)
    def test_module_does_not_import_db(self, module_file: str) -> None:
        """Each discovery-stage module must not import theme_engine.db."""
        path = _THEME_ENGINE / module_file
        if not path.exists():
            pytest.skip(f"Module not found: {path}")

        source = path.read_text(encoding="utf-8")

        # Check for any direct import of `db`
        assert "from .db import" not in source, (
            f"{module_file} imports from .db — discovery modules must not use db.py"
        )
        assert "from theme_engine.db import" not in source, (
            f"{module_file} imports from theme_engine.db — discovery modules must not use db.py"
        )
        assert "import db" not in source, (
            f"{module_file} imports db — discovery modules must not use db.py"
        )
        assert "theme_engine import db" not in source, (
            f"{module_file} uses theme_engine.db — discovery modules must not use db.py"
        )

    def test_db_module_exists(self) -> None:
        """Sanity: db.py exists so the source scan is meaningful."""
        assert (_THEME_ENGINE / "db.py").exists()

    def test_db_module_is_importable(self) -> None:
        """db.py imports cleanly with duckdb available."""
        from theme_engine import db as _db  # noqa: F401

        assert hasattr(_db, "open_all_runs")
        assert hasattr(_db, "open_run")
        assert hasattr(_db, "registered_views")


# ---------------------------------------------------------------------------
# 5. View-set constants and registered_views() helper
# ---------------------------------------------------------------------------


class TestViewSetConstants:
    """Verify the view-set constants reflect leakage.py classification."""

    def test_discovery_view_names_include_required_artifacts(self) -> None:
        """Key artifacts are covered by discovery views."""
        required = {
            "v_disc_raw_documents",
            "v_disc_documents",
            "v_disc_chunks",
            "v_disc_entities",
            "v_disc_entity_aliases",
            "v_disc_edges",
            "v_disc_company_theme_exposure",
            "v_disc_fundamentals_asreported",
            "v_disc_financial_metrics",
            "v_disc_management_sentiment_fused",
            "v_disc_projected_impacts",
        }
        missing = required - DISCOVERY_VIEW_NAMES
        assert not missing, f"Missing discovery views: {missing}"

    def test_validation_view_names_include_key_artifacts(self) -> None:
        """Key validation artifacts are covered by validation views."""
        required = {
            "v_val_market_prices",
            "v_val_fundamentals",
            "v_val_projection_scores",
            "v_val_portfolio_baskets",
        }
        missing = required - VALIDATION_VIEW_NAMES
        assert not missing, f"Missing validation views: {missing}"

    def test_registered_views_returns_dict(self, runs_dir: Path) -> None:
        """registered_views() returns a dict with 'discovery' and 'validation' keys."""
        info = db.registered_views(base_dir=runs_dir)
        assert isinstance(info, dict)
        assert "discovery" in info
        assert "validation" in info
        # Both sets should have at least the artifacts we wrote in the fixture
        assert "v_disc_chunks" in info["discovery"]
        assert "v_disc_entities" in info["discovery"]
        assert "v_disc_edges" in info["discovery"]
        assert "v_val_market_prices" in info["validation"]

    def test_registered_views_single_run(self, single_run_dir: tuple) -> None:
        """registered_views(run_id) scopes to one run."""
        run_id, base = single_run_dir
        info = db.registered_views(run_id, base_dir=base)
        assert "v_disc_chunks" in info["discovery"]
        assert "v_disc_entities" in info["discovery"]
        # No validation artifacts in single_run_dir
        assert info["validation"] == []

    def test_empty_runs_dir_produces_no_views(self, tmp_path: Path) -> None:
        """When no runs exist, no views are registered (graceful empty glob)."""
        empty = tmp_path / "empty_runs"
        empty.mkdir()
        info = db.registered_views(base_dir=empty)
        assert info["discovery"] == []
        assert info["validation"] == []


# ---------------------------------------------------------------------------
# 6. Context manager safety
# ---------------------------------------------------------------------------


class TestContextManager:
    """open_run() and open_all_runs() are context managers that auto-close."""

    def test_open_all_runs_closes_connection(self, runs_dir: Path) -> None:
        """Connection is closed after exiting the context manager."""
        with open_all_runs(base_dir=runs_dir) as conn:
            assert conn is not None
        # After close, executing on the connection should fail
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_open_run_closes_connection(self, single_run_dir: tuple) -> None:
        """Single-run connection is closed after context exit."""
        run_id, base = single_run_dir
        with open_run(run_id, base_dir=base) as conn:
            assert conn is not None
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_connection_closed_even_on_query_error(self, runs_dir: Path) -> None:
        """Connection closes cleanly even when an exception occurs inside the block."""
        conn_ref: list[duckdb.DuckDBPyConnection] = []
        with pytest.raises(ZeroDivisionError):
            with open_all_runs(base_dir=runs_dir) as conn:
                conn_ref.append(conn)
                raise ZeroDivisionError("deliberate")
        # The connection was captured; it should now be closed
        with pytest.raises(Exception):
            conn_ref[0].execute("SELECT 1")
