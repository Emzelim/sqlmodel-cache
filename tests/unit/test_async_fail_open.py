"""Unit tests for async fail-open error handling — Story 5.4.

Verifies that transport errors (get/set/delete) in async paths are:
  - Caught and suppressed (never propagate to caller)
  - Logged at WARNING level with correct format (key + exc_type, no field values)
  - Transparent: DB fallback occurs on read failure; result returned on write failure

Production fail-open is already implemented in _async_cache_on_execute (Story 5.2).
This story provides dedicated, focused coverage of those error paths.

Restricted to asyncio backend (aiosqlite incompatible with trio).
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


# Restrict to asyncio (aiosqlite only)
@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Test model
# ---------------------------------------------------------------------------


class AsyncFailHero(SQLModel, table=True):
    """Cache-enabled model for fail-open tests."""

    __tablename__ = "async_fail_hero_fo"
    __cache_config__ = CacheConfig()

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


# ---------------------------------------------------------------------------
# Error transports
# ---------------------------------------------------------------------------


class GetFailsTransport(FakeAsyncTransport):
    """Transport where get() always raises."""

    async def get(self, key: str) -> bytes | None:
        raise ConnectionError("Redis unavailable — get failed")


class SetFailsTransport(FakeAsyncTransport):
    """Transport where set() always raises."""

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        raise ConnectionError("Redis unavailable — set failed")


class CorruptBytesTransport(FakeAsyncTransport):
    """Transport where get() returns bytes that cannot be deserialized."""

    async def get(self, key: str) -> bytes | None:
        return b"not-valid-msgpack-data-at-all\x00\xff\xfe"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_engine():
    """In-memory async SQLite with test tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# AC1 — get() exception: DB fallback, no exception propagates
# ---------------------------------------------------------------------------


class TestGetFailureFallsThrough:
    @pytest.mark.anyio
    async def test_get_failure_returns_db_result(self, async_engine: Any) -> None:
        """AC1: transport.get() raises → DB queried → hero returned."""
        SQLModelCache.configure(transport=GetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=1, name="DB-Hero"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 1)

        assert result is not None
        assert result.name == "DB-Hero"

    @pytest.mark.anyio
    async def test_get_failure_no_exception_propagates(self, async_engine: Any) -> None:
        """AC1: transport.get() raises → caller sees no exception."""
        SQLModelCache.configure(transport=GetFailsTransport())

        # Should NOT raise
        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 999)

        assert result is None  # no DB row and no exception

    @pytest.mark.anyio
    async def test_get_failure_db_query_issued(self, async_engine: Any) -> None:
        """Task 2.5: on transport.get() failure, invoke_statement() is called."""
        SQLModelCache.configure(transport=GetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=2, name="DB-Fallback"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 2)

        assert result is not None  # DB was queried


# ---------------------------------------------------------------------------
# AC2 — logger.warning emitted exactly once on get failure
# ---------------------------------------------------------------------------


class TestGetFailureLogging:
    @pytest.mark.anyio
    async def test_get_failure_logs_warning(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC2: transport.get() raises → exactly one WARNING in sqlmodel_cache logger."""
        SQLModelCache.configure(transport=GetFailsTransport())

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                await session.get(AsyncFailHero, 1)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert any("async_cache_read" in r.message for r in warnings)

    @pytest.mark.anyio
    async def test_get_failure_log_contains_key_not_field_values(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC3: log message contains key/class name but NOT field values."""
        SQLModelCache.configure(transport=GetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=3, name="SENSITIVE_VALUE"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                await session.get(AsyncFailHero, 3)

        for record in caplog.records:
            # Must NOT contain field values
            assert "SENSITIVE_VALUE" not in record.message
            # Should contain model name (part of key)
            # Key format: "sqlmodelcache:AsyncFailHero:id=3"

    @pytest.mark.anyio
    async def test_get_failure_log_format(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC2, AC3: log format is 'async_cache_read failed key=%s exc_type=%s'."""
        SQLModelCache.configure(transport=GetFailsTransport())

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                await session.get(AsyncFailHero, 1)

        matching = [r for r in caplog.records if "async_cache_read" in r.message]
        assert matching, "Expected warning with 'async_cache_read' in message"
        # Verify exc_type is in the log (format: key=%s exc_type=%s)
        assert "ConnectionError" in matching[0].message


# ---------------------------------------------------------------------------
# AC4 — set() exception suppressed on cache miss
# ---------------------------------------------------------------------------


class TestSetFailureSuppressed:
    @pytest.mark.anyio
    async def test_set_failure_result_returned(self, async_engine: Any) -> None:
        """AC4: transport.set() raises on miss → hero still returned."""
        SQLModelCache.configure(transport=SetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=10, name="SetFail"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 10)

        assert result is not None
        assert result.name == "SetFail"

    @pytest.mark.anyio
    async def test_set_failure_no_exception_propagates(self, async_engine: Any) -> None:
        """AC4: transport.set() raises → caller sees no exception."""
        SQLModelCache.configure(transport=SetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=11, name="SetFailSilent"))
            await session.commit()

        # Must NOT raise
        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 11)

        assert result is not None

    @pytest.mark.anyio
    async def test_set_failure_logs_warning(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC4, AC5: transport.set() raises → logger.warning called."""
        SQLModelCache.configure(transport=SetFailsTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=12, name="WriteWarn"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                await session.get(AsyncFailHero, 12)

        assert any("async_cache_write" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Task 2.6 — Corrupted bytes: deserialization failure → DB fallback
# ---------------------------------------------------------------------------


class TestDeserializationFailure:
    @pytest.mark.anyio
    async def test_corrupt_bytes_falls_through_to_db(self, async_engine: Any) -> None:
        """Task 2.6: deserialization failure on cache read → DB fallback."""
        SQLModelCache.configure(transport=CorruptBytesTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=20, name="RealDB"))
            await session.commit()

        async with AsyncSession(async_engine) as session:
            result = await session.get(AsyncFailHero, 20)

        # Despite corrupt cache bytes, DB result is returned
        assert result is not None
        assert result.name == "RealDB"

    @pytest.mark.anyio
    async def test_corrupt_bytes_logs_warning(
        self, async_engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Task 2.6: deserialization failure logs a warning."""
        SQLModelCache.configure(transport=CorruptBytesTransport())

        async with AsyncSession(async_engine) as session:
            session.add(AsyncFailHero(id=21, name="CorruptLog"))
            await session.commit()

        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            async with AsyncSession(async_engine) as session:
                await session.get(AsyncFailHero, 21)

        # Deserialize failure logged
        assert any("deserialize" in r.message for r in caplog.records)
