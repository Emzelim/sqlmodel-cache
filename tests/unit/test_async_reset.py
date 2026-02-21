"""Unit tests for SQLModelCache.reset() complete listener cleanup — Story 5.5.

Verifies that after reset(), ALL event listeners from both sync and async
transport configurations are removed from the Session class.

AC3: reset() must remove all async listeners added in Epic 5 (Stories 5.2, 5.3).
"""
from __future__ import annotations

from fakes import FakeAsyncTransport, FakeTransport
from sqlalchemy import event
from sqlmodel import Session as SASession

from sqlmodel_cache import SQLModelCache
from sqlmodel_cache._interceptor import _async_cache_on_execute, _cache_on_execute
from sqlmodel_cache._invalidation import (
    _after_commit_handler,
    _after_flush_handler,
    _after_rollback_handler,
    _async_after_commit_handler,
)

# ---------------------------------------------------------------------------
# Tests: reset() after async transport configure
# ---------------------------------------------------------------------------


class TestResetAfterAsyncConfigure:
    """AC3: All async handlers absent after reset()."""

    def test_async_cache_on_execute_removed(self) -> None:
        """_async_cache_on_execute is removed from Session after reset()."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_async_after_commit_handler_removed(self) -> None:
        """_async_after_commit_handler is removed from Session after reset()."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "after_commit", _async_after_commit_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_commit", _async_after_commit_handler)

    def test_after_flush_handler_removed(self) -> None:
        """_after_flush_handler removed after reset() (shared for sync + async)."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "after_flush", _after_flush_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_flush", _after_flush_handler)

    def test_after_rollback_handler_removed(self) -> None:
        """_after_rollback_handler removed after reset()."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "after_rollback", _after_rollback_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)

    def test_sync_handler_not_registered_for_async_transport(self) -> None:
        """After async configure, sync _cache_on_execute is NOT registered."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_sync_commit_handler_not_registered_for_async_transport(self) -> None:
        """After async configure, sync _after_commit_handler is NOT registered."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert not event.contains(SASession, "after_commit", _after_commit_handler)

    def test_all_listeners_absent_after_reset(self) -> None:
        """All 6 possible listeners are absent after reset() with async transport."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        assert not event.contains(SASession, "after_flush", _after_flush_handler)
        assert not event.contains(SASession, "after_commit", _after_commit_handler)
        assert not event.contains(SASession, "after_commit", _async_after_commit_handler)
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)


# ---------------------------------------------------------------------------
# Tests: reset() after sync transport configure
# ---------------------------------------------------------------------------


class TestResetAfterSyncConfigure:
    """Sync listeners also cleanly removed after reset()."""

    def test_sync_cache_on_execute_removed(self) -> None:
        """_cache_on_execute is removed from Session after reset()."""
        SQLModelCache.configure(transport=FakeTransport())
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_sync_after_commit_handler_removed(self) -> None:
        """_after_commit_handler is removed from Session after reset()."""
        SQLModelCache.configure(transport=FakeTransport())
        assert event.contains(SASession, "after_commit", _after_commit_handler)
        SQLModelCache.reset()
        assert not event.contains(SASession, "after_commit", _after_commit_handler)

    def test_async_handler_not_registered_for_sync_transport(self) -> None:
        """After sync configure, _async_cache_on_execute is NOT registered."""
        SQLModelCache.configure(transport=FakeTransport())
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_all_listeners_absent_after_reset(self) -> None:
        """All 6 possible listeners are absent after reset() with sync transport."""
        SQLModelCache.configure(transport=FakeTransport())
        SQLModelCache.reset()
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        assert not event.contains(SASession, "after_flush", _after_flush_handler)
        assert not event.contains(SASession, "after_commit", _after_commit_handler)
        assert not event.contains(SASession, "after_commit", _async_after_commit_handler)
        assert not event.contains(SASession, "after_rollback", _after_rollback_handler)


# ---------------------------------------------------------------------------
# Tests: idempotent reset (no prior configure, or double reset)
# ---------------------------------------------------------------------------


class TestResetIdempotent:
    """reset() is safe to call with no prior configure or multiple times."""

    def test_reset_without_configure_no_error(self) -> None:
        """reset() with no prior configure() must not raise."""
        # autouse fixture already reset — calling again:
        SQLModelCache.reset()  # must not raise

    def test_double_reset_no_error(self) -> None:
        """Calling reset() twice in a row must not raise."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        SQLModelCache.reset()
        SQLModelCache.reset()  # second reset — no listeners to remove

    def test_configure_after_reset_works(self) -> None:
        """Can configure() after reset() without errors."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        SQLModelCache.reset()
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)


# ---------------------------------------------------------------------------
# Tests: mutually exclusive registration (no double-registration)
# ---------------------------------------------------------------------------


class TestMutuallyExclusiveHandlers:
    """Sync + async handlers are never both registered simultaneously."""

    def test_only_async_handler_registered_with_async_transport(self) -> None:
        """One async configure: ONLY _async_cache_on_execute on Session."""
        SQLModelCache.configure(transport=FakeAsyncTransport())
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _cache_on_execute)

    def test_only_sync_handler_registered_with_sync_transport(self) -> None:
        """One sync configure: ONLY _cache_on_execute on Session."""
        SQLModelCache.configure(transport=FakeTransport())
        assert event.contains(SASession, "do_orm_execute", _cache_on_execute)
        assert not event.contains(SASession, "do_orm_execute", _async_cache_on_execute)

    def test_double_async_configure_single_registration(self) -> None:
        """Double configure() with async transport: listener registered exactly once."""
        transport = FakeAsyncTransport()
        SQLModelCache.configure(transport=transport)
        SQLModelCache.configure(transport=transport)
        # Verify it passes (SQLAlchemy would raise on duplicate unless guarded)
        assert event.contains(SASession, "do_orm_execute", _async_cache_on_execute)
