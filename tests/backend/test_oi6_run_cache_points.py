"""OI-6 R1-3: point-aware run_cache.

Per-point artifact paths discovery/<t1>/chunks.parquet and
discovery/<t2>/chunks.parquet are naturally distinct LRU keys (resolved-path +
mtime).  The frozen-status cache is re-keyed to (run_id, as_of) so a per-point
freeze of t1 cannot mark t2 frozen.
"""

from __future__ import annotations

import time

import pyarrow as pa
import pyarrow.parquet as pq

from theme_engine import freeze as freeze_mod, run_cache, runs
from theme_engine.models import RunCreateRequest

T1 = "2024-03-31"
T2 = "2024-06-30"


def _write_chunks(path, n: int) -> None:
    pq.write_table(pa.table({"chunk_id": pa.array([f"c{i}" for i in range(n)], type=pa.string())}), path)


def _seed_point(run_id: str, as_of: str) -> None:
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    for name in runs.REQUIRED_DISCOVERY_ARTIFACTS:
        (d / name).write_text(f"seed-{as_of}-{name}", encoding="utf-8")


def test_point_paths_are_independent_lru_keys():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    p1 = runs.discovery_point_dir(run_id, T1, for_write=True) / "chunks.parquet"
    p2 = runs.discovery_point_dir(run_id, T2, for_write=True) / "chunks.parquet"
    _write_chunks(p1, 2)
    _write_chunks(p2, 5)

    run_cache.clear()
    run_cache.reset_read_count()

    rows1 = run_cache.load_parquet_rows(p1)
    rows2 = run_cache.load_parquet_rows(p2)
    assert len(rows1) == 2 and len(rows2) == 5
    assert run_cache.read_count() == 2  # one real read per distinct point path

    # Repeat loads are pure cache hits — no extra reads.
    run_cache.load_parquet_rows(p1)
    run_cache.load_parquet_rows(p2)
    assert run_cache.read_count() == 2


def test_deep_copy_isolation_across_points():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    p1 = runs.discovery_point_dir(run_id, T1, for_write=True) / "chunks.parquet"
    _write_chunks(p1, 1)
    run_cache.clear()

    a = run_cache.load_parquet_rows(p1)
    a[0]["chunk_id"] = "MUTATED"
    b = run_cache.load_parquet_rows(p1)
    assert b[0]["chunk_id"] == "c0"  # mutation did not corrupt the cache


def test_mtime_invalidation_per_path():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    p1 = runs.discovery_point_dir(run_id, T1, for_write=True) / "chunks.parquet"
    _write_chunks(p1, 2)
    run_cache.clear()

    assert len(run_cache.load_parquet_rows(p1)) == 2
    time.sleep(0.01)
    _write_chunks(p1, 7)  # rewrite changes mtime
    assert len(run_cache.load_parquet_rows(p1)) == 7  # stale entry invalidated


def test_frozen_status_cache_keyed_by_point():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _seed_point(run_id, T1)
    _seed_point(run_id, T2)
    run_cache.clear_frozen_cache()

    assert run_cache._is_frozen(run_id, as_of=T1) is False
    assert run_cache._is_frozen(run_id, as_of=T2) is False
    assert run_cache._is_frozen(run_id, as_of=None) is False  # run-level

    freeze_mod.freeze_discovery(run_id, as_of=T1)
    run_cache.clear_frozen_cache()

    assert run_cache._is_frozen(run_id, as_of=T1) is True
    assert run_cache._is_frozen(run_id, as_of=T2) is False  # t2 NOT frozen
    assert run_cache._is_frozen(run_id, as_of=None) is False  # run-level not flipped
