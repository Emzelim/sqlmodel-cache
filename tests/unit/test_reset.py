"""Unit tests for SQLModelCache.reset() — Story 4.3.

Tests verify:
  AC1 — _state cleared after reset (ConfigurationError raised)
  AC2 — All 4 event listeners removed after reset
  AC3 — reset() before configure() is a no-op (no exception)
  AC4 — Re-configure after reset works
  AC5 — SQLModelCache.reset is callable
  AC6 — Full lifecycle: configure → reset → get_config raises; configure → reset → configure → get_config succeeds

Uses in-memory SQLite and FakeTransport (no Redis, no network I/O).
Each test is isolated by the autouse ``reset_cache`` fixture in conftest.py.
"""
from __future__ import annotations

import pytest
from fakes import FakeTransport
from sqlalchemy import event
from sqlmodel import Session as SASession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache import _state as state_module
from sqlmodel_cache._errors import ConfigurationError
from sqlmodel_cache._interceptor import _cache_on_execute
from sqlmodel_cache._invalidation import (
    _after_commit_handler,
    _after_flush_handler,
    _after_rollback_handler,
)

# ---------------------------------------------------------------------------
# Story 4.3 — SQLModelCache.reset()
# ---------------------------------------------------------------------------


class TestReset:
    """AC1-AC6 from Story 4.3."""

    def test_reset_clears_state(self) -> None:
        """AC1: After configure → reset, _state.get_config() raises ConfigurationError."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        with pytest.raises(ConfigurationError):
            state_module.get_config()

    def test_reset_removes_do_orm_execute_listener(self) -> None:
        """AC2 (partial): do_orm_execute listener absent after reset."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_reset_removes_after_flush_listener(self) -> None:
        """AC2 (partial): after_flush listener absent after reset."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        assert not event.contains(SASession, "after_flush", _after_flush_handler)

    def test_reset_removes_after_commit_listener(self) -> None:
        """AC2 (partial): after_commit listener absent after reset."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        assert not event.contains(SASession, "after_commit", _after_commit_handler)

    def test_reset_removes_after_rollback_listener(self) -> None:
        """AC2 (partial): after_rollback listener absent after reset."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)

    def test_reset_removes_all_listeners(self) -> None:
        """AC2 (combined): all 4 listeners absent after reset."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "after_flush", _after_flush_handler)
        assert not event.contains(SASession, "after_commit", _after_commit_handler)
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)

    def test_reset_before_configure_is_no_op(self) -> None:
        """AC3: reset() before configure() raises no exception."""
        # The autouse fixture always resets; this test calls it explicitly
        # while already in a clean state to verify no exception.
        SQLModelCache.reset()  # should not raise

    def test_reconfigure_after_reset_works(self) -> None:
        """AC4: configure → reset → configure → get_config() returns new config."""
        transport_first = FakeTransport()
        transport_second = FakeTransport()

        SQLModelCache.configure(transport=transport_first)
        SQLModelCache.reset()
        SQLModelCache.configure(transport=transport_second)

        cfg = state_module.get_config()
        assert cfg.transport is transport_second

    def test_reset_is_callable(self) -> None:
        """AC5: SQLModelCache.reset is callable."""
        assert callable(SQLModelCache.reset)

    def test_configure_reset_get_config_raises(self) -> None:
        """AC6a: configure → reset → get_config raises ConfigurationError."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()

        with pytest.raises(ConfigurationError):
            SQLModelCache.get_config()

    def test_configure_reset_configure_get_config_succeeds(self) -> None:
        """AC6b: configure → reset → configure → get_config succeeds."""
        SQLModelCache.configure(transport=FakeTransport(), default_ttl=100)
        SQLModelCache.reset()
        SQLModelCache.configure(transport=FakeTransport(), default_ttl=200)

        cfg = SQLModelCache.get_config()
        assert cfg.default_ttl == 200

    def test_listeners_absent_after_reset_lifecycle(self) -> None:
        """AC6c: listeners absent after configure → reset."""
        SQLModelCache.configure(transport=FakeTransport())
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)

        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "after_flush", _after_flush_handler)
        assert not event.contains(SASession, "after_commit", _after_commit_handler)
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)
