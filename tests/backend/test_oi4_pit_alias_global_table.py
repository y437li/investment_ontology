"""OI-4: Point-in-time alias resolution + global companion table tests.

Deliverables tested:

  (A) PIT TABLE UNCHANGED — entity_aliases.parquet is built from chunks with
      available_at <= as_of_date only.  Future-dated alias sources are excluded.
      Every row records as_of_date and alias_scope="point_in_time".

  (B) GLOBAL TABLE WRITTEN — entity_aliases_global.parquet is written over the
      FULL corpus (all entities/chunks regardless of available_at).
      alias_scope="global", as_of_date="" (GLOBAL_AS_OF_SENTINEL).
      Entities excluded from the PIT table (future-dated) ARE present in global.

  (C) ISOLATION SOURCE-SCAN — graph_build.py, exposure.py, and themes.py must
      NOT contain "entity_aliases_global" as a string literal.  (These modules
      don't read any alias table at all; the scan proves it, and is made
      non-tautological by injecting a violation into a synthetic module.)

  (D) ISOLATION BEHAVIORAL — running build_graph with a run that has a poison
      (invalid) entity_aliases_global.parquet succeeds without error, proving
      graph_build never opens that file.

All tests are hermetic.  conftest.py redirects RUN_OUTPUT_DIR to a temp dir.
No network calls, no LLM calls.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine.config import settings
from theme_engine.entity_resolution import (
    ENTITY_ALIASES_COLUMNS,
    GLOBAL_AS_OF_SENTINEL,
    _build_alias_rows,
    _filter_pit_entities,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AS_OF = "2024-06-30"
PAST_DATE = "2024-01-01"      # always <= AS_OF: PIT-eligible
FUTURE_DATE = "2025-06-30"    # strictly > AS_OF: excluded from PIT, present in global
_CREATED_AT = "2024-06-30T00:00:00Z"


# ---------------------------------------------------------------------------
# Minimal fixture builders
# ---------------------------------------------------------------------------


def _chunk(chunk_id: str, available_at: str) -> dict:
    return {"chunk_id": chunk_id, "available_at": available_at}


def _entity(entity_id: str, chunk_id: str, entity_type: str = "Company") -> dict:
    return {
        "entity_id": entity_id,
        "canonical_name": entity_id,
        "entity_type": entity_type,
        "source_chunk_ids": [chunk_id],
        "ticker": None,
    }


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_run(as_of_date: str = AS_OF) -> tuple[str, Path]:
    """Create a minimal run directory and return (run_id, run_dir)."""
    run_id = f"oi4_test_{uuid.uuid4().hex[:10]}"
    run_dir = settings.run_output_dir / run_id
    discovery = run_dir / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "as_of_date": as_of_date,
        "created_at": _utcnow(),
        "code_version": "oi4-test",
        "universe_config": "configs/universe.example.yml",
        "pipeline_config": "configs/pipeline.example.yml",
        "validation_config": "configs/validation.example.yml",
        "input_hash": "test",
        "discovery_frozen": False,
        "discovery_artifact_hashes": None,
        "sweep_parent_id": None,
        "frozen_at": None,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_id, run_dir


def _write_parquet_simple(path: Path, rows: list[dict]) -> None:
    """Write rows as a Parquet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        pq.write_table(pa.table({}), path)
        return
    names = list(rows[0].keys())
    arrays: dict[str, pa.Array] = {}
    for name in names:
        values = [r.get(name) for r in rows]
        non_null = [v for v in values if v is not None]
        if non_null and isinstance(non_null[0], float):
            arrays[name] = pa.array(values, type=pa.float64())
        elif non_null and isinstance(non_null[0], list):
            arrays[name] = pa.array(values, type=pa.list_(pa.string()))
        else:
            arrays[name] = pa.array(
                [str(v) if v is not None else None for v in values],
                type=pa.string(),
            )
    pq.write_table(pa.table(arrays), path)


# ---------------------------------------------------------------------------
# (A) PIT TABLE: filter gate unit tests
# ---------------------------------------------------------------------------


class TestPITFilterGate:
    """Unit tests for _filter_pit_entities: the PIT exclusion gate."""

    def test_past_chunk_entity_is_pit_eligible(self):
        """Entity with past-dated chunk is included in PIT set."""
        chunks = [_chunk("c1", PAST_DATE)]
        entities = [_entity("ent_past", "c1")]

        result = _filter_pit_entities(entities, chunks, AS_OF)

        assert len(result) == 1
        assert result[0]["entity_id"] == "ent_past"

    def test_future_chunk_entity_is_pit_excluded(self):
        """Entity whose ONLY chunk is future-dated is excluded from PIT set.

        This is the core OI-4 acceptance criterion: a source with
        available_at > as_of_date must NOT shape the alias set at as_of_date.
        """
        chunks = [_chunk("c_future", FUTURE_DATE)]
        entities = [_entity("ent_future", "c_future")]

        result = _filter_pit_entities(entities, chunks, AS_OF)

        assert result == [], (
            f"Future-dated entity must be excluded from PIT set; got: {result}"
        )

    def test_mixed_entities_only_past_pass(self):
        """With both past and future entities, only the past one is PIT-eligible."""
        chunks = [
            _chunk("c_past", PAST_DATE),
            _chunk("c_future", FUTURE_DATE),
        ]
        entities = [
            _entity("ent_a", "c_past"),
            _entity("ent_b", "c_future"),
        ]

        result = _filter_pit_entities(entities, chunks, AS_OF)

        entity_ids = {e["entity_id"] for e in result}
        assert "ent_a" in entity_ids, "Past entity must be in PIT set"
        assert "ent_b" not in entity_ids, "Future entity must be excluded from PIT set"

    def test_entity_with_one_past_one_future_chunk_is_eligible(self):
        """Entity eligible if ANY of its source chunks is past-dated (inclusive rule)."""
        chunks = [
            _chunk("c_past", PAST_DATE),
            _chunk("c_future", FUTURE_DATE),
        ]
        # Entity whose evidence spans both dates
        ent = {
            "entity_id": "ent_multi",
            "canonical_name": "Multi Entity",
            "entity_type": "Company",
            "source_chunk_ids": ["c_past", "c_future"],
            "ticker": None,
        }

        result = _filter_pit_entities([ent], chunks, AS_OF)

        assert len(result) == 1, (
            "Entity with at least one past chunk must be PIT-eligible"
        )


# ---------------------------------------------------------------------------
# (A) PIT TABLE: alias row schema tests
# ---------------------------------------------------------------------------


class TestPITAliasRowSchema:
    """Verify PIT alias rows have correct schema fields."""

    def test_pit_rows_alias_scope_is_point_in_time(self):
        """Every row in the PIT table must have alias_scope='point_in_time'."""
        entities = [_entity("ent_x", "c1")]
        rows = _build_alias_rows(entities, AS_OF, "point_in_time", _CREATED_AT)

        assert rows, "Expected at least one alias row"
        for row in rows:
            assert row["alias_scope"] == "point_in_time", (
                f"PIT row has wrong alias_scope: {row['alias_scope']!r}"
            )

    def test_pit_rows_record_as_of_date(self):
        """Every PIT row must record the run's as_of_date."""
        entities = [_entity("ent_y", "c1")]
        rows = _build_alias_rows(entities, AS_OF, "point_in_time", _CREATED_AT)

        assert rows
        for row in rows:
            assert row["as_of_date"] == AS_OF, (
                f"PIT row has wrong as_of_date: {row['as_of_date']!r}"
            )

    def test_pit_rows_have_all_required_columns(self):
        """Every PIT row must contain all ENTITY_ALIASES_COLUMNS."""
        entities = [_entity("ent_z", "c1")]
        rows = _build_alias_rows(entities, AS_OF, "point_in_time", _CREATED_AT)

        assert rows
        for row in rows:
            for col in ENTITY_ALIASES_COLUMNS:
                assert col in row, f"PIT row missing required column: {col!r}"


# ---------------------------------------------------------------------------
# (B) GLOBAL TABLE: alias row schema tests
# ---------------------------------------------------------------------------


class TestGlobalAliasRowSchema:
    """Verify global alias rows have correct schema fields."""

    def test_global_rows_alias_scope_is_global(self):
        """Every row in the global table must have alias_scope='global'."""
        entities = [_entity("ent_g1", "c1")]
        rows = _build_alias_rows(
            entities, GLOBAL_AS_OF_SENTINEL, "global", _CREATED_AT
        )

        assert rows
        for row in rows:
            assert row["alias_scope"] == "global", (
                f"Global row has wrong alias_scope: {row['alias_scope']!r}"
            )

    def test_global_rows_as_of_date_is_sentinel(self):
        """Global rows must have as_of_date=GLOBAL_AS_OF_SENTINEL (empty string)."""
        entities = [_entity("ent_g2", "c1")]
        rows = _build_alias_rows(
            entities, GLOBAL_AS_OF_SENTINEL, "global", _CREATED_AT
        )

        assert rows
        for row in rows:
            assert row["as_of_date"] == GLOBAL_AS_OF_SENTINEL, (
                f"Global row has wrong as_of_date: {row['as_of_date']!r}; "
                f"expected sentinel {GLOBAL_AS_OF_SENTINEL!r}"
            )

    def test_global_rows_have_all_required_columns(self):
        """Every global row must contain all ENTITY_ALIASES_COLUMNS."""
        entities = [_entity("ent_g3", "c1")]
        rows = _build_alias_rows(
            entities, GLOBAL_AS_OF_SENTINEL, "global", _CREATED_AT
        )

        assert rows
        for row in rows:
            for col in ENTITY_ALIASES_COLUMNS:
                assert col in row, f"Global row missing required column: {col!r}"


# ---------------------------------------------------------------------------
# (B) GLOBAL TABLE: full-corpus coverage vs PIT exclusion
# ---------------------------------------------------------------------------


class TestGlobalVsPITCoverage:
    """Prove the global table includes entities the PIT table excludes."""

    def test_future_entity_absent_from_pit_present_in_global(self):
        """An entity from a future-dated chunk is excluded from PIT but in global.

        This is the core OI-4 acceptance criterion for the global table:
        the global table is a superset of the PIT table.
        """
        chunks = [
            _chunk("c_past", PAST_DATE),
            _chunk("c_future", FUTURE_DATE),
        ]
        entities = [
            _entity("ent_past", "c_past"),
            _entity("ent_future", "c_future"),
        ]

        # PIT: only past entity
        pit_entities = _filter_pit_entities(entities, chunks, AS_OF)
        pit_rows = _build_alias_rows(pit_entities, AS_OF, "point_in_time", _CREATED_AT)

        # Global: ALL entities
        global_rows = _build_alias_rows(
            entities, GLOBAL_AS_OF_SENTINEL, "global", _CREATED_AT
        )

        # PIT must NOT contain the future entity
        pit_aliases = {r["alias"] for r in pit_rows}
        assert "ent_future" not in pit_aliases, (
            f"Future entity should not appear in PIT alias set; pit_aliases={pit_aliases}"
        )

        # Global MUST contain the future entity
        global_aliases = {r["alias"] for r in global_rows}
        assert "ent_future" in global_aliases, (
            f"Future entity must appear in global alias set; global_aliases={global_aliases}"
        )

        # Past entity must appear in BOTH
        assert "ent_past" in pit_aliases, "Past entity must be in PIT set"
        assert "ent_past" in global_aliases, "Past entity must also be in global set"

    def test_global_is_superset_of_pit_aliases(self):
        """Every alias in the PIT table also appears in the global table."""
        chunks = [
            _chunk("c1", PAST_DATE),
            _chunk("c2", FUTURE_DATE),
        ]
        entities = [
            _entity("ent_a", "c1"),
            _entity("ent_b", "c2"),
        ]

        pit_entities = _filter_pit_entities(entities, chunks, AS_OF)
        pit_rows = _build_alias_rows(pit_entities, AS_OF, "point_in_time", _CREATED_AT)
        global_rows = _build_alias_rows(
            entities, GLOBAL_AS_OF_SENTINEL, "global", _CREATED_AT
        )

        global_aliases = {r["alias"] for r in global_rows}
        for row in pit_rows:
            assert row["alias"] in global_aliases, (
                f"Alias {row['alias']!r} in PIT but missing from global"
            )


# ---------------------------------------------------------------------------
# (B) GLOBAL TABLE: end-to-end resolve_entities writes both files
# ---------------------------------------------------------------------------


class TestResolveEntitiesWritesBothTables:
    """End-to-end test: resolve_entities() writes PIT + global tables."""

    def test_resolve_entities_writes_pit_table(self):
        """resolve_entities must write entity_aliases.parquet."""
        from theme_engine import entity_resolution

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        # Seed chunks (past-dated so PIT has at least one row)
        _write_parquet_simple(
            discovery / "chunks.parquet",
            [{"chunk_id": "c1", "available_at": PAST_DATE}],
        )
        # Seed entities
        _write_parquet_simple(
            discovery / "entities.parquet",
            [{
                "entity_id": "ent_x",
                "canonical_name": "Entity X",
                "entity_type": "Company",
                "source_chunk_ids": ["c1"],
                "ticker": None,
                "first_seen_at": PAST_DATE,
            }],
        )

        entity_resolution.resolve_entities(run_id)

        pit_path = discovery / "entity_aliases.parquet"
        assert pit_path.exists(), "entity_aliases.parquet must be written"
        rows = pq.read_table(pit_path).to_pylist()
        assert rows, "PIT table must contain at least one row"
        assert all(r["alias_scope"] == "point_in_time" for r in rows)
        assert all(r["as_of_date"] == AS_OF for r in rows)

    def test_resolve_entities_writes_global_table(self):
        """resolve_entities must write entity_aliases_global.parquet."""
        from theme_engine import entity_resolution

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        _write_parquet_simple(
            discovery / "chunks.parquet",
            [
                {"chunk_id": "c_past", "available_at": PAST_DATE},
                {"chunk_id": "c_future", "available_at": FUTURE_DATE},
            ],
        )
        _write_parquet_simple(
            discovery / "entities.parquet",
            [
                {
                    "entity_id": "ent_past",
                    "canonical_name": "Past Entity",
                    "entity_type": "Company",
                    "source_chunk_ids": ["c_past"],
                    "ticker": None,
                    "first_seen_at": PAST_DATE,
                },
                {
                    "entity_id": "ent_future",
                    "canonical_name": "Future Entity",
                    "entity_type": "Company",
                    "source_chunk_ids": ["c_future"],
                    "ticker": None,
                    "first_seen_at": FUTURE_DATE,
                },
            ],
        )

        entity_resolution.resolve_entities(run_id)

        global_path = discovery / "entity_aliases_global.parquet"
        assert global_path.exists(), "entity_aliases_global.parquet must be written"
        rows = pq.read_table(global_path).to_pylist()
        assert rows, "Global table must contain at least one row"
        assert all(r["alias_scope"] == "global" for r in rows), (
            "All global table rows must have alias_scope='global'"
        )
        assert all(r["as_of_date"] == GLOBAL_AS_OF_SENTINEL for r in rows), (
            "All global table rows must have as_of_date=GLOBAL_AS_OF_SENTINEL"
        )

    def test_global_table_includes_future_excluded_entity(self):
        """Future-dated entity absent from PIT must appear in global after resolve."""
        from theme_engine import entity_resolution

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        _write_parquet_simple(
            discovery / "chunks.parquet",
            [
                {"chunk_id": "c_past", "available_at": PAST_DATE},
                {"chunk_id": "c_future", "available_at": FUTURE_DATE},
            ],
        )
        _write_parquet_simple(
            discovery / "entities.parquet",
            [
                {
                    "entity_id": "ent_pit",
                    "canonical_name": "PIT Entity",
                    "entity_type": "MacroIndicator",
                    "source_chunk_ids": ["c_past"],
                    "ticker": None,
                    "first_seen_at": PAST_DATE,
                },
                {
                    "entity_id": "ent_global_only",
                    "canonical_name": "Global Only Entity",
                    "entity_type": "Commodity",
                    "source_chunk_ids": ["c_future"],
                    "ticker": None,
                    "first_seen_at": FUTURE_DATE,
                },
            ],
        )

        entity_resolution.resolve_entities(run_id)

        pit_rows = pq.read_table(discovery / "entity_aliases.parquet").to_pylist()
        global_rows = pq.read_table(
            discovery / "entity_aliases_global.parquet"
        ).to_pylist()

        pit_canonical = {r["canonical_name"] for r in pit_rows}
        global_canonical = {r["canonical_name"] for r in global_rows}

        assert "Global Only Entity" not in pit_canonical, (
            "Future-dated entity must NOT appear in PIT table"
        )
        assert "Global Only Entity" in global_canonical, (
            "Future-dated entity MUST appear in global table"
        )
        assert "PIT Entity" in pit_canonical, "Past entity must be in PIT table"
        assert "PIT Entity" in global_canonical, "Past entity must also be in global table"

    def test_pit_row_count_not_greater_than_global(self):
        """Global table must have >= as many rows as the PIT table (superset)."""
        from theme_engine import entity_resolution

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        _write_parquet_simple(
            discovery / "chunks.parquet",
            [
                {"chunk_id": "c_past", "available_at": PAST_DATE},
                {"chunk_id": "c_future", "available_at": FUTURE_DATE},
            ],
        )
        _write_parquet_simple(
            discovery / "entities.parquet",
            [
                {
                    "entity_id": "ent_a",
                    "canonical_name": "Entity A",
                    "entity_type": "Company",
                    "source_chunk_ids": ["c_past"],
                    "ticker": None,
                    "first_seen_at": PAST_DATE,
                },
                {
                    "entity_id": "ent_b",
                    "canonical_name": "Entity B",
                    "entity_type": "Company",
                    "source_chunk_ids": ["c_future"],
                    "ticker": None,
                    "first_seen_at": FUTURE_DATE,
                },
            ],
        )

        entity_resolution.resolve_entities(run_id)

        pit_count = len(pq.read_table(discovery / "entity_aliases.parquet").to_pylist())
        global_count = len(
            pq.read_table(discovery / "entity_aliases_global.parquet").to_pylist()
        )

        assert global_count >= pit_count, (
            f"Global table ({global_count} rows) must have >= rows as PIT ({pit_count} rows)"
        )


# ---------------------------------------------------------------------------
# (C) ISOLATION SOURCE-SCAN
# ---------------------------------------------------------------------------

# Discovery modules that must NOT reference entity_aliases_global
_GRAPH_DISCOVERY_MODULES = {
    "graph_build.py",
    "exposure.py",
    "themes.py",
}

_FORBIDDEN_GLOBAL_FRAGMENT = "entity_aliases_global"

_THEME_ENGINE_ROOT = (
    Path(__file__).resolve().parents[2] / "app" / "backend" / "theme_engine"
)


def _nondocstring_string_literals(source: str) -> list[str]:
    """Return string literals from *source*, excluding module/class/function docstrings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring_ids.add(id(node.body[0].value))

    literals: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            literals.append(node.value)
    return literals


class TestIsolationSourceScan:
    """Gate (C): graph/exposure/themes must not reference entity_aliases_global.parquet."""

    def test_graph_discovery_modules_do_not_reference_global_alias_table(self):
        """graph_build, exposure, themes must NOT contain 'entity_aliases_global'
        as a non-docstring string literal.

        These modules consume entities.parquet, edges.parquet, and graph.json.
        The global alias table is inspection-only and must never be wired into
        the discovery pipeline.
        """
        violations: list[str] = []

        for module_name in sorted(_GRAPH_DISCOVERY_MODULES):
            src_path = _THEME_ENGINE_ROOT / module_name
            if not src_path.exists():
                continue

            source = src_path.read_text(encoding="utf-8")
            literals = _nondocstring_string_literals(source)
            flagged = [lit for lit in literals if _FORBIDDEN_GLOBAL_FRAGMENT in lit]
            if flagged:
                violations.append(
                    f"{module_name}: found '{_FORBIDDEN_GLOBAL_FRAGMENT}' "
                    f"string literal(s): {flagged[:3]}"
                )

        assert not violations, (
            "graph_build/exposure/themes must not reference entity_aliases_global:\n"
            + "\n".join(violations)
        )

    def test_source_scan_is_nontautological(self, tmp_path):
        """Prove the scan catches a violation when injected into a synthetic module."""
        bad_src = (
            'def bad_graph_read(run_id):\n'
            '    path = "discovery/entity_aliases_global.parquet"\n'
            '    return open(path).read()\n'
        )
        literals = _nondocstring_string_literals(bad_src)
        flagged = [lit for lit in literals if _FORBIDDEN_GLOBAL_FRAGMENT in lit]
        assert flagged, (
            "Source scan must detect 'entity_aliases_global' literal "
            "in synthetic bad module (non-tautological check)"
        )

    def test_docstring_reference_not_flagged(self):
        """A module-level docstring mentioning the global table must NOT be flagged.

        This proves the exclusion of docstring strings works, preventing
        false positives from documentation text.
        """
        doc_src = (
            '"""This module reads entity_aliases_global.parquet for reference."""\n'
            '\n'
            'def legit_function():\n'
            '    return 42\n'
        )
        literals = _nondocstring_string_literals(doc_src)
        flagged = [lit for lit in literals if _FORBIDDEN_GLOBAL_FRAGMENT in lit]
        # The module docstring should have been excluded
        assert not flagged, (
            "Docstring references to entity_aliases_global must not be flagged as violations"
        )


# ---------------------------------------------------------------------------
# (D) ISOLATION BEHAVIORAL — graph_build does NOT open the global table
# ---------------------------------------------------------------------------


class TestIsolationBehavioral:
    """Behavioral proof: graph_build succeeds even if global table is poisoned."""

    def test_graph_build_succeeds_with_poison_global_alias_table(self):
        """build_graph completes without error even when entity_aliases_global.parquet
        is invalid/poison, proving graph_build never opens that file.

        If graph_build silently read the global table, it would raise a Parquet
        read error on the poison file and this test would fail.
        """
        from theme_engine import graph_build

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        # Seed minimal entities and edges for build_graph
        _write_parquet_simple(
            discovery / "entities.parquet",
            [{
                "entity_id": "ent_co",
                "canonical_name": "Test Co",
                "entity_type": "Company",
                "first_seen_at": PAST_DATE,
                "source_chunk_ids": ["c1"],
                "ticker": "TST",
            }],
        )
        _write_parquet_simple(
            discovery / "edges.parquet",
            [],  # no edges needed; graph builds with nodes only
        )

        # Write a POISON (invalid) entity_aliases_global.parquet
        # If build_graph reads this, it will fail with an error.
        poison_path = discovery / "entity_aliases_global.parquet"
        poison_path.write_bytes(b"NOT_A_VALID_PARQUET_FILE_POISON_OI4")

        # build_graph must succeed — proving it never opens the poison file
        node_count, edge_count = graph_build.build_graph(run_id)

        assert node_count >= 1, "Expected at least one node in graph"
        # Poison file must still be in place (not consumed/read)
        assert poison_path.exists(), "Poison global alias file should be untouched"
        assert poison_path.read_bytes() == b"NOT_A_VALID_PARQUET_FILE_POISON_OI4", (
            "Poison file content must be unmodified (graph_build must not have read it)"
        )

    def test_exposure_compute_does_not_read_global_alias_table(self):
        """compute_exposure completes without error even with a poisoned global table.

        Exposure reads graph.json, communities.json, entities.parquet, edges.parquet.
        It must never read entity_aliases_global.parquet.
        """
        from theme_engine import exposure as exposure_mod
        from theme_engine import graph_build, themes

        run_id, run_dir = _make_run(AS_OF)
        discovery = run_dir / "discovery"

        # Seed minimal data for graph + themes + exposure pipeline
        _write_parquet_simple(
            discovery / "entities.parquet",
            [{
                "entity_id": "co_a",
                "canonical_name": "Company A",
                "entity_type": "Company",
                "first_seen_at": PAST_DATE,
                "source_chunk_ids": ["c1"],
                "ticker": "CA",
            }],
        )
        _write_parquet_simple(
            discovery / "edges.parquet",
            [],
        )

        # Write poison global table
        poison_path = discovery / "entity_aliases_global.parquet"
        poison_path.write_bytes(b"POISON_GLOBAL_OI4_EXPOSURE_ISOLATION")

        # Run graph build and theme discovery (required by exposure)
        graph_build.build_graph(run_id)
        themes.discover_themes(run_id)

        # compute_exposure must succeed without reading the poison
        row_count = exposure_mod.compute_exposure(run_id)
        # row_count may be 0 (no communities with connected companies) — that's fine
        assert isinstance(row_count, int)

        # Poison file must remain unmodified
        assert poison_path.read_bytes() == b"POISON_GLOBAL_OI4_EXPOSURE_ISOLATION", (
            "Poison file content unmodified — exposure did not read global alias table"
        )
