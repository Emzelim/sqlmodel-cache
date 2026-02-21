"""Structural Protocol interfaces for cache transport implementations.

Core library modules import only from this file — never from ``sync.py`` or
``async_.py`` directly.  This keeps the core transport-agnostic and allows
``FakeTransport`` in tests to satisfy the protocol via structural subtyping
(no inheritance required).
"""
from __future__ import annotations

from typing import Protocol

__all__ = ["AsyncCacheTransport", "CacheTransport"]


class CacheTransport(Protocol):
    """Synchronous cache transport protocol.

    Any object implementing these three methods satisfies this protocol —
    no inheritance from ``CacheTransport`` is required.
    """

    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl: int) -> None: ...
    def delete(self, *keys: str) -> None: ...


class AsyncCacheTransport(Protocol):
    """Asynchronous cache transport protocol."""

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: int) -> None: ...
    async def delete(self, *keys: str) -> None: ...
