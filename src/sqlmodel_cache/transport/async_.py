"""RedisAsyncTransport — redis.asyncio.Redis wrapper.

This module is intentionally named ``async_.py`` (trailing underscore) because
``async`` is a reserved keyword in Python and cannot be used as a module name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio

__all__ = ["RedisAsyncTransport"]


class RedisAsyncTransport:
    """Asynchronous Redis transport wrapping ``redis.asyncio.Redis``.

    Satisfies :class:`~sqlmodel_cache.transport._protocols.AsyncCacheTransport`
    structurally — no inheritance required.

    Args:
        client: A configured ``redis.asyncio.Redis`` instance.

    Note:
        ``redis.asyncio`` is NOT imported at module level — this ensures no
        event loop is required at import time (NFR-COMPAT-5).
    """

    def __init__(self, client: redis.asyncio.Redis) -> None:  # type: ignore[name-defined]
        self._client = client

    async def get(self, key: str) -> bytes | None:
        """Return cached bytes or ``None`` if the key does not exist."""
        return await self._client.get(key)  # type: ignore[no-any-return]

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        """Store ``value`` under ``key`` with an expiry of ``ttl`` seconds."""
        await self._client.set(key, value, ex=ttl)

    async def delete(self, *keys: str) -> None:
        """Remove one or more keys from the cache."""
        if keys:
            await self._client.delete(*keys)
