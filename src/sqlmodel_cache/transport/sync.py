"""RedisSyncTransport — synchronous redis.Redis wrapper.

Only this module imports ``redis`` from the core library.  All other
core modules depend on the ``CacheTransport`` Protocol defined in
``transport/_protocols.py``.
"""
from __future__ import annotations

import redis

__all__ = ["RedisSyncTransport"]


class RedisSyncTransport:
    """Synchronous Redis transport wrapping ``redis.Redis``.

    Satisfies :class:`~sqlmodel_cache.transport._protocols.CacheTransport`
    structurally — no inheritance required.

    Args:
        client: A configured ``redis.Redis`` instance.
    """

    def __init__(self, client: redis.Redis) -> None:  # type: ignore[type-arg]
        self._client = client

    def get(self, key: str) -> bytes | None:
        """Return cached bytes or ``None`` if the key does not exist."""
        return self._client.get(key)  # type: ignore[return-value]

    def set(self, key: str, value: bytes, ttl: int) -> None:
        """Store ``value`` under ``key`` with an expiry of ``ttl`` seconds."""
        self._client.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> None:
        """Remove one or more keys from the cache."""
        if keys:
            self._client.delete(*keys)
