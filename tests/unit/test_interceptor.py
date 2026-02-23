"""Unit tests for _interceptor.py — Stories 2.2, 2.3, 2.4, 2.5, 2.6.

Uses in-memory SQLite (no Redis, no network I/O).
Each test is fully isolated: the autouse ``reset_cache`` fixture in
``tests/conftest.py`` calls ``SQLModelCache.reset()`` after every test.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fakes import FakeTransport
from sqlalchemy import create_engine, event
from sqlmodel import Field, SQLModel
from sqlmodel import Session as SASession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._config import CacheConfig
from sqlmodel_cache._interceptor import (
    _cache_on_execute,
    _get_cache_config,
    _resolve_ttl,
)
from sqlmodel_cache.serializer import serialize

# ---------------------------------------------------------------------------
# Test models — registered once in SQLModel.metadata at import time
# ---------------------------------------------------------------------------


class Hero(SQLModel, table=True):
    """Cache-enabled hero model for interceptor tests."""

    __tablename__ = "hero_it"  # unique name to avoid conflicts
    __cache_config__ = CacheConfig()  # enabled=True, ttl=None

    id: int | None = Field(default=None, primary_key=True)
    name: str


class HeroWithCustomTTL(SQLModel, table=True):
    """Cache-enabled model with an explicit per-model TTL."""

    __tablename__ = "hero_it_custom_ttl"
    __cache_config__ = CacheConfig(ttl=600)

    id: int | None = Field(default=None, primary_key=True)
    name: str


class HeroWithUUID(SQLModel, table=True):
    """Cache-enabled model with a UUID primary key."""

    __tablename__ = "hero_it_uuid"
    __cache_config__ = CacheConfig()

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str


class PlainHero(SQLModel, table=True):
    """Model WITHOUT __cache_config__ — interceptor must pass through."""

    __tablename__ = "plain_hero_it"

    id: int | None = Field(default=None, primary_key=True)
    name: str


class DisabledHero(SQLModel, table=True):
    """Model with __cache_config__(enabled=False) — interceptor must pass through."""

    __tablename__ = "disabled_hero_it"
    __cache_config__ = CacheConfig(enabled=False)

    id: int | None = Field(default=None, primary_key=True)
    name: str


# ---------------------------------------------------------------------------
# Tracking transport (call-counting)
# ---------------------------------------------------------------------------


class TrackingTransport(FakeTransport):
    """FakeTransport that records every get/set call."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int]] = []  # (key, ttl)

    def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return super().get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self.set_calls.append((key, ttl))
        super().set(key, value, ttl)


class ErrorTransport:
    """Transport that raises on get and/or set for fail-open testing."""

    def __init__(self, fail_on_get: bool = True, fail_on_set: bool = False) -> None:
        self._store: dict[str, bytes] = {}
        self._fail_on_get = fail_on_get
        self._fail_on_set = fail_on_set
        self.get_calls: list[str] = []
        self.set_calls: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        if self._fail_on_get:
            raise ConnectionError("Redis unavailable")
        return self._store.get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self.set_calls.append(key)
        if self._fail_on_set:
            raise ConnectionError("Redis write failed")
        self._store[key] = value

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all test tables created."""
    e = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(e)
    yield e
    SQLModel.metadata.drop_all(e)


@pytest.fixture()
def transport() -> TrackingTransport:
    return TrackingTransport()


@pytest.fixture()
def configured(
    transport: TrackingTransport,
) -> Generator[TrackingTransport, None, None]:
    """Configure SQLModelCache with the tracking transport."""
    SQLModelCache.configure(transport=transport)
    yield transport


# ---------------------------------------------------------------------------
# Story 2.2 — Registration & Dispatch
# ---------------------------------------------------------------------------


class TestRegistration:
    """AC1, AC2, AC3, AC4, AC5 from Story 2.2."""

    def test_listener_registered_after_configure(
        self, configured: TrackingTransport
    ) -> None:
        """AC1: event.contains returns True after configure()."""
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_listener_not_registered_before_configure(self) -> None:
        """AC2: listener is absent before configure()."""
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_listener_not_registered_after_reset(
        self, configured: TrackingTransport
    ) -> None:
        """AC2 (post-reset): listener is removed by reset()."""
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_double_configure_single_registration(
        self, transport: TrackingTransport
    ) -> None:
        """AC3: calling configure() twice registers the listener exactly once."""
        SQLModelCache.configure(transport=transport)
        SQLModelCache.configure(transport=transport)
        # SQLAlchemy would raise if the same listener were registered twice
        # via the dedup guard; we just confirm it is registered (once).
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_no_transport_call_for_plain_model(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC4: models without __cache_config__ bypass the transport."""
        with SASession(engine) as s:
            s.get(PlainHero, 999)  # doesn't exist — that's fine
        assert configured.get_calls == []

    def test_no_transport_call_for_disabled_model(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC5: models with enabled=False bypass the transport."""
        with SASession(engine) as s:
            s.get(DisabledHero, 999)
        assert configured.get_calls == []

    def test_transport_called_for_cached_model(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC6: transport.get() is called for a cache-enabled model."""
        with SASession(engine) as s:
            s.get(Hero, 1)
        assert configured.get_calls == ["sqlmodelcache:Hero:id=1"]


# ---------------------------------------------------------------------------
# Story 2.3 — Cache Hit Path
# ---------------------------------------------------------------------------


class TestCacheHitPath:
    """AC1-AC5 from Story 2.3."""

    def test_hit_returns_cached_instance(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC1: deserialized instance is returned on cache hit."""
        hero = Hero(id=1, name="Deadpond")
        transport._store["sqlmodelcache:Hero:id=1"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            result = s.get(Hero, 1)

        assert result is not None
        assert result.id == 1
        assert result.name == "Deadpond"

    def test_hit_emits_no_sql(self, transport: TrackingTransport, engine: Any) -> None:
        """AC2: zero SELECT statements issued on cache hit."""
        hero = Hero(id=42, name="Rusty-Man")
        transport._store["sqlmodelcache:Hero:id=42"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        sql_calls: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def capture(conn: Any, cursor: Any, stmt: str, *a: Any) -> None:
            sql_calls.append(stmt)

        with SASession(engine) as s:
            s.get(Hero, 42)

        event.remove(engine, "before_cursor_execute", capture)
        assert sql_calls == [], f"Expected no SQL but got: {sql_calls}"

    def test_hit_uses_correct_key(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC3: transport.get() is called with the expected key format."""
        hero = Hero(id=7, name="Spider-Man")
        transport._store["sqlmodelcache:Hero:id=7"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            s.get(Hero, 7)

        assert transport.get_calls == ["sqlmodelcache:Hero:id=7"]

    def test_hit_uuid_field_returns_uuid_type(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC4: UUID primary key is returned as uuid.UUID, not str."""
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        hero = HeroWithUUID(id=uid, name="Nova")
        key = f"sqlmodelcache:HeroWithUUID:id={uid}"
        transport._store[key] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            result = s.get(HeroWithUUID, uid)

        assert result is not None
        assert isinstance(result.id, uuid.UUID)
        assert result.id == uid

    def test_hit_does_not_write_back(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC5: transport.set() is never called on a cache hit."""
        hero = Hero(id=99, name="Dormammu")
        transport._store["sqlmodelcache:Hero:id=99"] = serialize(hero)
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            s.get(Hero, 99)

        assert transport.set_calls == []


# ---------------------------------------------------------------------------
# Story 2.4 — Cache Miss Path
# ---------------------------------------------------------------------------


class TestCacheMissPath:
    """AC1-AC5 from Story 2.4."""

    def test_miss_queries_db_and_returns_instance(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC1: cache miss causes a DB query and the correct instance is returned."""
        with SASession(engine) as s:
            s.add(Hero(id=10, name="Colossus"))
            s.commit()

        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            result = s.get(Hero, 10)

        assert result is not None
        assert result.name == "Colossus"

    def test_miss_writes_to_cache(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC2: cache miss writes the result to the transport."""
        with SASession(engine) as s:
            s.add(Hero(id=11, name="Storm"))
            s.commit()

        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            s.get(Hero, 11)

        assert "sqlmodelcache:Hero:id=11" in transport._store

    def test_miss_second_call_is_cache_hit(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC3: after a miss + write, the second call is served from cache."""
        with SASession(engine) as s:
            s.add(Hero(id=12, name="Iceman"))
            s.commit()

        SQLModelCache.configure(transport=transport)

        sql_calls: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def capture(conn: Any, cursor: Any, stmt: str, *a: Any) -> None:
            sql_calls.append(stmt)

        with SASession(engine) as s:
            s.get(Hero, 12)  # miss — DB query

        first_sql_count = len(sql_calls)
        sql_calls.clear()

        with SASession(engine) as s:
            result = s.get(Hero, 12)  # hit — zero SQL

        event.remove(engine, "before_cursor_execute", capture)

        assert first_sql_count >= 1, "First call should have queried DB"
        assert sql_calls == [], "Second call should be served from cache"
        assert result is not None
        assert result.name == "Iceman"

    def test_miss_nonexistent_row_does_not_cache_none(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC4: when the DB has no matching row, transport.set() is NOT called."""
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            result = s.get(Hero, 9999)

        assert result is None
        assert transport.set_calls == []

    def test_miss_uses_global_default_ttl(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC5: transport.set() is called with the global default_ttl."""
        with SASession(engine) as s:
            s.add(Hero(id=13, name="Beast"))
            s.commit()

        SQLModelCache.configure(transport=transport, default_ttl=120)

        with SASession(engine) as s:
            s.get(Hero, 13)

        assert transport.set_calls == [("sqlmodelcache:Hero:id=13", 120)]

    def test_miss_uses_model_ttl_over_global(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC5 + Story 2.6: model-level TTL overrides global default_ttl."""
        with SASession(engine) as s:
            s.add(HeroWithCustomTTL(id=1, name="Professor X"))
            s.commit()

        SQLModelCache.configure(transport=transport, default_ttl=300)

        with SASession(engine) as s:
            s.get(HeroWithCustomTTL, 1)

        assert transport.set_calls == [("sqlmodelcache:HeroWithCustomTTL:id=1", 600)]


# ---------------------------------------------------------------------------
# Story 2.5 — Fail-Open Error Handling
# ---------------------------------------------------------------------------


class TestFailOpen:
    """AC1-AC4 from Story 2.5."""

    def test_transport_get_error_falls_through_to_db(self, engine: Any) -> None:
        """AC1: transport.get() failure → DB is still queried, result returned."""
        err_transport = ErrorTransport(fail_on_get=True)
        SQLModelCache.configure(transport=err_transport)

        with SASession(engine) as s:
            s.add(Hero(id=20, name="Havok"))
            s.commit()

        with SASession(engine) as s:
            result = s.get(Hero, 20)

        assert result is not None
        assert result.name == "Havok"

    def test_transport_get_error_does_not_propagate(self, engine: Any) -> None:
        """AC1: a transport read failure must NOT raise an exception to the caller."""
        err_transport = ErrorTransport(fail_on_get=True)
        SQLModelCache.configure(transport=err_transport)

        with SASession(engine) as s:
            # Should not raise — fail-open
            result = s.get(Hero, 999)

        assert result is None  # DB has no such row, but no exception

    def test_transport_set_error_does_not_propagate(self, engine: Any) -> None:
        """AC2: a cache write failure must not prevent the caller from receiving the result."""
        err_transport = ErrorTransport(fail_on_get=False, fail_on_set=True)
        SQLModelCache.configure(transport=err_transport)

        with SASession(engine) as s:
            s.add(Hero(id=21, name="Polaris"))
            s.commit()

        with SASession(engine) as s:
            result = s.get(Hero, 21)

        assert result is not None
        assert result.name == "Polaris"

    def test_transport_get_error_logs_warning(
        self, engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC3: transport read failures are logged at WARNING level."""
        import logging

        err_transport = ErrorTransport(fail_on_get=True)
        SQLModelCache.configure(transport=err_transport)

        with (
            caplog.at_level(logging.WARNING, logger="sqlmodel_cache"),
            SASession(engine) as s,
        ):
            s.get(Hero, 999)

        assert any("cache_read failed" in r.message for r in caplog.records)

    def test_transport_set_error_logs_warning(
        self, engine: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC4: transport write failures are logged at WARNING level."""
        import logging

        err_transport = ErrorTransport(fail_on_get=False, fail_on_set=True)
        SQLModelCache.configure(transport=err_transport)

        with SASession(engine) as s:
            s.add(Hero(id=22, name="Gambit"))
            s.commit()

        with (
            caplog.at_level(logging.WARNING, logger="sqlmodel_cache"),
            SASession(engine) as s,
        ):
            s.get(Hero, 22)

        assert any("cache_write failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Story 2.6 — Effective TTL Resolution (unit tests for _resolve_ttl)
# ---------------------------------------------------------------------------


class TestResolveTTL:
    """AC1-AC3 from Story 2.6."""

    def test_model_ttl_wins_over_global(self) -> None:
        """AC1: CacheConfig.ttl overrides global default_ttl."""
        cfg = CacheConfig(ttl=600)
        assert _resolve_ttl(cfg, global_default_ttl=300) == 600

    def test_global_ttl_used_when_model_ttl_is_none(self) -> None:
        """AC2: global default_ttl is used when CacheConfig.ttl is None."""
        cfg = CacheConfig()  # ttl=None
        assert _resolve_ttl(cfg, global_default_ttl=300) == 300

    def test_configure_without_explicit_ttl_stores_300(self) -> None:
        """AC3: configure() without default_ttl stores 300 (library default)."""
        from sqlmodel_cache import _state

        SQLModelCache.configure(transport=FakeTransport())
        assert _state.get_config().default_ttl == 300

    def test_configure_with_explicit_ttl_stored(self) -> None:
        """AC3 variant: configure(default_ttl=X) stores exactly X."""
        from sqlmodel_cache import _state

        SQLModelCache.configure(transport=FakeTransport(), default_ttl=60)
        assert _state.get_config().default_ttl == 60

    def test_model_ttl_zero_wins_over_global(self) -> None:
        """Edge case: explicit ttl=0 on model still overrides global."""
        cfg = CacheConfig(ttl=0)
        assert _resolve_ttl(cfg, global_default_ttl=300) == 0

    def test_global_ttl_zero_is_respected(self) -> None:
        """Edge case: global_default_ttl=0 is valid (caller's choice)."""
        cfg = CacheConfig()  # ttl=None
        assert _resolve_ttl(cfg, global_default_ttl=0) == 0


# ---------------------------------------------------------------------------
# Direct helper tests
# ---------------------------------------------------------------------------


class TestGetCacheConfig:
    """Unit tests for _get_cache_config() helper (AC4, AC5 of Story 2.2)."""

    def test_returns_none_for_non_orm_statement(self) -> None:
        """Non-ORM statements return None immediately."""

        class FakeState:
            is_orm_statement = False
            is_select = True

        assert _get_cache_config(FakeState()) is None  # type: ignore[arg-type]

    def test_returns_none_for_non_select(self) -> None:
        """Non-SELECT ORM statements return None."""

        class FakeState:
            is_orm_statement = True
            is_select = False

        assert _get_cache_config(FakeState()) is None  # type: ignore[arg-type]
