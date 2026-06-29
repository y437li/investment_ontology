"""OI-6 R2: DuckDB discovery views span per-point discovery/<as_of>/ subdirs.

The discovery glob now covers BOTH the flat discovery/<artifact> layout and the
per-point discovery/<as_of>/<artifact> layout, and emits an ``as_of`` column
derived from the path ('' for flat).  This test writes two per-point chunk
artifacts and asserts both surface with distinct as_of values.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from theme_engine.db import open_all_runs, open_run

T1 = "2024-03-31"
T2 = "2024-06-30"


def _write_chunks(path: Path, chunk_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"chunk_id": pa.array(chunk_ids, type=pa.string())}), path
    )


def test_views_see_per_point_subdirs(tmp_path: Path):
    base = tmp_path / "runs"
    rid = "run_panel_demo"
    disc = base / rid / "discovery"
    _write_chunks(disc / T1 / "chunks.parquet", ["t1_a", "t1_b"])
    _write_chunks(disc / T2 / "chunks.parquet", ["t2_a", "t2_b", "t2_c"])

    with open_all_runs(base_dir=base) as conn:
        rows = conn.execute(
            "SELECT as_of, chunk_id FROM v_disc_chunks ORDER BY as_of, chunk_id"
        ).fetchall()

    as_ofs = {r[0] for r in rows}
    chunk_ids = {r[1] for r in rows}
    assert as_ofs == {T1, T2}, f"expected both points, got {as_ofs}"
    assert chunk_ids == {"t1_a", "t1_b", "t2_a", "t2_b", "t2_c"}

    # Per-point row counts are correct and carry the right as_of.
    t1_rows = [r for r in rows if r[0] == T1]
    t2_rows = [r for r in rows if r[0] == T2]
    assert len(t1_rows) == 2
    assert len(t2_rows) == 3


def test_single_run_view_spans_points_and_flat(tmp_path: Path):
    base = tmp_path / "runs"
    rid = "run_mixed"
    disc = base / rid / "discovery"
    # Flat (legacy) artifact + a per-point artifact coexist.
    _write_chunks(disc / "chunks.parquet", ["flat_x"])
    _write_chunks(disc / T1 / "chunks.parquet", ["t1_a"])

    with open_run(rid, base_dir=base) as conn:
        rows = conn.execute(
            "SELECT as_of, chunk_id FROM v_disc_chunks ORDER BY chunk_id"
        ).fetchall()

    by_chunk = {r[1]: r[0] for r in rows}
    assert by_chunk["flat_x"] == ""  # flat layout -> empty as_of
    assert by_chunk["t1_a"] == T1
