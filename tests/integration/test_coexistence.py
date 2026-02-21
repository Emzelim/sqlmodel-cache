"""Integration tests for sync/async transport co-existence — Story 5.5.

Verifies:
 AC1 — No transport cross-contamination: each session type calls only its
        own transport, never the other.
 AC2 — Sync commit invalidates key visible to async transport (shared Redis).
 AC3 — reset() leaves no ghost listeners on Session or AsyncSession.

All tests are marked ``@pytest.mark.integration`` and therefore excluded from
the unit-only ``hatch run test`` / ``pytest tests/unit/`` run.

Async tests additionally carry ``@pytest.mark.anyio``.  aiosqlite only works
under asyncio, so the module-level ``anyio_backend`` fixture pins to asyncio.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel, create_engine
from sqlmodel import Session as SASession

# Make 'fakes' importable (it lives in tests/unit/)
sys.path.insert(0, str(Path(__file__).parent.parent / "unit"))
from fakes import FakeAsyncTransport, FakeTransport

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._config import CacheConfig
from sqlmodel_cache._interceptor import (
    _async_cache_on_execute,
    _cache_on_execute,
)
from sqlmodel_cache._invalidation import (
    _after_commit_handler,
    _after_flush_handler,
    _after_rollback_handler,
    _async_after_commit_handler,
)
from sqlmodel_cache.keys import build_key
from sqlmodel_cache.serializer import serialize

# ---------------------------------------------------------------------------
# Local tracking wrappers — record get() calls for AC1 cross-contamination check
# ---------------------------------------------------------------------------


class TrackingFakeTransport(FakeTransport):
    """FakeTransport that records keys passed to get()."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return super().get(key)


class TrackingFakeAsyncTransport(FakeAsyncTransport):
    """FakeAsyncTransport that records keys passed to get()."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return await super().get(key)

# ---------------------------------------------------------------------------
# anyio backend — aiosqlite requires asyncio, not trio
# ---------------------------------------------------------------------------


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Test models — unique table names to avoid conflicts with unit test tables
# ---------------------------------------------------------------------------


class CoexistHero(SQLModel, table=True):
    """Model used exclusively by coexistence integration tests."""

    __tablename__ = "coexist_hero_it"
    __cache_config__ = CacheConfig()

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


# ---------------------------------------------------------------------------
# Local fixtures — sync SQLite engine / session
# ---------------------------------------------------------------------------


@pytest.fixture()
def sync_engine() -> Any:
    """In-memory sync SQLite engine with metadata pre-created."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# AC1 — No transport cross-contamination (uses Fakes, no real Redis needed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAC1NoTransportCrossContamination:
    """AC1: sync and async transports are mutually exclusive per configure()."""

    def test_sync_transport_only_called_by_sync_session(self, sync_engine: Any) -> None:
        """With sync transport configured, sync session.get() uses sync transport only."""
        sync_transport = TrackingFakeTransport()
        async_transport = TrackingFakeAsyncTransport()

        SQLModelCache.configure(transport=sync_transport)

        # Only sync handler should be registered
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

        with SASession(sync_engine) as session:
            session.get(CoexistHero, 1)

        # Sync transport was touched; async transport was not
        assert len(sync_transport.get_calls) == 1, "sync transport.get() should be called"
        assert len(async_transport.get_calls) == 0, "async transport must NOT be called"

    @pytest.mark.anyio
    async def test_async_transport_only_called_by_async_session(self) -> None:
        """With async transport configured, async session.get() uses async transport only."""
        sync_transport = TrackingFakeTransport()
        async_transport = TrackingFakeAsyncTransport()

        SQLModelCache.configure(transport=async_transport)

        # Only async handler should be registered
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS coexist_hero_it "
                    "(id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '')"
                )
            )

        from sqlmodel.ext.asyncio.session import AsyncSession

        async with AsyncSession(engine) as session:
            await session.get(CoexistHero, 1)

        await engine.dispose()

        # Async transport was touched; sync transport was not
        assert len(async_transport.get_calls) == 1, "async transport.get() should be called"
        assert len(sync_transport.get_calls) == 0, "sync transport must NOT be called"

    def test_reconfigure_switches_handler_exclusively(self) -> None:
        """Re-configuring from sync to async swaps all handlers atomically."""
        SQLModelCache.configure(transport=TrackingFakeTransport())
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

        SQLModelCache.configure(transport=TrackingFakeAsyncTransport())
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)


# ---------------------------------------------------------------------------
# AC2 — Sync commit invalidates shared Redis key; async session sees cache miss
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_ac2_sync_commit_invalidates_for_async_session(
    redis_url: str,
    sync_redis_transport: Any,
    async_redis_transport: Any,
    async_redis_client: Any,
    sync_engine: Any,
) -> None:
    """Sync session commit → key deleted from shared Redis → async session sees miss.

    Steps:
    1. Insert CoexistHero(id=2) into the sync SQLite DB (no cache yet).
    2. Pre-seed Redis with a serialized CoexistHero at the expected cache key,
       simulating a previously cached read.
    3. Configure RedisSyncTransport → open sync session → retrieve the hero
       (now in session.dirty after modification) → commit → invalidation
       deletes the key from Redis.
    4. Verify the key no longer exists in Redis (async transport would see a
       cache miss and issue a DB query).
    """
    hero_id = 2
    cache_key = build_key(CoexistHero, {"id": hero_id})

    # Step 1: Insert the hero into the sync DB (separate baseline session)
    with SASession(sync_engine) as setup_session:
        setup_session.add(CoexistHero(id=hero_id, name="Baseline Anya"))
        setup_session.commit()

    # Step 2: Pre-seed Redis to simulate an existing cached entry
    hero = CoexistHero(id=hero_id, name="Baseline Anya")
    raw = serialize(hero)
    await async_redis_client.set(cache_key, raw)
    assert await async_redis_client.get(cache_key) is not None, "Pre-seed must succeed"

    # Step 3: Configure sync transport → modify hero → commit → invalidation fires
    SQLModelCache.configure(transport=sync_redis_transport)

    with SASession(sync_engine) as session:
        existing = session.get(CoexistHero, hero_id)
        assert existing is not None, "Hero must exist in DB"
        existing.name = "Updated Anya"  # marks instance as dirty
        session.commit()
        # after_flush collects (CoexistHero, {id: hero_id}) from session.dirty
        # after_commit calls sync_transport.delete(cache_key) → key removed from Redis

    # Step 4: Verify key gone from Redis (invalidation crossed transport boundary)
    value = await async_redis_client.get(cache_key)
    assert value is None, (
        f"Expected cache key '{cache_key}' to be deleted after sync commit, "
        f"but it still exists in Redis."
    )


# ---------------------------------------------------------------------------
# AC3 — reset() removes all listeners (integration-level sanity check)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAC3ResetRemovesAllListeners:
    """AC3 (integration): reset() leaves no ghost listeners after async configure."""

    def test_reset_removes_async_interceptor(self) -> None:
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_reset_removes_async_commit_handler(self) -> None:
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "after_commit", _async_after_commit_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_commit", _async_after_commit_handler)

    def test_reset_removes_flush_and_rollback_handlers(self) -> None:
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "after_flush", _after_flush_handler)
        assert event.contains(SASession, "after_rollback", _after_rollback_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_flush", _after_flush_handler)
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)

    def test_reset_clears_all_six_handler_slots(self) -> None:
        """All 6 handler slots are empty after reset()."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        SQLModelCache.reset()
        dead = [
            event.contains(SASession, "do_orm_execute", _cache_on_execute),
            event.contains(SASession, "do_orm_execute", _async_cache_on_execute),
            event.contains(SASession, "after_flush", _after_flush_handler),
            event.contains(SASession, "after_commit", _after_commit_handler),
            event.contains(SASession, "after_commit", _async_after_commit_handler),
            event.contains(SASession, "after_rollback", _after_rollback_handler),
        ]
        assert not any(dead), f"Unexpected live listeners after reset(): {dead}"

    def test_reset_is_idempotent_no_error_if_called_twice(self) -> None:
        SQLModelCache.configure(transport=FakeAsyncTransport())
        SQLModelCache.reset()
        SQLModelCache.reset()  # second call must not raise
