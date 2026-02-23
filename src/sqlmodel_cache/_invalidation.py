"""Cache invalidation event handlers for SQLModel sessions.

Three SQLAlchemy session events are used:

- ``after_flush``  — collect cache-enabled dirty/deleted instances into
  ``session.info`` before the DB write.
- ``after_commit`` — delete collected cache keys after the DB commit succeeds.
- ``after_rollback`` — discard collected keys without touching the cache when
  the transaction is rolled back.

All handlers are registered by ``SQLModelCache.configure()`` and removed by
``SQLModelCache.reset()``.  Never import this module at the top level outside
of ``_config.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlmodel_cache import _state
from sqlmodel_cache.keys import build_key, extract_pk_from_instance

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger("sqlmodel_cache")

# Shared key for per-session pending-invalidation storage.
# Stored in session.info — the SQLAlchemy-blessed per-session dict.
_PENDING_KEY = "_sqlmodelcache_pending_invalidation"

__all__ = [
    "_after_commit_handler",
    "_after_flush_handler",
    "_after_rollback_handler",
    "_async_after_commit_handler",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _after_flush_handler(session: Session, flush_context: Any) -> None:
    """Collect cache-enabled dirty and deleted instances before the DB write.

    Populates ``session.info[_PENDING_KEY]`` with ``(model_cls, pk_dict)``
    tuples for every cache-enabled instance in ``session.dirty`` and
    ``session.deleted``.  Instances in ``session.new`` (inserts) are
    intentionally skipped — there is no existing cache entry to invalidate.

    ``after_flush`` fires before ``session.dirty`` / ``session.deleted`` are
    cleared, making it the only correct collection point.
    """
    pending: list[tuple[type, dict[str, Any]]] = session.info.setdefault(
        _PENDING_KEY, []
    )
    for instance in (  # type: ignore[reportUnknownVariableType]
        *session.dirty,  # type: ignore[reportUnknownArgumentType]
        *session.deleted,  # type: ignore[reportUnknownArgumentType]
    ):
        cache_cfg = getattr(type(instance), "__cache_config__", None)  # type: ignore[reportUnknownArgumentType]
        if cache_cfg is None or not cache_cfg.enabled:
            continue
        pending.append((type(instance), extract_pk_from_instance(instance)))  # type: ignore[reportUnknownArgumentType]


def _after_commit_handler(session: Session) -> None:
    """Delete cache keys for all collected instances after a successful commit.

    Pops the pending list so no keys bleed into the next transaction.
    Exceptions from ``transport.delete()`` are caught, logged at WARNING, and
    suppressed — the DB commit already succeeded and a Redis outage must never
    surface as an application error (fail-open, NFR-REL-4).
    """
    pending: list[Any] = session.info.pop(_PENDING_KEY, [])
    if not pending:
        return
    config = _state.get_config()
    if not config.enabled:
        return
    for model_cls, pk_dict in pending:
        key = build_key(model_cls, pk_dict)
        try:
            config.transport.delete(key)
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: invalidation_failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )
            # Never re-raise — DB commit succeeded; cache is stale but recoverable


def _after_rollback_handler(session: Session) -> None:
    """Clear pending invalidation keys on rollback — never touch the cache.

    A rolled-back transaction means the DB write did not happen, so the cached
    value (reflecting the pre-flush state) is still correct.  Clearing the
    pending list prevents keys from leaking into the next transaction on the
    same session.
    """
    session.info[_PENDING_KEY] = []


def _async_after_commit_handler(session: Session) -> None:
    """Delete cache keys via async transport after a successful async commit.

    Mirrors ``_after_commit_handler`` exactly but uses ``await_only()`` from
    SQLAlchemy's greenlet bridge to schedule the async ``transport.delete()``
    coroutine back onto the running event loop.  This handler is a regular
    ``def`` — async event handlers are not supported by SQLAlchemy's sync event
    system, but ``AsyncSession.commit()`` runs within a greenlet context that
    makes ``await_only()`` safe to call.

    Registered by ``configure()`` in place of ``_after_commit_handler`` when
    ``is_async_transport(transport)`` is ``True``.

    Never raises — follows the same fail-open contract as the sync handler.
    """
    from sqlalchemy.util._concurrency_py3k import await_only  # greenlet bridge

    pending: list[Any] = session.info.pop(_PENDING_KEY, [])
    if not pending:
        return
    config = _state.get_config()
    if not config.enabled:
        return
    for model_cls, pk_dict in pending:
        key = build_key(model_cls, pk_dict)
        try:
            await_only(config.transport.delete(key))  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: async_invalidation_failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )
            # Never re-raise — DB commit succeeded; cache is stale but recoverable
