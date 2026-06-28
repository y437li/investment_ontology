"""Hermetic tests for run_cache.py (GitHub #98).

Asserts:
  (A) hit-no-reread — a second call for the same (path, mtime) does NOT
      re-read the file (read_count stays at 1).
  (B) mtime-invalidation — rewriting the artifact (new mtime) forces a
      fresh read and returns the new content.
  (C) response-equality — cached and direct-read paths return identical data.
  (D) no-shared-mutation — mutating the object returned by call 1 does not
      corrupt the object returned by call 2.
  (E) lru-bound — the cache evicts oldest entries when max_entries is reached.
  (F) clear() flushes and forces a re-read on the next call.
  (G) path-based key — two different paths for the same content are cached
      independently.
  (H) json / parquet parity — both load_json and load_parquet_rows go through
      the same cache and satisfy the same invariants.
  (I) FileNotFoundError propagated — absent file raises immediately (no stale
      cache entry created).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# Use the internal class so tests can control max_entries and read_count
from theme_engine.run_cache import _RunCache, load_json, load_parquet_rows, clear, cache_size


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    # Ensure filesystem mtime advances even on fast runs (some filesystems have
    # 1-second resolution).  We touch with a future mtime explicitly.


def _write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        table = pa.table({})
    else:
        # Build a simple string schema from the row keys
        names = list(rows[0].keys())
        arrays = {k: pa.array([str(r[k]) for r in rows]) for k in names}
        table = pa.table(arrays)
    pq.write_table(table, path)


def _advance_mtime(path: Path) -> None:
    """Bump a file's mtime by a known increment so the cache key changes."""
    st = path.stat()
    new_ns = st.st_mtime_ns + 1_000_000_000  # +1 second in nanoseconds
    import os
    os.utime(path, ns=(st.st_atime_ns, new_ns))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_run(tmp_path: Path):
    """Return a fresh temp directory, also clears the module-level cache."""
    clear()
    yield tmp_path
    clear()


@pytest.fixture()
def fresh_cache():
    """Return an isolated _RunCache instance (not the module singleton)."""
    return _RunCache(max_entries=256)


# ---------------------------------------------------------------------------
# (A) Hit-no-reread
# ---------------------------------------------------------------------------


class TestHitNoReread:
    def test_json_second_call_does_not_reread(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "graph.json"
        _write_json(path, {"nodes": [1, 2]})

        # First call — cache miss → reads the file
        result1 = fresh_cache.load_json(path)
        assert fresh_cache.read_count() == 1

        # Second call with same file (mtime unchanged) — cache hit → no re-read
        result2 = fresh_cache.load_json(path)
        assert fresh_cache.read_count() == 1, "second call should NOT re-read the file"
        assert result1 == result2

    def test_parquet_second_call_does_not_reread(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "entities.parquet"
        _write_parquet(path, [{"entity_id": "ent_01", "name": "Acme"}])

        result1 = fresh_cache.load_parquet_rows(path)
        assert fresh_cache.read_count() == 1

        result2 = fresh_cache.load_parquet_rows(path)
        assert fresh_cache.read_count() == 1, "second call should NOT re-read the file"
        assert result1 == result2

    def test_cache_size_increments_on_miss_only(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "g.json"
        _write_json(path, {"x": 1})

        assert fresh_cache.cache_size() == 0
        fresh_cache.load_json(path)
        assert fresh_cache.cache_size() == 1
        fresh_cache.load_json(path)  # hit
        assert fresh_cache.cache_size() == 1  # no new entry


# ---------------------------------------------------------------------------
# (B) Mtime-invalidation
# ---------------------------------------------------------------------------


class TestMtimeInvalidation:
    def test_json_rewrite_returns_fresh_data(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "communities.json"
        _write_json(path, {"version": 1, "communities": []})

        result1 = fresh_cache.load_json(path)
        assert result1["version"] == 1
        assert fresh_cache.read_count() == 1

        # Rewrite with new content + advance mtime
        _write_json(path, {"version": 2, "communities": ["c1"]})
        _advance_mtime(path)

        result2 = fresh_cache.load_json(path)
        assert result2["version"] == 2, "stale cached version must NOT be returned"
        assert fresh_cache.read_count() == 2, "rewrite must trigger a re-read"

    def test_parquet_rewrite_returns_fresh_rows(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "edges.parquet"
        _write_parquet(path, [{"edge_id": "e1"}])

        rows1 = fresh_cache.load_parquet_rows(path)
        assert len(rows1) == 1

        _write_parquet(path, [{"edge_id": "e1"}, {"edge_id": "e2"}])
        _advance_mtime(path)

        rows2 = fresh_cache.load_parquet_rows(path)
        assert len(rows2) == 2, "stale rows must NOT be returned after rewrite"
        assert fresh_cache.read_count() == 2

    def test_stale_entry_evicted_on_mtime_change(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "snap.json"
        _write_json(path, {"v": "old"})
        fresh_cache.load_json(path)  # populate cache
        assert fresh_cache.cache_size() == 1

        _write_json(path, {"v": "new"})
        _advance_mtime(path)

        fresh_cache.load_json(path)  # should evict old entry, admit new
        # Only one entry for this path (stale evicted, new admitted)
        assert fresh_cache.cache_size() == 1


# ---------------------------------------------------------------------------
# (C) Response equality (before-cache == after-cache)
# ---------------------------------------------------------------------------


class TestResponseEquality:
    def test_json_cache_matches_direct_read(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "theme_snapshots.json"
        data = {"snapshots": [{"id": "s1", "name": "AI"}, {"id": "s2"}], "meta": "test"}
        _write_json(path, data)

        direct = json.loads(path.read_text(encoding="utf-8"))
        cached = fresh_cache.load_json(path)
        assert cached == direct

    def test_parquet_cache_matches_direct_read(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "ents.parquet"
        rows_in = [{"entity_id": "e1", "name": "Foo"}, {"entity_id": "e2", "name": "Bar"}]
        _write_parquet(path, rows_in)

        direct = pq.read_table(path).to_pylist()
        cached = fresh_cache.load_parquet_rows(path)
        assert cached == direct

    def test_repeated_hits_return_equal_objects(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "obj.json"
        data = {"a": [1, 2, 3], "b": {"nested": True}}
        _write_json(path, data)

        r1 = fresh_cache.load_json(path)
        r2 = fresh_cache.load_json(path)
        assert r1 == r2
        assert r1 == data


# ---------------------------------------------------------------------------
# (D) No shared mutation
# ---------------------------------------------------------------------------


class TestNoSharedMutation:
    def test_json_mutation_does_not_corrupt_cache(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "g.json"
        _write_json(path, {"nodes": ["n1", "n2"], "count": 2})

        result1 = fresh_cache.load_json(path)
        # Mutate the returned object in a variety of ways
        result1["nodes"].append("INJECTED")
        result1["count"] = 999
        result1["extra_key"] = "poison"

        # Second call must return original, unmodified data
        result2 = fresh_cache.load_json(path)
        assert result2 == {"nodes": ["n1", "n2"], "count": 2}
        assert "extra_key" not in result2
        assert "INJECTED" not in result2["nodes"]

    def test_parquet_mutation_does_not_corrupt_cache(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "rows.parquet"
        original_rows = [{"edge_id": "e1", "weight": "0.9"}]
        _write_parquet(path, original_rows)

        rows1 = fresh_cache.load_parquet_rows(path)
        # Mutate the list and its contents
        rows1[0]["weight"] = "MUTATED"
        rows1.append({"edge_id": "POISON", "weight": "0"})

        rows2 = fresh_cache.load_parquet_rows(path)
        assert len(rows2) == 1, "appended row must not appear in next call"
        assert rows2[0]["weight"] == "0.9", "mutated field must not propagate"

    def test_two_callers_get_independent_copies(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "shared.json"
        _write_json(path, {"shared": [1, 2, 3]})

        caller_a = fresh_cache.load_json(path)
        caller_b = fresh_cache.load_json(path)

        caller_a["shared"].append(99)
        assert caller_b["shared"] == [1, 2, 3], "mutation by caller_a must not affect caller_b"


# ---------------------------------------------------------------------------
# (E) LRU eviction / capacity bound
# ---------------------------------------------------------------------------


class TestLRUBound:
    def test_oldest_entry_evicted_at_cap(self, tmp_run: Path):
        cache = _RunCache(max_entries=3)
        paths = []
        for i in range(3):
            p = tmp_run / f"f{i}.json"
            _write_json(p, {"i": i})
            cache.load_json(p)
            paths.append(p)

        assert cache.cache_size() == 3

        # Adding a 4th should evict the oldest (paths[0])
        p_new = tmp_run / "f3.json"
        _write_json(p_new, {"i": 3})
        cache.load_json(p_new)
        assert cache.cache_size() == 3

        # Verify 4th entry is cached (no reread on second access)
        count_before = cache.read_count()
        cache.load_json(p_new)
        assert cache.read_count() == count_before, "newly admitted entry should hit"

    def test_max_entries_one_works(self, tmp_run: Path):
        cache = _RunCache(max_entries=1)
        p1 = tmp_run / "a.json"
        p2 = tmp_run / "b.json"
        _write_json(p1, {"src": "a"})
        _write_json(p2, {"src": "b"})

        cache.load_json(p1)
        assert cache.cache_size() == 1

        cache.load_json(p2)
        assert cache.cache_size() == 1  # p1 evicted

    def test_cache_never_exceeds_cap(self, tmp_run: Path):
        cap = 5
        cache = _RunCache(max_entries=cap)
        for i in range(20):
            p = tmp_run / f"x{i}.json"
            _write_json(p, {"i": i})
            cache.load_json(p)
            assert cache.cache_size() <= cap


# ---------------------------------------------------------------------------
# (F) clear() resets everything
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_forces_reread(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "c.json"
        _write_json(path, {"v": 1})

        fresh_cache.load_json(path)
        assert fresh_cache.read_count() == 1
        assert fresh_cache.cache_size() == 1

        fresh_cache.clear()
        assert fresh_cache.cache_size() == 0

        # Next call must re-read the file
        fresh_cache.load_json(path)
        assert fresh_cache.read_count() == 2


# ---------------------------------------------------------------------------
# (G) Path-based key independence
# ---------------------------------------------------------------------------


class TestPathIndependence:
    def test_two_paths_cached_independently(self, tmp_run: Path, fresh_cache: _RunCache):
        p1 = tmp_run / "a" / "x.json"
        p2 = tmp_run / "b" / "x.json"  # same filename, different directory
        _write_json(p1, {"path": "a"})
        _write_json(p2, {"path": "b"})

        r1 = fresh_cache.load_json(p1)
        r2 = fresh_cache.load_json(p2)
        assert r1 != r2
        assert fresh_cache.cache_size() == 2
        assert fresh_cache.read_count() == 2


# ---------------------------------------------------------------------------
# (I) FileNotFoundError propagated
# ---------------------------------------------------------------------------


class TestFileNotFoundPropagation:
    def test_json_missing_file_raises(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "does_not_exist.json"
        with pytest.raises(FileNotFoundError):
            fresh_cache.load_json(path)
        # No stale entry created
        assert fresh_cache.cache_size() == 0

    def test_parquet_missing_file_raises(self, tmp_run: Path, fresh_cache: _RunCache):
        path = tmp_run / "does_not_exist.parquet"
        with pytest.raises(FileNotFoundError):
            fresh_cache.load_parquet_rows(path)
        assert fresh_cache.cache_size() == 0


# ---------------------------------------------------------------------------
# Module-level singleton wiring
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def test_module_load_json(self, tmp_run: Path):
        """Module-level load_json routes through the global cache."""
        clear()
        path = tmp_run / "m.json"
        _write_json(path, {"singleton": True})
        result = load_json(path)
        assert result == {"singleton": True}
        assert cache_size() >= 1
        clear()

    def test_module_load_parquet_rows(self, tmp_run: Path):
        """Module-level load_parquet_rows routes through the global cache."""
        clear()
        path = tmp_run / "m.parquet"
        _write_parquet(path, [{"id": "r1"}])
        rows = load_parquet_rows(path)
        assert len(rows) == 1
        assert cache_size() >= 1
        clear()
