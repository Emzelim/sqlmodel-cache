"""Unit tests for library-wide disable via configure(enabled=False) — Story 4.4.

Tests verify:
  AC1 — No transport.get() or transport.set() calls when enabled=False
  AC2 — SQL still issued and correct model returned
  AC3 — Invalidation (transport.delete) suppressed when enabled=False
  AC4 — enabled defaults to True
  AC5 — Normal caching path works when enabled=True (regression)

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
    """Cache-enabled hero model for enabled-flag tests."""

    __tablename__ = "hero_en"
    __cache_config__ = CacheConfig(ttl=300)

    id: int | None = Field(default=None, primary_key=True)
    name: str


# ---------------------------------------------------------------------------
# Tracking transport
# ---------------------------------------------------------------------------


class TrackingTransport(FakeTransport):
    """FakeTransport that records call counts."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int]] = []
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
# Story 4.4 — Library-Wide Disable
# ---------------------------------------------------------------------------


class TestEnabledFalse:
    """AC1-AC3 from Story 4.4: behaviour when enabled=False."""

    def test_no_transport_get_when_disabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC1 (get): transport.get() NOT called when enabled=False."""
        # Pre-populate cache so a hit would normally occur.
        hero = Hero(id=1, name="Deadpond")
        transport._store["sqlmodelcache:Hero:id=1"] = serialize(hero)
        SQLModelCache.configure(transport=transport, enabled=False)

        with SASession(engine) as s:
            s.get(Hero, 1)

        assert transport.get_calls == [], "transport.get() must NOT be called when enabled=False"

    def test_no_transport_set_on_miss_when_disabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC1 (set): transport.set() NOT called on miss when enabled=False."""
        SQLModelCache.configure(transport=transport, enabled=False)

        with SASession(engine) as s:
            hero = Hero(id=2, name="Rusty-Man")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            s.get(Hero, 2)

        assert transport.set_calls == [], "transport.set() must NOT be called when enabled=False"

    def test_sql_still_issued_when_disabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC2: SQL SELECT issued and correct Hero returned when enabled=False."""
        SQLModelCache.configure(transport=transport, enabled=False)

        with SASession(engine) as s:
            hero = Hero(id=3, name="Nova")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            def action() -> Hero | None:
                return s.get(Hero, 3)

            select_count = count_selects(engine, action)
            result = s.get(Hero, 3)

        assert select_count >= 1, "Expected SQL SELECT when enabled=False"
        assert result is not None
        assert result.name == "Nova"

    def test_invalidation_suppressed_when_disabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC3: transport.delete() NOT called on commit when enabled=False."""
        SQLModelCache.configure(transport=transport, enabled=False)

        # Insert, then update to trigger invalidation path.
        with SASession(engine) as s:
            hero = Hero(id=4, name="Original")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            h = s.get(Hero, 4)
            assert h is not None
            h.name = "Updated"
            s.commit()

        assert transport.delete_calls == [], (
            "transport.delete() must NOT be called when enabled=False"
        )


class TestEnabledTrue:
    """AC4 & AC5 from Story 4.4: default behaviour and enabled=True regression."""

    def test_enabled_defaults_to_true(self, transport: TrackingTransport) -> None:
        """AC4: configure() without enabled → _state.get_config().enabled is True."""
        SQLModelCache.configure(transport=transport)
        cfg = SQLModelCache.get_config()
        assert cfg.enabled is True

    def test_enabled_true_explicit_is_true(self, transport: TrackingTransport) -> None:
        """AC4 (explicit): configure(enabled=True) → enabled is True."""
        SQLModelCache.configure(transport=transport, enabled=True)
        assert SQLModelCache.get_config().enabled is True

    def test_enabled_false_stored(self, transport: TrackingTransport) -> None:
        """AC4 (disabled): configure(enabled=False) → enabled is False."""
        SQLModelCache.configure(transport=transport, enabled=False)
        assert SQLModelCache.get_config().enabled is False

    def test_normal_cache_path_when_enabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC5: enabled=True (default) → normal cache read/write path."""
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            hero = Hero(id=20, name="Spider-Man")
            s.add(hero)
            s.commit()

        with SASession(engine) as s:
            # First call — miss → writes to cache.
            s.get(Hero, 20)

        assert transport.set_calls, "transport.set() must be called on miss (enabled=True)"

        with SASession(engine) as s:
            # Second call — hit → no SQL.
            sql_count = count_selects(engine, lambda: s.get(Hero, 20))

        assert sql_count == 0, "Expected cache hit (no SQL) on second call"

    def test_invalidation_fires_when_enabled(
        self, transport: TrackingTransport, engine: Any
    ) -> None:
        """AC5 (invalidation): transport.delete() IS called on commit when enabled=True."""
        SQLModelCache.configure(transport=transport)

        with SASession(engine) as s:
            hero = Hero(id=21, name="Original")
            s.add(hero)
            s.commit()

        # Populate cache.
        with SASession(engine) as s:
            s.get(Hero, 21)

        # Update and commit — should trigger invalidation.
        with SASession(engine) as s:
            h = s.get(Hero, 21)
            assert h is not None
            h.name = "Updated"
            s.commit()

        assert transport.delete_calls, (
            "transport.delete() must be called after commit when enabled=True"
        )
