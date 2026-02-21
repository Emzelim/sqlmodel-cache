"""Fake in-memory transport implementations for unit testing.

These classes structurally match the CacheTransport and AsyncCacheTransport
protocols defined in transport/_protocols.py — no inheritance required.
"""
from __future__ import annotations


class FakeTransport:
    """In-memory dict-backed synchronous transport for unit tests.

    Satisfies the CacheTransport Protocol structurally::

        transport = FakeTransport()
        SQLModelCache.configure(transport=transport)
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self._store[key] = value

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Helper for test setup/teardown — not part of the protocol."""
        self._store.clear()

    def __contains__(self, key: str) -> bool:
        """Helper: allows `assert key in transport` in tests."""
        return key in self._store


class FakeAsyncTransport:
    """In-memory dict-backed asynchronous transport for unit tests.

    Satisfies the AsyncCacheTransport Protocol structurally.
    Full integration in Story 5.1.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        self._store[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Helper for test teardown — not part of the protocol."""
        self._store.clear()

    def __contains__(self, key: str) -> bool:
        """Helper: allows ``assert key in transport`` in tests."""
        return key in self._store
