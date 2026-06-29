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

Pre-freeze validation guard (OI-3)
-----------------------------------
Before serving any artifact under a run's ``validation/`` sub-directory the
cache checks that ``discovery_frozen=True`` in the run manifest.  If the run
is NOT yet frozen, ``leakage.LeakageError`` is raised immediately — no file
I/O is performed.  The check is cheap:

  1. The path is resolved.  If it is not under ``settings.run_output_dir``,
     the guard is skipped (non-run paths are never blocked).
  2. The run_id is extracted from the resolved path.
  3. The manifest's ``discovery_frozen`` flag is read and **cached** per run_id
     in a module-level dict.  Once a run is frozen (True) the flag is permanent,
     so the cached True is returned without a disk read on subsequent calls.
  4. If ``discovery_frozen`` is ``False``, ``LeakageError`` is raised.

Discovery artifact reads (``discovery/`` paths) are NEVER blocked.

Public API
----------
  load_json(path: Path) -> dict
      Read a JSON file (or serve from cache).  Raises FileNotFoundError if path
      absent; callers should check existence before calling.
      Raises LeakageError if path is under validation/ and run not yet frozen.

  load_parquet_rows(path: Path) -> list[dict]
      Read a Parquet file and return rows as list[dict] (or serve from cache).
      Raises FileNotFoundError if path absent.
      Raises LeakageError if path is under validation/ and run not yet frozen.

  clear() -> None
      Flush the entire cache (useful in tests).

  cache_size() -> int
      Current number of cached entries (useful in tests / diagnostics).

  clear_frozen_cache() -> None
      Flush the per-run frozen-status cache (useful in tests).

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
from typing import Any, Optional

import pyarrow.parquet as pq

from .leakage import LeakageError, assert_read_allowed

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

_DEFAULT_MAX_ENTRIES: int = 256

# --------------------------------------------------------------------------- #
# Pre-freeze validation guard                                                 #
# --------------------------------------------------------------------------- #

# Per-(run, point) frozen status cache.  Keys are (run_id, as_of_or_None);
# values are True once that scope is confirmed frozen.  Freeze is a one-way
# transition (False → True, never back), so we only store True permanently.
# Per-point freeze means one run can have point A frozen and point B not, so a
# run-keyed permanent-True is unsafe — the key carries the point.
_frozen_status_cache: dict[tuple[str, Optional[str]], bool] = {}


def _get_run_id_from_path(path: Path) -> Optional[str]:
    """Extract the run_id from an artifact path under ``settings.run_output_dir``.

    Returns ``None`` when the path is not under the run output directory
    (e.g. during tests with ``tmp_path`` fixtures) so the guard silently skips.
    """
    from .config import settings  # local import to avoid load-time side-effects

    try:
        run_output = settings.run_output_dir.resolve()
        rel = path.resolve().relative_to(run_output)
        return rel.parts[0]  # first component is run_id
    except (ValueError, IndexError):
        return None


def _point_from_path(path: Path) -> Optional[str]:
    """Return the as_of point for a per-point discovery artifact, else None.

    For ``<run>/discovery/<X>/<...>/<file>`` (a per-point subtree) returns ``X``.
    For flat ``<run>/discovery/<file>`` returns None.  For validation paths and
    non-run paths returns None.
    """
    from .config import settings  # local import to avoid load-time side-effects

    try:
        run_output = settings.run_output_dir.resolve()
        rel = path.resolve().relative_to(run_output)
    except (ValueError, IndexError):
        return None
    parts = rel.parts  # (run_id, 'discovery', <maybe point>, ..., file)
    if len(parts) < 3 or parts[1] != "discovery":
        return None
    # parts[2] is a point only if there is a further path component beneath it
    # (i.e. it is a directory level above the filename), not the filename itself.
    if len(parts) >= 4:
        return parts[2]
    return None


def _is_frozen(run_id: str, as_of: Optional[str] = None) -> bool:
    """Return whether *run_id*'s discovery is frozen for the given scope.

    ``as_of=None`` (validation/flat paths): run-level ``discovery_frozen``.
    ``as_of`` set: membership in ``discovery_frozen_points``.

    Consults the module-level cache first.  Once ``True`` is determined for a
    given ``(run_id, as_of)`` scope it is cached permanently (freeze is one-way).
    """
    key = (run_id, as_of)
    if _frozen_status_cache.get(key) is True:
        return True

    # Cache miss — load manifest
    from . import runs as _runs  # local import to avoid circular at module level

    manifest = _runs.load_manifest(run_id)
    if manifest is None:
        return False  # unknown run; don't enforce

    if as_of is None:
        frozen = bool(manifest.discovery_frozen)
    else:
        frozen = as_of in (manifest.discovery_frozen_points or {})

    if frozen:
        _frozen_status_cache[key] = True
        return True
    return False


def _check_prefreeeze_guard(path: Path) -> None:
    """Raise ``LeakageError`` if *path* is a validation artifact on an unfrozen run.

    This is the read-time enforcement for OI-3: validation/ data must not be
    read until discovery is frozen (io_contracts §16).

    The validation read-gate still keys on the run-level frozen flag (as_of=None);
    validation/ artifacts are future data gated by run-level discovery_frozen.
    Per-point freeze status is computed (for cache correctness) but does NOT
    block discovery reads — discovery reads remain never-blocked.

    Non-run paths (e.g. test tmp dirs) are skipped silently.

    Raises
    ------
    LeakageError
        When path is under validation/ and the owning run is not yet frozen.
    """
    run_id = _get_run_id_from_path(path)
    if run_id is None:
        return  # not a run artifact path — no enforcement

    # Validation gate is run-level (as_of=None).  Per-point status is tracked via
    # a distinct cache key so per-point freezes never poison the run-level entry.
    frozen = _is_frozen(run_id, as_of=None)
    assert_read_allowed(path, frozen)


def clear_frozen_cache() -> None:
    """Flush the per-run frozen-status cache.

    Useful in tests that create and freeze runs in the same session so the
    cache does not carry stale state between test cases.
    """
    _frozen_status_cache.clear()


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
        Raises LeakageError if path is under validation/ and run not yet frozen.
        """
        _check_prefreeeze_guard(path)  # OI-3: enforce pre-freeze read boundary

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
        Raises LeakageError if path is under validation/ and run not yet frozen.
        """
        _check_prefreeeze_guard(path)  # OI-3: enforce pre-freeze read boundary

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


# Re-export LeakageError so callers can import from run_cache if needed.
__all__ = [
    "load_json",
    "load_parquet_rows",
    "clear",
    "cache_size",
    "read_count",
    "reset_read_count",
    "clear_frozen_cache",
    "LeakageError",
]
