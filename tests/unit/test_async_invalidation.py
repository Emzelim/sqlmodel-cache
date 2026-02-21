"""Unit tests for async invalidation — Story 5.3.

Verifies that after_flush, after_commit (async), and after_rollback work
correctly when using AsyncSession with an async transport.

Uses in-memory SQLite via aiosqlite + AsyncSession to provide the greenlet
context that await_only() requires inside _async_after_commit_handler.

Restricted to asyncio backend (aiosqlite incompatible with trio).
Each test is fully isolated via the autouse reset_cache fixture.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes import FakeAsyncTransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._config import CacheConfig
from sqlmodel_cache._invalidation import (
    _PENDING_KEY,
)


# aiosqlite only works under asyncio — restrict all anyio tests in this file
@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class AsyncWidget(SQLModel, table=True):
    """Cache-enabled model for async invalidation tests."""

    __tablename__ = "async_widget_inv"
    __cache_config__ = CacheConfig()

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class AsyncPlainWidget(SQLModel, table=True):
    """Model WITHOUT __cache_config__ — must NOT collect pending keys."""

    __tablename__ = "async_plain_widget_inv"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


# ---------------------------------------------------------------------------
# Tracking async transport
# ---------------------------------------------------------------------------


class TrackingAsyncTransport(FakeAsyncTransport):
    def __init__(self) -> None:
        super().__init__()
        self.delete_calls: list[tuple[str, ...]] = []

    async def delete(self, *keys: str) -> None:
        self.delete_calls.append(keys)
        await super().delete(*keys)


class ErrorDeleteTransport(FakeAsyncTransport):
    """Transport where delete() always raises."""

    async def delete(self, *keys: str) -> None:
        raise ConnectionError("Redis delete failed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_engine():
    """In-memory async SQLite engine."""
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
    SQLModelCache.configure(transport=transport)
    return transport


# ---------------------------------------------------------------------------
# AC1 — after_flush populates pending_invalidation (sync handler, fires for async too)
# ---------------------------------------------------------------------------


class TestAfterFlush:
    """after_flush handler collects dirty/deleted cache-enabled instances."""

    @pytest.mark.anyio
    async def test_flush_populates_pending_for_dirty(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC1: after AsyncSession.flush(), sync session info has pending keys."""
        # Insert then modify via two separate sessions
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=1, name="original"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 1)
            assert widget is not None
            widget.name = "modified"
            await session.flush()
            # Check pending keys in sync_session.info
            pending = session.sync_session.info.get(_PENDING_KEY, [])
            assert any(
                pk_dict.get("id") == 1 for _, pk_dict in pending
            ), f"Expected pending keys for AsyncWidget id=1, got: {pending}"

    @pytest.mark.anyio
    async def test_flush_skips_plain_model(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """after_flush does NOT collect instances without __cache_config__."""
        async with AsyncSession(async_engine) as session:
            session.add(AsyncPlainWidget(id=10, name="plain"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncPlainWidget, 10)
            assert widget is not None
            widget.name = "changed"
            await session.flush()
            pending = session.sync_session.info.get(_PENDING_KEY, [])
            assert pending == []

    @pytest.mark.anyio
    async def test_flush_collects_deleted(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """after_flush collects deleted instances too."""
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=2, name="to-delete"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 2)
            assert widget is not None
            await session.delete(widget)
            await session.flush()
            pending = session.sync_session.info.get(_PENDING_KEY, [])
            assert any(pk_dict.get("id") == 2 for _, pk_dict in pending)


# ---------------------------------------------------------------------------
# AC2 — after_commit (async) deletes collected keys
# ---------------------------------------------------------------------------


class TestAsyncAfterCommit:
    """_async_after_commit_handler: await transport.delete() called for each pending key."""

    @pytest.mark.anyio
    async def test_commit_deletes_pending_key(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC2: transport.delete() called with cache key after async commit."""
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=3, name="pre-commit"))
            await session.commit()

        # Modify and commit — should trigger invalidation
        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 3)
            assert widget is not None
            widget.name = "post-commit"
            await session.commit()

        deleted_keys = [key for tup in configured.delete_calls for key in tup]
        assert "sqlmodelcache:AsyncWidget:id=3" in deleted_keys

    @pytest.mark.anyio
    async def test_commit_clears_pending_after_delete(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """Pending list is empty after commit — no leak into next transaction."""
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=4, name="init"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 4)
            assert widget is not None
            widget.name = "updated"
            await session.commit()
            pending = session.sync_session.info.get(_PENDING_KEY, [])
            assert pending == []

    @pytest.mark.anyio
    async def test_commit_no_delete_when_no_dirty(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """No-op commit (no dirty/deleted rows) must not call transport.delete()."""
        async with AsyncSession(async_engine) as session:
            # Commit with no mutations
            await session.commit()

        assert configured.delete_calls == []

    @pytest.mark.anyio
    async def test_commit_no_delete_when_enabled_false(
        self, async_engine: Any
    ) -> None:
        """enabled=False: transport.delete() NOT called even on commit."""
        transport = TrackingAsyncTransport()
        SQLModelCache.configure(transport=transport, enabled=False)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=5, name="noop"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 5)
            assert widget is not None
            widget.name = "changed"
            await session.commit()

        assert transport.delete_calls == []


# ---------------------------------------------------------------------------
# AC3 — after_rollback clears without deleting
# ---------------------------------------------------------------------------


class TestAfterRollback:
    @pytest.mark.anyio
    async def test_rollback_clears_pending_without_delete(
        self, configured: TrackingAsyncTransport, async_engine: Any
    ) -> None:
        """AC3: rollback clears pending list and does NOT call transport.delete()."""
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=6, name="pre-rollback"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            widget = await session.get(AsyncWidget, 6)
            assert widget is not None
            widget.name = "modified"
            await session.flush()  # flush populates pending

            pending_before = list(session.sync_session.info.get(_PENDING_KEY, []))
            assert pending_before, "Expected pending keys after flush"

            await session.rollback()

            pending_after = session.sync_session.info.get(_PENDING_KEY, [])
            assert pending_after == [], "Pending list must be empty after rollback"

        # transport.delete() must not have been called
        assert configured.delete_calls == []


# ---------------------------------------------------------------------------
# AC4 — Failure suppressed (fail-open) with warning logged
# ---------------------------------------------------------------------------


class TestFailOpen:
    @pytest.mark.anyio
    async def test_delete_failure_suppressed_with_warning(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC4: transport.delete() raises → no exception propagates, warning logged."""
        transport = ErrorDeleteTransport()
        SQLModelCache.configure(transport=transport)

        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=7, name="fail-delete"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                widget = await session.get(AsyncWidget, 7)
                assert widget is not None
                widget.name = "changed"
                # Should NOT raise even though transport.delete raises
                await session.commit()

        assert any("async_invalidation_failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_delete_failure_continues_remaining_keys(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC4: after one delete fails, remaining keys are still attempted."""
        call_count = 0
        fail_count = 0

        class PartialFailTransport(FakeAsyncTransport):
            async def delete(self, *keys: str) -> None:
                nonlocal call_count, fail_count
                call_count += 1
                if call_count == 1:
                    fail_count += 1
                    raise ConnectionError("first key fails")
                await super().delete(*keys)

        transport = PartialFailTransport()
        SQLModelCache.configure(transport=transport)

        # Insert two widgets to dirty in same session
        async with AsyncSession(async_engine) as session:
            session.add(AsyncWidget(id=8, name="a"))
            session.add(AsyncWidget(id=9, name="b"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                w1 = await session.get(AsyncWidget, 8)
                w2 = await session.get(AsyncWidget, 9)
                assert w1 and w2
                w1.name = "a-modified"
                w2.name = "b-modified"
                await session.commit()

        # first key fails, second key should still be attempted
        assert call_count == 2, f"Expected 2 delete calls, got {call_count}"
        assert fail_count == 1
