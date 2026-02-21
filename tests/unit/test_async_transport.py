"""Unit tests for Story 5.1: AsyncCacheTransport Protocol & RedisAsyncTransport.

Tests use AsyncMock to avoid a real Redis connection or event loop startup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fakes import FakeAsyncTransport, FakeTransport

# ---------------------------------------------------------------------------
# Task 6.1-6.3 — RedisAsyncTransport method delegation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_delegates_to_client() -> None:
    """RedisAsyncTransport.get() awaits client.get(key) and returns its value (AC2, AC9)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    mock_client = AsyncMock()
    mock_client.get.return_value = b"cached_value"
    transport = RedisAsyncTransport(mock_client)

    result = await transport.get("mykey")

    mock_client.get.assert_awaited_once_with("mykey")
    assert result == b"cached_value"


@pytest.mark.anyio
async def test_get_returns_none_when_client_returns_none() -> None:
    """RedisAsyncTransport.get() propagates None from client (cache miss)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    mock_client = AsyncMock()
    mock_client.get.return_value = None
    transport = RedisAsyncTransport(mock_client)

    result = await transport.get("missing_key")

    assert result is None


@pytest.mark.anyio
async def test_set_delegates_to_client_with_ex() -> None:
    """RedisAsyncTransport.set() awaits client.set(key, value, ex=ttl) (AC2, AC9)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    mock_client = AsyncMock()
    transport = RedisAsyncTransport(mock_client)

    await transport.set("mykey", b"myvalue", 300)

    mock_client.set.assert_awaited_once_with("mykey", b"myvalue", ex=300)


@pytest.mark.anyio
async def test_delete_delegates_to_client_with_keys() -> None:
    """RedisAsyncTransport.delete() awaits client.delete(*keys) (AC2, AC9)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    mock_client = AsyncMock()
    transport = RedisAsyncTransport(mock_client)

    await transport.delete("key1", "key2")

    mock_client.delete.assert_awaited_once_with("key1", "key2")


@pytest.mark.anyio
async def test_delete_skips_when_no_keys() -> None:
    """RedisAsyncTransport.delete() does NOT call client.delete() when called with no keys (AC2)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    mock_client = AsyncMock()
    transport = RedisAsyncTransport(mock_client)

    await transport.delete()

    mock_client.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Task 6.4 — No event loop at import time (AC3)
# ---------------------------------------------------------------------------


def test_import_does_not_raise_event_loop_error() -> None:
    """Importing sqlmodel_cache does not raise RuntimeError about no running event loop (AC3)."""
    # If there were a top-level asyncio.get_event_loop() call, this would fail
    import sqlmodel_cache  # noqa: F401 — just verifying import is side-effect free

    # Explicitly verify RedisAsyncTransport is importable from transport subpackage
    from sqlmodel_cache.transport import RedisAsyncTransport  # noqa: F401

    # No assertion needed — test passes if no exception (RuntimeError, ImportError, etc.)


# ---------------------------------------------------------------------------
# Task 6.5 — Public re-export (AC4)
# ---------------------------------------------------------------------------


def test_redis_async_transport_importable_from_transport_package() -> None:
    """RedisAsyncTransport is importable from sqlmodel_cache.transport (AC4)."""
    from sqlmodel_cache.transport import RedisAsyncTransport

    assert RedisAsyncTransport is not None


def test_redis_async_transport_in_transport_all() -> None:
    """RedisAsyncTransport is in sqlmodel_cache.transport.__all__."""
    import sqlmodel_cache.transport as transport_pkg

    assert "RedisAsyncTransport" in transport_pkg.__all__


# ---------------------------------------------------------------------------
# Task 6.6 — configure() accepts async transport (AC5)
# ---------------------------------------------------------------------------


def test_configure_accepts_async_transport() -> None:
    """SQLModelCache.configure() stores FakeAsyncTransport correctly (AC5)."""
    from sqlmodel_cache import SQLModelCache, _state

    transport = FakeAsyncTransport()
    SQLModelCache.configure(transport=transport)  # type: ignore[arg-type]

    assert _state.get_config().transport is transport


# ---------------------------------------------------------------------------
# Task 6.7-6.8 — is_async_transport helper
# ---------------------------------------------------------------------------


def test_is_async_transport_returns_true_for_async() -> None:
    """_state.is_async_transport() returns True for FakeAsyncTransport."""
    from sqlmodel_cache._state import is_async_transport

    assert is_async_transport(FakeAsyncTransport()) is True


def test_is_async_transport_returns_false_for_sync() -> None:
    """_state.is_async_transport() returns False for FakeTransport."""
    from sqlmodel_cache._state import is_async_transport

    assert is_async_transport(FakeTransport()) is False


def test_is_async_transport_returns_false_for_non_transport() -> None:
    """_state.is_async_transport() returns False for arbitrary objects."""
    from sqlmodel_cache._state import is_async_transport

    assert is_async_transport(object()) is False
    assert is_async_transport("not_a_transport") is False


# ---------------------------------------------------------------------------
# FakeAsyncTransport helpers (Task 5.2-5.3)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fake_async_transport_get_miss() -> None:
    """FakeAsyncTransport.get() returns None for missing keys."""
    transport = FakeAsyncTransport()
    result = await transport.get("missing")
    assert result is None


@pytest.mark.anyio
async def test_fake_async_transport_set_get_roundtrip() -> None:
    """FakeAsyncTransport stores and retrieves bytes correctly."""
    transport = FakeAsyncTransport()
    await transport.set("k", b"value", 300)
    assert await transport.get("k") == b"value"


@pytest.mark.anyio
async def test_fake_async_transport_delete() -> None:
    """FakeAsyncTransport.delete() removes keys."""
    transport = FakeAsyncTransport()
    await transport.set("k", b"v", 300)
    await transport.delete("k")
    assert await transport.get("k") is None


def test_fake_async_transport_contains() -> None:
    """FakeAsyncTransport.__contains__ works for membership tests."""
    import asyncio

    transport = FakeAsyncTransport()
    asyncio.run(transport.set("k", b"v", 300))
    assert "k" in transport
    assert "missing" not in transport


def test_fake_async_transport_clear() -> None:
    """FakeAsyncTransport.clear() removes all entries."""
    import asyncio

    transport = FakeAsyncTransport()
    asyncio.run(transport.set("k", b"v", 300))
    transport.clear()
    assert "k" not in transport
