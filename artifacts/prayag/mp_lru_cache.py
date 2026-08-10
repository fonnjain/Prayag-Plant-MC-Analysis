"""Lightweight thread-safe LRU cache for in-process engine results.

Extracted into its own module so it can be unit-tested without importing the
full Flask application (which requires live Google credentials and a database).
"""
from __future__ import annotations

import threading
from collections import OrderedDict


class BoundedLRUCache:
    """Thread-safe LRU dict with a fixed maximum entry count.

    Evicts the least-recently-used entry when the cap is reached, so the
    in-process footprint stays bounded regardless of how many plan runs are
    performed in one gunicorn worker lifetime.

    Every public method acquires the lock for its entire duration, so there is
    no TOCTOU window between a membership test and a subsequent read — callers
    must use ``get()`` (not ``__contains__`` + ``__getitem__``) to retrieve a
    value without risk of eviction between the two operations.

    Supports: .get(), ``in``, ``[]`` read/write, len().
    """

    _SENTINEL = object()

    def __init__(self, maxsize: int = 50) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize!r}")
        self._maxsize = maxsize
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, key, default=None):
        """Return the value for *key* and promote it to MRU, or *default*."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return default

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key):
        with self._lock:
            self._data.move_to_end(key)
            return self._data[key]

    # ------------------------------------------------------------------
    # Write helper
    # ------------------------------------------------------------------

    def __setitem__(self, key, value) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            # Evict LRU entries until within cap.
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._data.clear()

    @property
    def maxsize(self) -> int:
        return self._maxsize
