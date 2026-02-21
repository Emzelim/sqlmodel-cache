"""Unit tests for execution_options bypass and TTL override — Stories 4.1 & 4.2.

Tests cover:
  Story 4.1 — cache=False bypass (FR14)
  Story 4.2 — cache_ttl=N per-call TTL override (FR15)

Uses in-memory SQLite and FakeTransport (no Redis, no network I/O).
Each test is isolated by the autouse ``reset_cache`` fixture in conftest.py.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fakes import FakeTransport
from sqlalchemy import create_engine, event
from sqlmodel import Field, SQLModel
from sqlmodel import Session as SASession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._config import CacheConfig
from sqlmodel_cache.serializer import serialize

# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class Hero(SQLModel, table=True):
    """Cache-enabled hero model for execution_options tests."""

    __tablename__ = "hero_eo"
    __cache_config__ = CacheConfig(ttl=600)

    id: int | None = Field(default=None, primary_key=True)
    name: str


# ---------------------------------------------------------------------------
# Tracking transport
# ---------------------------------------------------------------------------


class TrackingTransport(FakeTransport):
    """FakeTransport that records every get/set/delete call."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int]] = []  # (key, ttl)
        self.delete_calls: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        return super().get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self.set_calls.append((key, ttl))
        super().set(key, value, ttl)

    def delete(self, *keys: str) -> None:
        self.delete_calls.extend(keys)
        super().delete(*keys)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> Generator[Any, None, None]:
    e = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(e)
    yield e
    SQLModel.metadata.drop_all(e)


@pytest.fixture()
def transport() -> TrackingTransport:
    return TrackingTransport()


@pytest.fixture()
def configured(transport: TrackingTransport) -> Generator[TrackingTransport, None, None]:
    SQLModelCache.configure(transport=transport)
    yield transport


def count_selects(engine: Any, action: Any) -> int:
    """Run *action* and return the number of SELECT statements issued."""
    sql_calls: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn: Any, cursor: Any, stmt: str, *a: Any) -> None:
        if stmt.strip().upper().startswith("SELECT"):
            sql_calls.append(stmt)

    action()
    event.remove(engine, "before_cursor_execute", capture)
    return len(sql_calls)


# ---------------------------------------------------------------------------
# Story 4.1 — Per-Call Cache Bypass via execution_options(cache=False)
# ---------------------------------------------------------------------------


class TestCacheBypass:
    """AC1-AC4 from Story 4.1."""

    def test_bypass_skips_cache_read(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC1: cache=False → transport.get NOT called."""
        # Pre-populate cache so a hit would normally occur.
        hero = Hero(id=1, name="Deadpond")
        configured._store["sqlmodelcache:Hero:id=1"] = serialize(hero)

        with SASession(engine) as s:
            s.get(Hero, 1, execution_options={"cache": False})

        assert configured.get_calls == [], "transport.get() must NOT be called when cache=False"

    def test_bypass_skips_cache_write_on_miss(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC2: cache=False on miss → transport.set NOT called."""
        # Insert a real hero so the DB returns something (not None).
        with SASession(engine) as s:
            hero = Hero(id=2, name="Rusty-Man")
            s.add(hero)
            s.commit()

        transport_fresh = configured
        transport_fresh.set_calls.clear()

        with SASession(engine) as s:
            result = s.get(Hero, 2, execution_options={"cache": False})

        assert result is not None
        assert transport_fresh.set_calls == [], "transport.set() must NOT be called on bypass miss"

    def test_bypass_issues_sql(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC1 (SQL issued): bypass results in a DB query, not a cache serve."""
        # Pre-populate cache with stale name.
        hero = Hero(id=3, name="CachedName")
        configured._store["sqlmodelcache:Hero:id=3"] = serialize(hero)

        # Insert the real row with a different name.
        with SASession(engine) as s:
            real_hero = Hero(id=3, name="RealName")
            s.add(real_hero)
            s.commit()

        with SASession(engine) as s:
            select_count = count_selects(
                engine, lambda: s.get(Hero, 3, execution_options={"cache": False})
            )

        assert select_count >= 1, "Expected at least one SELECT when cache=False"

    def test_bypass_returns_db_value_not_cached(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC6: DB value returned even when Redis has a different cached value."""
        # Insert real row.
        with SASession(engine) as s:
            real_hero = Hero(id=4, name="RealName")
            s.add(real_hero)
            s.commit()

        # Populate cache with different name.
        stale = Hero(id=4, name="CachedStaleName")
        configured._store["sqlmodelcache:Hero:id=4"] = serialize(stale)

        with SASession(engine) as s:
            result = s.get(Hero, 4, execution_options={"cache": False})

        assert result is not None
        assert result.name == "RealName", "DB value must be returned, not cached stale value"

    def test_no_bypass_uses_cache_by_default(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC4: omitting execution_options → normal cache read path."""
        hero = Hero(id=5, name="Dormammu")
        configured._store["sqlmodelcache:Hero:id=5"] = serialize(hero)

        with SASession(engine) as s:
            result = s.get(Hero, 5)

        assert "sqlmodelcache:Hero:id=5" in configured.get_calls, (
            "transport.get() must be called on the normal path (no bypass)"
        )
        assert result is not None
        assert result.name == "Dormammu"

    def test_cache_true_explicit_uses_cache(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC4 (explicit True): cache=True behaves identically to default."""
        hero = Hero(id=6, name="Spider-Man")
        configured._store["sqlmodelcache:Hero:id=6"] = serialize(hero)

        with SASession(engine) as s:
            result = s.get(Hero, 6, execution_options={"cache": True})

        assert "sqlmodelcache:Hero:id=6" in configured.get_calls
        assert result is not None
        assert result.name == "Spider-Man"


# ---------------------------------------------------------------------------
# Story 4.2 — Per-Call TTL Override via execution_options(cache_ttl=N)
# ---------------------------------------------------------------------------


class TestPerCallTTL:
    """AC1-AC5 from Story 4.2."""

    def test_per_call_ttl_used_on_miss(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC1: cache_ttl=30 overrides model CacheConfig(ttl=600) on miss."""
        with SASession(engine) as s:
            hero = Hero(id=10, name="Nova")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            s.get(Hero, 10, execution_options={"cache_ttl": 30})

        assert configured.set_calls, "transport.set() must be called on miss"
        _key, ttl = configured.set_calls[0]
        assert ttl == 30, f"Expected ttl=30 but got ttl={ttl}"

    def test_per_call_ttl_no_effect_on_hit(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC2: cache_ttl=30 on a cache hit → transport.set NOT called."""
        hero = Hero(id=11, name="Thor")
        configured._store["sqlmodelcache:Hero:id=11"] = serialize(hero)

        with SASession(engine) as s:
            s.get(Hero, 11, execution_options={"cache_ttl": 30})

        assert configured.set_calls == [], "transport.set() must NOT be called on cache hit"

    def test_per_call_ttl_with_cache_true_explicit(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC3: cache=True + cache_ttl=45 → transport.set called with ttl=45."""
        with SASession(engine) as s:
            hero = Hero(id=12, name="Captain Marvel")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            s.get(Hero, 12, execution_options={"cache": True, "cache_ttl": 45})

        assert configured.set_calls, "transport.set() must be called"
        _, ttl = configured.set_calls[0]
        assert ttl == 45, f"Expected ttl=45 but got ttl={ttl}"

    def test_bypass_wins_over_per_call_ttl(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """AC4: cache=False + cache_ttl=45 → neither get nor set called."""
        with SASession(engine) as s:
            hero = Hero(id=13, name="Panther")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            s.get(Hero, 13, execution_options={"cache": False, "cache_ttl": 45})

        assert configured.get_calls == [], "transport.get() must NOT be called when cache=False"
        assert configured.set_calls == [], "transport.set() must NOT be called when cache=False"

    def test_default_ttl_used_when_no_per_call_ttl(
        self, configured: TrackingTransport, engine: Any
    ) -> None:
        """Regression: omitting cache_ttl → model TTL (600) is used."""
        with SASession(engine) as s:
            hero = Hero(id=14, name="Wasp")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            s.get(Hero, 14)

        assert configured.set_calls, "transport.set() must be called on miss"
        _, ttl = configured.set_calls[0]
        assert ttl == 600, f"Expected model TTL=600 but got ttl={ttl}"
