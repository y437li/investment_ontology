"""OI-6 R1-1: per-point discovery layout round-trip.

A multi-point run (as_of_dates=[t1, t2]) stores discovery artifacts in
per-point subtrees discovery/<as_of>/...  The central resolver
runs.discovery_point_dir(..., for_write=True) routes each write to the correct
subtree, and a flat write alongside does not corrupt point resolution.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from theme_engine import runs
from theme_engine.models import RunCreateRequest

T1 = "2024-03-31"
T2 = "2024-06-30"


def _chunks_table(n: int) -> pa.Table:
    return pa.table({"chunk_id": pa.array([f"c{i}" for i in range(n)], type=pa.string())})


def test_multi_point_layout_round_trip():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id

    # Manifest records the point-list and defaults as_of_date to the latest point.
    assert run.as_of_dates == [T1, T2]
    assert run.as_of_date == T2
    assert runs.list_as_of_points(run_id) == [T1, T2]
    assert runs.latest_as_of(run_id) == T2

    # create_run pre-creates per-point subdirs.
    base = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR
    assert (base / T1).is_dir()
    assert (base / T2).is_dir()

    # Write distinct chunk tables to each point via the write resolver.
    d1 = runs.discovery_point_dir(run_id, T1, for_write=True)
    d2 = runs.discovery_point_dir(run_id, T2, for_write=True)
    assert d1 == base / T1
    assert d2 == base / T2
    pq.write_table(_chunks_table(2), d1 / "chunks.parquet")
    pq.write_table(_chunks_table(5), d2 / "chunks.parquet")

    # Read back via the read resolver — each point is independent.
    r1 = pq.read_table(runs.discovery_point_dir(run_id, T1) / "chunks.parquet")
    r2 = pq.read_table(runs.discovery_point_dir(run_id, T2) / "chunks.parquet")
    assert r1.num_rows == 2
    assert r2.num_rows == 5


def test_flat_write_alongside_does_not_corrupt_point_resolution():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id

    d1 = runs.discovery_point_dir(run_id, T1, for_write=True)
    pq.write_table(_chunks_table(3), d1 / "chunks.parquet")

    # A stray flat-level file must not shadow the per-point subtree resolution.
    flat = runs.get_run_dir(run_id) / runs.DISCOVERY_DIR
    pq.write_table(_chunks_table(99), flat / "chunks.parquet")

    # Per-point read still resolves to the subtree (3 rows), not the flat file.
    resolved = runs.discovery_point_dir(run_id, T1)
    assert resolved == flat / T1
    assert pq.read_table(resolved / "chunks.parquet").num_rows == 3

    # Default read (no point) picks the latest point subtree, which exists once
    # written; here T2 was never written so it falls back to flat for T2.
    assert runs.discovery_point_dir(run_id, T1).name == T1
