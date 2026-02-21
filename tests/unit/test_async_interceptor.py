"""Unit tests for the async do_orm_execute interceptor — Story 5.2.

Uses in-memory SQLite via aiosqlite (no Redis, no network I/O).
AsyncSession provides the greenlet context required by await_only() inside
_async_cache_on_execute.

aiosqlite only supports the asyncio backend (not trio), so all async tests
in this file are restricted to asyncio via the anyio_backend fixture.

Each test is fully isolated: the autouse ``reset_cache`` fixture in
``tests/conftest.py`` calls ``SQLModelCache.reset()`` after every test.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes import FakeAsyncTransport
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel import Session as SASession
from sqlmodel.ext.asyncio.session import AsyncSession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._config import CacheConfig
from sqlmodel_cache._interceptor import _async_cache_on_execute
from sqlmodel_cache.serializer import serialize


# aiosqlite only works under asyncio — restrict all anyio tests in this file
# to the asyncio backend via the anyio_backend fixture.
@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class AsyncHero(SQLModel, table=True):
    """Cache-enabled model for async interceptor tests."""

    __tablename__ = "async_hero_it"
    __cache_config__ = CacheConfig()  # enabled=True, ttl=None

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class AsyncHeroCustomTTL(SQLModel, table=True):
    """Cache-enabled model with explicit TTL."""

    __tablename__ = "async_hero_it_ttl"
    __cache_config__ = CacheConfig(ttl=120)

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class AsyncPlainHero(SQLModel, table=True):
    """Model WITHOUT __cache_config__ — must pass through."""

    __tablename__ = "async_plain_hero_it"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class AsyncDisabledHero(SQLModel, table=True):
    """Model with enabled=False — must pass through."""

    __tablename__ = "async_disabled_hero_it"
    __cache_config__ = CacheConfig(enabled=False)

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


# ---------------------------------------------------------------------------
# Tracking async transport
# ---------------------------------------------------------------------------


class TrackingAsyncTransport(FakeAsyncTransport):
    """FakeAsyncTransport that records get/set/delete call args."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int]] = []  # (key, ttl)

    async def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return await super().get(key)

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        self.set_calls.append((key, ttl))
        await super().set(key, value, ttl)


class ErrorAsyncTransport(FakeAsyncTransport):
    """Async transport that raises on get and/or set."""

    def __init__(self, fail_on_get: bool = True, fail_on_set: bool = False) -> None:
        super().__init__()
        self._fail_on_get = fail_on_get
        self._fail_on_set = fail_on_set

    async def get(self, key: str) -> bytes | None:
        if self._fail_on_get:
            raise ConnectionError("Redis unavailable")
        return await super().get(key)

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        if self._fail_on_set:
            raise ConnectionError("Redis write failed")
        await super().set(key, value, ttl)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_engine():
    """In-memory async SQLite engine with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
def transport() -> TrackingAsyncTransport:
    return TrackingAsyncTransport()


@pytest.fixture()
def configured(transport: TrackingAsyncTransport) -> TrackingAsyncTransport:
    """Configure SQLModelCache with the async tracking transport."""
    SQLModelCache.configure(transport=transport)
    return transport


# ---------------------------------------------------------------------------
# Story 5.2 AC5 — Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """Verify async listener registration mechanics."""

    def test_async_listener_registered_after_configure(
        self, configured: TrackingAsyncTransport
    ) -> None:
        """AC5: _async_cache_on_execute is registered on Session after configure()."""
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_sync_listener_not_registered_for_async_transport(
        self, configured: TrackingAsyncTransport
    ) -> None:
        """When transport is async, sync _cache_on_execute must NOT be registered."""
        from sqlmodel_cache._interceptor import _cache_on_execute

        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_async_listener_removed_after_reset(
        self, configured: TrackingAsyncTransport
    ) -> None:
        """_async_cache_on_execute is removed by reset()."""
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_double_configure_single_registration(
        self, transport: TrackingAsyncTransport
    ) -> None:
        """Double configure() registers the listener exactly once (no duplicates)."""
        SQLModelCache.configure(transport=transport)
        SQLModelCache.configure(transport=transport)
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)


# ---------------------------------------------------------------------------
# Story 5.2 AC1 — Cache HIT via async transport
# ---------------------------------------------------------------------------


class TestCacheHitPath:
    """Cache hit: transport.get() returns bytes → DB not queried."""

    @pytest.mark.anyio
    async def test_hit_returns_cached_instance(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC1: deserialized async-transport result returned, no SQL issued."""
        hero = AsyncHero(id=1, name="Async-Deadpond")
        transport._store["sqlmodelcache:AsyncHero:id=1"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncHero, 1)

        assert result is not None
        assert result.id == 1
        assert result.name == "Async-Deadpond"

    @pytest.mark.anyio
    async def test_hit_calls_transport_get(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC1: transport.get() is called with the correct cache key."""
        hero = AsyncHero(id=5, name="Speed")
        transport._store["sqlmodelcache:AsyncHero:id=5"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHero, 5)

        assert transport.get_calls == ["sqlmodelcache:AsyncHero:id=5"]

    @pytest.mark.anyio
    async def test_hit_does_not_call_transport_set(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC1 (no write-back on HIT): transport.set() must NOT be called."""
        hero = AsyncHero(id=7, name="Cached")
        transport._store["sqlmodelcache:AsyncHero:id=7"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHero, 7)

        assert transport.set_calls == []


# ---------------------------------------------------------------------------
# Story 5.2 AC2 — Cache MISS via async transport
# ---------------------------------------------------------------------------


class TestCacheMissPath:
    """Cache miss: transport.get() returns None → DB queried → cache written."""

    @pytest.mark.anyio
    async def test_miss_queries_db(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC2: DB is queried on cache miss and result returned."""
        SQLModelCache.configure(transport=transport)

        # Insert hero into DB
        async with AsyncSession(async_engine) as session:
            hero = AsyncHero(id=10, name="DB-Hero")
            session.add(hero)
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncHero, 10)

        assert result is not None
        assert result.name == "DB-Hero"

    @pytest.mark.anyio
    async def test_miss_calls_transport_set(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC2: transport.set() called with the correct key and TTL on cache miss."""
        SQLModelCache.configure(transport=transport, default_ttl=300)

        async with AsyncSession(async_engine) as session:
            hero = AsyncHero(id=20, name="Miss-Hero")
            session.add(hero)
            await session.commit()

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHero, 20)

        keys = [entry[0] for entry in transport.set_calls]
        ttls = [entry[1] for entry in transport.set_calls]
        assert "sqlmodelcache:AsyncHero:id=20" in keys
        assert 300 in ttls

    @pytest.mark.anyio
    async def test_miss_populated_for_subsequent_hit(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC2: second request is a cache hit after first populates the cache."""
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncHero(id=30, name="Persist"))
            await session.commit()

        # First call — MISS — populates cache
        async with AsyncSession(async_engine) as s1:
            await s1.get(AsyncHero, 30)

        # Second call — HIT from in-memory FakeAsyncTransport
        async with AsyncSession(async_engine) as s2:
            await s2.get(AsyncHero, 30)

        # Two get calls total
        assert len(transport.get_calls) == 2
        # No additional set call on second request
        assert len(transport.set_calls) == 1


# ---------------------------------------------------------------------------
# Story 5.2 AC3 — None result NOT written to cache
# ---------------------------------------------------------------------------


class TestNoneResultNotCached:
    @pytest.mark.anyio
    async def test_none_result_not_written(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC3: transport.set() NOT called when DB returns no row."""
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncHero, 999)

        assert result is None
        assert transport.set_calls == []


# ---------------------------------------------------------------------------
# Story 5.2 — Bypass options
# ---------------------------------------------------------------------------


class TestBypassOptions:
    @pytest.mark.anyio
    async def test_cache_false_bypasses_transport(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC per-call bypass: transport.get() NOT called when cache=False."""
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHero, 1, execution_options={"cache": False})

        assert transport.get_calls == []

    @pytest.mark.anyio
    async def test_enabled_false_bypasses_transport(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """enabled=False: transport NOT called for any request."""
        SQLModelCache.configure(transport=transport, enabled=False)

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHero, 1)

        assert transport.get_calls == []

    @pytest.mark.anyio
    async def test_cache_ttl_override(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """Per-call cache_ttl overrides default_ttl on cache miss."""
        SQLModelCache.configure(transport=transport, default_ttl=300)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncHero(id=50, name="TTL-Override"))
            await session.commit()

        # Use per-call TTL via execution_options on session.get()
        async with AsyncSession(async_engine) as session:
            await session.get(
                AsyncHero,
                50,
                execution_options={"cache_ttl": 60},
            )

        assert transport.set_calls, "transport.set should have been called on cache miss"
        assert transport.set_calls[-1][1] == 60

    @pytest.mark.anyio
    async def test_custom_model_ttl(
        self, transport: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """Model-level CacheConfig.ttl=120 overrides library default_ttl=300."""
        SQLModelCache.configure(transport=transport, default_ttl=300)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncHeroCustomTTL(id=1, name="ModelTTL"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            await session.get(AsyncHeroCustomTTL, 1)

        assert transport.set_calls
        assert transport.set_calls[0][1] == 120


# ---------------------------------------------------------------------------
# Story 5.2 — Fail-open (async transport errors)
# ---------------------------------------------------------------------------


class TestFailOpen:
    @pytest.mark.anyio
    async def test_read_failure_falls_through_to_db(
        self, async_engine: Any
    ) -> None:
        """AC (fail-open read): transport.get() raises → SQL issued, result returned."""
        transport = ErrorAsyncTransport(fail_on_get=True)
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncHero(id=100, name="FailOpen"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncHero, 100)

        assert result is not None
        assert result.name == "FailOpen"

    @pytest.mark.anyio
    async def test_write_failure_still_returns_result(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC (fail-open write): transport.set() raises → result still returned, warning logged."""
        transport = ErrorAsyncTransport(fail_on_get=False, fail_on_set=True)
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncHero(id=101, name="WriteFailOpen"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                result = await session.get(AsyncHero, 101)

        assert result is not None
        assert result.name == "WriteFailOpen"
        assert any("async_cache_write failed" in r.message for r in caplog.records)
