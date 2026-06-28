"""Per-run artifact in-memory cache with mtime-based invalidation (GitHub #98).

Design
------
Key     : (str(path.resolve()), file_mtime_ns)  — encodes both the artifact
          identity and its point-in-time freshness.
Evict   : LRU; capped at ``max_entries`` (default 256 entries).  Oldest entry
          is evicted when the cap is reached.
Invalidate : stat() the file's mtime_ns on every call.  If it differs from the
          cached key, the stale entry is dropped and the file is re-read.
Mutation safety : copy-on-read via ``copy.deepcopy()``.  Every caller receives
          an independent deep copy; mutations to the returned object cannot
          corrupt cached state or affect other callers.

Public API
----------
  load_json(path: Path) -> dict
      Read a JSON file (or serve from cache).  Raises FileNotFoundError if path
      absent; callers should check existence before calling.

  load_parquet_rows(path: Path) -> list[dict]
      Read a Parquet file and return rows as list[dict] (or serve from cache).
      Raises FileNotFoundError if path absent.

  clear() -> None
      Flush the entire cache (useful in tests).

  cache_size() -> int
      Current number of cached entries (useful in tests / diagnostics).

Thread safety
-------------
A threading.Lock guards the OrderedDict.  File I/O is performed *outside* the
lock (to avoid blocking other threads during disk reads).  A double-check after
reacquiring the lock prevents duplicate entries when two threads race on the
same miss.

Typical usage (in endpoint code)
---------------------------------
  from . import run_cache

  data = run_cache.load_json(runs.get_run_dir(run_id) / "discovery" / "graph.json")
  rows = run_cache.load_parquet_rows(runs.get_run_dir(run_id) / "discovery" / "edges.parquet")
"""

from __future__ import annotations

import copy
import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

import pyarrow.parquet as pq

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

_DEFAULT_MAX_ENTRIES: int = 256

# --------------------------------------------------------------------------- #
# Internal cache class                                                        #
# --------------------------------------------------------------------------- #

_CacheKey = tuple[str, int]  # (resolved_path_str, mtime_ns)


class _RunCache:
    """Thread-safe bounded LRU artifact cache.

    Cached values are the raw Python objects (dict for JSON, list[dict] for
    Parquet rows).  The public ``load_*`` methods return deep copies so callers
    cannot mutate shared cache state.

    For testing, ``_read_count`` tracks total file reads (cache misses only).
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._store: OrderedDict[_CacheKey, Any] = OrderedDict()
        self._max: int = max(1, max_entries)
        self._lock: Lock = Lock()
        self._read_count: int = 0  # incremented on every actual file read

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _mtime_ns(self, path: Path) -> int:
        """Return the file's mtime in nanoseconds.  Raises FileNotFoundError."""
        return path.stat().st_mtime_ns

    def _evict_stale(self, path_str: str) -> None:
        """Remove all cached entries for *path_str* (any mtime).  Lock must be held."""
        stale = [k for k in self._store if k[0] == path_str]
        for sk in stale:
            del self._store[sk]

    def _admit(self, key: _CacheKey, value: Any) -> None:
        """Store *value* under *key* and enforce the LRU cap.  Lock must be held."""
        if key in self._store:
            self._store.move_to_end(key)
            return
        # Evict other entries for the same path (stale mtime)
        self._evict_stale(key[0])
        self._store[key] = value
        self._store.move_to_end(key)
        # LRU eviction: drop oldest entry when over cap
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    # ------------------------------------------------------------------ #
    # Public loaders                                                      #
    # ------------------------------------------------------------------ #

    def load_json(self, path: Path) -> dict:
        """Return the parsed JSON from *path*, using the cache.

        Returns a deep copy; callers may safely mutate the returned object
        without affecting the cache or other callers.

        Raises FileNotFoundError if the file does not exist.
        """
        path_str = str(path.resolve())
        mtime = self._mtime_ns(path)  # FileNotFoundError propagates to caller
        key: _CacheKey = (path_str, mtime)

        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return copy.deepcopy(self._store[key])

        # Cache miss — read outside the lock
        data: dict = json.loads(path.read_text(encoding="utf-8"))
        self._read_count += 1  # non-atomic but fine for monotonic test counter

        with self._lock:
            self._admit(key, data)

        return copy.deepcopy(data)

    def load_parquet_rows(self, path: Path) -> list[dict]:
        """Return Parquet rows from *path* as list[dict], using the cache.

        Returns a deep copy; callers may safely mutate the returned list or
        its row dicts without affecting the cache or other callers.

        Raises FileNotFoundError if the file does not exist.
        """
        path_str = str(path.resolve())
        mtime = self._mtime_ns(path)  # FileNotFoundError propagates to caller
        key: _CacheKey = (path_str, mtime)

        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return copy.deepcopy(self._store[key])

        # Cache miss — read outside the lock
        rows: list[dict] = pq.read_table(path).to_pylist()
        self._read_count += 1  # non-atomic but fine for monotonic test counter

        with self._lock:
            self._admit(key, rows)

        return copy.deepcopy(rows)

    # ------------------------------------------------------------------ #
    # Maintenance / introspection                                         #
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """Flush all cached entries (useful in tests and hot-reload scenarios)."""
        with self._lock:
            self._store.clear()

    def cache_size(self) -> int:
        """Return the current number of cached entries."""
        with self._lock:
            return len(self._store)

    def read_count(self) -> int:
        """Return the cumulative number of actual file reads (cache misses)."""
        return self._read_count

    def reset_read_count(self) -> None:
        """Reset the read counter to zero (useful in tests)."""
        self._read_count = 0


# --------------------------------------------------------------------------- #
# Module-level singleton                                                      #
# --------------------------------------------------------------------------- #

_cache: _RunCache = _RunCache(max_entries=_DEFAULT_MAX_ENTRIES)


# --------------------------------------------------------------------------- #
# Public convenience functions (thin wrappers over the singleton)            #
# --------------------------------------------------------------------------- #


def load_json(path: Path) -> dict:
    """Load a JSON file via the module-level run cache.

    Returns a deep copy.  Raises FileNotFoundError if the path does not exist.
    """
    return _cache.load_json(path)


def load_parquet_rows(path: Path) -> list[dict]:
    """Load a Parquet file via the module-level run cache.

    Returns a deep copy of the row list.  Raises FileNotFoundError if absent.
    """
    return _cache.load_parquet_rows(path)


def clear() -> None:
    """Flush the entire module-level cache."""
    _cache.clear()


def cache_size() -> int:
    """Return the current number of cached entries."""
    return _cache.cache_size()


def read_count() -> int:
    """Return the cumulative number of actual file reads (cache misses)."""
    return _cache.read_count()


def reset_read_count() -> None:
    """Reset the read counter to zero."""
    _cache.reset_read_count()
