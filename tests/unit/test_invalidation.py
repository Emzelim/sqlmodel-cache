"""Unit tests for the cache invalidation event handlers (Epic 3, Stories 3.1-3.5).

All tests are pure Python — no real DB, no real Redis.
A ``FakeSession`` stub replaces the SQLAlchemy session for direct handler
invocation.  The ``configured_cache`` fixture wires ``FakeTransport`` so that
event-registration tests can check ``event.contains()``.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes import FakeTransport
from sqlalchemy import event
from sqlmodel import Field, Session, SQLModel

from sqlmodel_cache._config import CacheConfig, SQLModelCache
from sqlmodel_cache._invalidation import (  # type: ignore[reportPrivateUsage]
    _PENDING_KEY,  # type: ignore[reportPrivateUsage]
    _after_commit_handler,
    _after_flush_handler,
    _after_rollback_handler,
)
from sqlmodel_cache.keys import extract_pk_from_instance

# ---------------------------------------------------------------------------
# Test models — unique __tablename__ per model to avoid SQLModel metadata clash
# ---------------------------------------------------------------------------


class HeroInv(SQLModel, table=True):
    __tablename__ = "hero_inv"  # type: ignore[assignment]
    __cache_config__ = CacheConfig()

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class PlainHeroInv(SQLModel, table=True):
    __tablename__ = "plain_hero_inv"  # type: ignore[assignment]
    # No __cache_config__ — should be ignored by invalidation collector

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class DisabledHeroInv(SQLModel, table=True):
    __tablename__ = "disabled_hero_inv"  # type: ignore[assignment]
    __cache_config__ = CacheConfig(enabled=False)

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""


class HeroTeamInv(SQLModel, table=True):
    """Composite PK model for Story 3.5 tests."""

    __tablename__ = "hero_team_inv"  # type: ignore[assignment]
    __cache_config__ = CacheConfig()

    hero_id: int = Field(primary_key=True)
    team_id: int = Field(primary_key=True)


# ---------------------------------------------------------------------------
# Stubs & helpers
# ---------------------------------------------------------------------------


class FakeSession:
    """Minimal session stub for direct handler invocation."""

    def __init__(
        self,
        *,
        dirty: tuple[Any, ...] = (),
        deleted: tuple[Any, ...] = (),
        new: tuple[Any, ...] = (),
    ) -> None:
        self.info: dict[str, Any] = {}
        self.dirty = dirty
        self.deleted = deleted
        self.new = new


class TrackingTransport(FakeTransport):
    """FakeTransport that also records every key passed to delete()."""

    def __init__(self) -> None:
        super().__init__()
        self.delete_calls: list[str] = []

    def delete(self, *keys: str) -> None:
        self.delete_calls.extend(keys)
        super().delete(*keys)


class ErrorOnDeleteTransport(FakeTransport):
    """Transport that raises whenever delete() is called."""

    def __init__(self, error_msg: str = "pipeline error") -> None:
        super().__init__()
        self.error_msg = error_msg
        self.delete_attempts: list[str] = []

    def delete(self, *keys: str) -> None:
        self.delete_attempts.extend(keys)
        raise Exception(self.error_msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture()
def tracking_transport() -> TrackingTransport:
    return TrackingTransport()


@pytest.fixture()
def configured_cache(fake_transport: FakeTransport) -> FakeTransport:
    SQLModelCache.configure(fake_transport)
    yield fake_transport  # type: ignore[misc]
    SQLModelCache.reset()


@pytest.fixture()
def configured_tracking(tracking_transport: TrackingTransport) -> TrackingTransport:
    SQLModelCache.configure(tracking_transport)
    yield tracking_transport  # type: ignore[misc]
    SQLModelCache.reset()


# ---------------------------------------------------------------------------
# Story 3.1: after_flush handler — registration & collection
# ---------------------------------------------------------------------------


class TestAfterFlushRegistration:
    """AC1 — handler registered after configure(); removed after reset()."""

    def test_registered_after_configure(self, configured_cache: FakeTransport) -> None:
        assert event.contains(Session, "after_flush", _after_flush_handler)

    def test_not_registered_before_configure(self) -> None:
        assert not event.contains(Session, "after_flush", _after_flush_handler)

    def test_not_registered_after_reset(self, fake_transport: FakeTransport) -> None:
        SQLModelCache.configure(fake_transport)
        SQLModelCache.reset()
        assert not event.contains(Session, "after_flush", _after_flush_handler)

    def test_double_registration_guard(self, fake_transport: FakeTransport) -> None:
        SQLModelCache.configure(fake_transport)
        SQLModelCache.configure(fake_transport)
        # Only one listener — removing it once must succeed and leave it gone
        SQLModelCache.reset()
        assert not event.contains(Session, "after_flush", _after_flush_handler)
        SQLModelCache.reset()  # second reset must be a no-op


class TestAfterFlushCollection:
    """AC2-AC6 — handler collects the right instances."""

    def test_dirty_instance_collected(self) -> None:
        """AC2: dirty instance with __cache_config__ appears in pending list."""
        hero = HeroInv(id=1, name="Deadpond")
        session = FakeSession(dirty=(hero,))
        _after_flush_handler(session, None)
        pending = session.info[_PENDING_KEY]
        assert (HeroInv, {"id": 1}) in pending

    def test_deleted_instance_collected(self) -> None:
        """AC3: deleted instance with __cache_config__ appears in pending list."""
        hero = HeroInv(id=2, name="Rusty")
        session = FakeSession(deleted=(hero,))
        _after_flush_handler(session, None)
        pending = session.info[_PENDING_KEY]
        assert (HeroInv, {"id": 2}) in pending

    def test_new_instance_not_collected(self) -> None:
        """AC4: inserted (new) instances are NOT collected."""
        hero = HeroInv(id=3, name="Spider-Boy")
        session = FakeSession(new=(hero,))
        _after_flush_handler(session, None)
        pending = session.info.get(_PENDING_KEY, [])
        assert (HeroInv, {"id": 3}) not in pending

    def test_plain_model_not_collected(self) -> None:
        """AC5: model without __cache_config__ is ignored."""
        plain = PlainHeroInv(id=10, name="Nobody")
        session = FakeSession(dirty=(plain,))
        _after_flush_handler(session, None)
        pending = session.info.get(_PENDING_KEY, [])
        assert len(pending) == 0

    def test_disabled_model_not_collected(self) -> None:
        """AC5 variant: model with enabled=False is ignored."""
        disabled = DisabledHeroInv(id=5, name="Inactive")
        session = FakeSession(dirty=(disabled,))
        _after_flush_handler(session, None)
        pending = session.info.get(_PENDING_KEY, [])
        assert len(pending) == 0

    def test_pending_key_initialised_if_absent(self) -> None:
        """AC6: session.info starts empty; key created without KeyError."""
        session = FakeSession()
        assert _PENDING_KEY not in session.info
        _after_flush_handler(session, None)
        assert _PENDING_KEY in session.info

    def test_both_dirty_and_deleted_collected(self) -> None:
        hero1 = HeroInv(id=1, name="A")
        hero2 = HeroInv(id=2, name="B")
        session = FakeSession(dirty=(hero1,), deleted=(hero2,))
        _after_flush_handler(session, None)
        pending = session.info[_PENDING_KEY]
        assert (HeroInv, {"id": 1}) in pending
        assert (HeroInv, {"id": 2}) in pending


# ---------------------------------------------------------------------------
# Story 3.2: after_commit handler — registration & key deletion
# ---------------------------------------------------------------------------


class TestAfterCommitRegistration:
    """AC1 — handler registered after configure(); removed after reset()."""

    def test_registered_after_configure(self, configured_cache: FakeTransport) -> None:
        assert event.contains(Session, "after_commit", _after_commit_handler)

    def test_not_registered_before_configure(self) -> None:
        assert not event.contains(Session, "after_commit", _after_commit_handler)

    def test_not_registered_after_reset(self, fake_transport: FakeTransport) -> None:
        SQLModelCache.configure(fake_transport)
        SQLModelCache.reset()
        assert not event.contains(Session, "after_commit", _after_commit_handler)


class TestAfterCommitExecution:
    """AC2-AC5 — handler deletes collected keys."""

    def test_deletes_each_collected_key(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC2: each (model_cls, pk_dict) in pending gets deleted."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [
            (HeroInv, {"id": 1}),
            (HeroInv, {"id": 2}),
        ]
        _after_commit_handler(session)
        assert "sqlmodelcache:HeroInv:id=1" in configured_tracking.delete_calls
        assert "sqlmodelcache:HeroInv:id=2" in configured_tracking.delete_calls

    def test_pending_list_cleared_after_commit(
        self, configured_cache: FakeTransport
    ) -> None:
        """AC3: pending list is empty (popped) after commit handler runs."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_commit_handler(session)
        assert session.info.get(_PENDING_KEY, []) == []

    def test_empty_pending_list_is_noop(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC4: empty pending list → delete never called."""
        session = FakeSession()
        session.info[_PENDING_KEY] = []
        _after_commit_handler(session)
        assert len(configured_tracking.delete_calls) == 0

    def test_missing_pending_key_is_noop(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC4 variant: key absent entirely → no-op."""
        session = FakeSession()
        _after_commit_handler(session)
        assert len(configured_tracking.delete_calls) == 0

    def test_key_removed_from_transport_store(
        self, configured_cache: FakeTransport
    ) -> None:
        """AC5 integration-style: set a key, commit-handler deletes it."""
        configured_cache.set("sqlmodelcache:HeroInv:id=1", b"data", ttl=300)
        assert "sqlmodelcache:HeroInv:id=1" in configured_cache

        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_commit_handler(session)
        assert "sqlmodelcache:HeroInv:id=1" not in configured_cache


# ---------------------------------------------------------------------------
# Story 3.3: after_rollback handler — registration & clearing
# ---------------------------------------------------------------------------


class TestAfterRollbackRegistration:
    """AC4 — handler registered after configure(); removed after reset()."""

    def test_registered_after_configure(self, configured_cache: FakeTransport) -> None:
        assert event.contains(Session, "after_rollback", _after_rollback_handler)

    def test_not_registered_before_configure(self) -> None:
        assert not event.contains(Session, "after_rollback", _after_rollback_handler)

    def test_not_registered_after_reset(self, fake_transport: FakeTransport) -> None:
        SQLModelCache.configure(fake_transport)
        SQLModelCache.reset()
        assert not event.contains(Session, "after_rollback", _after_rollback_handler)


class TestAfterRollbackExecution:
    """AC1-AC3, AC5 — rollback clears pending without touching transport."""

    def test_no_delete_on_rollback(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC1: transport.delete() NOT called on rollback."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_rollback_handler(session)
        assert len(configured_tracking.delete_calls) == 0

    def test_pending_list_cleared_by_rollback(self) -> None:
        """AC2: pending list is reset to [] after rollback."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_rollback_handler(session)
        assert session.info[_PENDING_KEY] == []

    def test_rollback_on_empty_pending_no_error(self) -> None:
        """AC5: rollback with no pending keys is a no-op."""
        session = FakeSession()
        _after_rollback_handler(session)  # must not raise

    def test_no_bleed_into_next_transaction(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC3: rollback clears pending; subsequent commit only deletes new keys."""
        session = FakeSession()
        # First "transaction" — collect then rollback
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 99})]
        _after_rollback_handler(session)
        assert session.info[_PENDING_KEY] == []

        # Second "transaction" — collect and commit
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_commit_handler(session)

        assert "sqlmodelcache:HeroInv:id=1" in configured_tracking.delete_calls
        assert "sqlmodelcache:HeroInv:id=99" not in configured_tracking.delete_calls


# ---------------------------------------------------------------------------
# Story 3.4: Invalidation failure suppression
# ---------------------------------------------------------------------------


class TestInvalidationFailureSuppression:
    """AC1-AC6 — transport errors logged and suppressed."""

    @pytest.fixture()
    def configured_error(self) -> ErrorOnDeleteTransport:
        transport = ErrorOnDeleteTransport()
        SQLModelCache.configure(transport)
        yield transport  # type: ignore[misc]
        SQLModelCache.reset()

    def test_exception_suppressed(
        self, configured_error: ErrorOnDeleteTransport
    ) -> None:
        """AC1: no exception propagates from the handler."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_commit_handler(session)  # must not raise

    def test_warning_logged(
        self, configured_error: ErrorOnDeleteTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC2: logger.warning called exactly once per failed key."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            _after_commit_handler(session)
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING

    def test_log_message_safe(
        self, configured_error: ErrorOnDeleteTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC3: log message contains key but NOT sensitive field values."""
        hero = HeroInv(id=1, name="Deadpond")
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, extract_pk_from_instance(hero))]
        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            _after_commit_handler(session)
        msg = caplog.records[0].message
        assert "sqlmodelcache:HeroInv:id=1" in msg
        assert "invalidation_failed" in msg
        assert "Deadpond" not in msg  # NFR-SEC-1

    def test_remaining_keys_attempted_after_failure(
        self, configured_error: ErrorOnDeleteTransport
    ) -> None:
        """AC4: failure on first key does not abort loop — second key attempted."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [
            (HeroInv, {"id": 1}),
            (HeroInv, {"id": 2}),
        ]
        _after_commit_handler(session)
        assert len(configured_error.delete_attempts) == 2

    def test_warning_per_failed_key(
        self,
        configured_error: ErrorOnDeleteTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AC2 extended: one warning per failed delete, not one total."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [
            (HeroInv, {"id": 1}),
            (HeroInv, {"id": 2}),
        ]
        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            _after_commit_handler(session)
        assert len(caplog.records) == 2

    def test_no_warning_on_success(
        self, configured_cache: FakeTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC6 complement: no warning logged on successful delete."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        with caplog.at_level(logging.WARNING, logger="sqlmodel_cache"):
            _after_commit_handler(session)
        assert len(caplog.records) == 0


# ---------------------------------------------------------------------------
# Story 3.5: Per-instance key scoping
# ---------------------------------------------------------------------------


class TestPerInstanceKeyScoping:
    """AC1-AC4 — only the specific instance's key is invalidated."""

    def test_only_modified_instances_key_deleted(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC1: Hero(id=1) committed → id=1 deleted, id=2 untouched."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [(HeroInv, {"id": 1})]
        _after_commit_handler(session)

        assert "sqlmodelcache:HeroInv:id=1" in configured_tracking.delete_calls
        assert "sqlmodelcache:HeroInv:id=2" not in configured_tracking.delete_calls

    def test_composite_pk_key_format(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC2: composite PK fields sorted alphabetically by build_key()."""
        session = FakeSession()
        # Provide dict with non-alphabetical insertion order — build_key sorts it
        session.info[_PENDING_KEY] = [(HeroTeamInv, {"team_id": 42, "hero_id": 7})]
        _after_commit_handler(session)

        assert (
            "sqlmodelcache:HeroTeamInv:hero_id=7:team_id=42"
            in configured_tracking.delete_calls
        )

    def test_multiple_instances_in_one_commit(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC3: both Hero(id=1) and Hero(id=2) deleted in one commit."""
        session = FakeSession()
        session.info[_PENDING_KEY] = [
            (HeroInv, {"id": 1}),
            (HeroInv, {"id": 2}),
        ]
        _after_commit_handler(session)

        assert "sqlmodelcache:HeroInv:id=1" in configured_tracking.delete_calls
        assert "sqlmodelcache:HeroInv:id=2" in configured_tracking.delete_calls

    def test_composite_pk_alphabetical_order_regardless_of_dict_order(
        self, configured_tracking: TrackingTransport
    ) -> None:
        """AC2 variant: dict insertion order does not affect key format."""
        session = FakeSession()
        # Pass hero_id first (alphabetical) — result must be same
        session.info[_PENDING_KEY] = [(HeroTeamInv, {"hero_id": 7, "team_id": 42})]
        _after_commit_handler(session)

        assert (
            "sqlmodelcache:HeroTeamInv:hero_id=7:team_id=42"
            in configured_tracking.delete_calls
        )

    def testextract_pk_from_instance_single_pk(self) -> None:
        """extract_pk_from_instance returns correct dict for single-PK model."""
        hero = HeroInv(id=42, name="Test")
        pk = extract_pk_from_instance(hero)
        assert pk == {"id": 42}

    def testextract_pk_from_instance_composite_pk(self) -> None:
        """extract_pk_from_instance returns correct dict for composite-PK model."""
        ht = HeroTeamInv(hero_id=7, team_id=42)
        pk = extract_pk_from_instance(ht)
        assert pk == {"hero_id": 7, "team_id": 42}
