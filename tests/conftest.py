"""Shared pytest fixtures for sqlmodel-cache test suite.

Unit fixtures (no Redis) live here.
Integration fixtures (redis_url, async_engine, async_session, etc.) are below.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def reset_cache() -> Generator[None, None, None]:
    """Ensure SQLModelCache is fully reset after every test.

    Removes the do_orm_execute event listener and clears LibraryConfig so
    tests cannot bleed state into one another.
    """
    yield
    from sqlmodel_cache import SQLModelCache

    SQLModelCache.reset()


# ---------------------------------------------------------------------------
# Integration fixtures — Redis (sync, via testcontainers)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def redis_url() -> Generator[str, None, None]:
    """Yield a Redis connection URL.

    In CI the ``REDIS_URL`` environment variable is set by the GitHub Actions
    ``services: redis:`` sidecar.  Locally (and when the variable is absent),
    a throwaway container is started via testcontainers.
    """
    import os

    url = os.environ.get("REDIS_URL")
    if url:
        yield url
        return

    from testcontainers.redis import RedisContainer

    with RedisContainer() as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}"


@pytest.fixture(scope="function")
def sync_redis_transport(redis_url: str) -> Any:
    """RedisSyncTransport wired to the testcontainers Redis instance."""
    import redis as _redis

    from sqlmodel_cache.transport import RedisSyncTransport

    client = _redis.Redis.from_url(redis_url)
    transport = RedisSyncTransport(client)
    yield transport
    client.close()


@pytest.fixture(scope="function")
def async_redis_client(redis_url: str) -> Any:
    """Sync-created async Redis client (closed via sync interface after test)."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    return aioredis.Redis.from_url(redis_url)


@pytest.fixture(scope="function")
def async_redis_transport(async_redis_client: Any) -> Any:
    """RedisAsyncTransport wired to the testcontainers Redis instance."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    return RedisAsyncTransport(async_redis_client)


# ---------------------------------------------------------------------------
# Integration fixtures — Async SQLite engine + session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def async_engine() -> AsyncGenerator[Any, None]:
    """Async SQLite engine with in-memory DB; metadata created on startup."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def async_session(async_engine: Any) -> AsyncGenerator[Any, None]:
    """AsyncSession bound to the in-memory SQLite async engine."""
    from sqlalchemy.ext.asyncio import AsyncSession

    session = AsyncSession(async_engine)
    yield session
    await session.close()
