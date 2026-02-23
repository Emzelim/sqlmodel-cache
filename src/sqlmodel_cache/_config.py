"""Cache opt-in configuration dataclass and global library configurator.

``CacheConfig`` — per-model cache settings assigned via ``__cache_config__``.
``SQLModelCache`` — application-level configure/reset interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlmodel_cache._state import LibraryConfig
    from sqlmodel_cache.transport._protocols import AsyncCacheTransport, CacheTransport


@dataclass(frozen=True)
class CacheConfig:
    """Per-model cache configuration.

    Assign as a class variable on any SQLModel subclass::

        class Hero(SQLModel, table=True):
            __cache_config__ = CacheConfig(ttl=300)
            id: int | None = Field(default=None, primary_key=True)
            name: str

    Attributes:
        enabled: When ``False``, caching is disabled for this model even if
            the library is configured.  Defaults to ``True``.
        ttl: Time-to-live in seconds for cached entries.  ``None`` means fall
            back to the global ``default_ttl`` set in
            ``SQLModelCache.configure()``.
    """

    enabled: bool = True
    ttl: int | None = None


class SQLModelCache:
    """Application-level interface for configuring sqlmodel-cache.

    Call ``configure()`` once at startup before any SQLModel session
    operations on cache-enabled models.
    """

    @classmethod
    def configure(
        cls,
        transport: CacheTransport | AsyncCacheTransport,
        *,
        default_ttl: int = 300,
        key_prefix: str = "sqlmodelcache",
        enabled: bool = True,
    ) -> None:
        """Configure the library with a transport and global defaults.

        Args:
            transport: A ``CacheTransport`` implementation (e.g.
                ``RedisSyncTransport``).  ``FakeTransport`` from
                ``tests/unit/fakes.py`` is suitable for tests.
            default_ttl: Default TTL in seconds for cached entries when the
                model's ``CacheConfig.ttl`` is ``None``.  Defaults to 300.
            key_prefix: Redis key prefix.  Defaults to ``"sqlmodelcache"``.
                Override per-deployment to avoid collisions across apps.
            enabled: When ``False``, the library is fully disabled — no cache
                reads, writes, or invalidations occur.  All ``session.get()``
                calls fall through to the database.  Defaults to ``True``.
        """
        from sqlmodel_cache import _state

        cfg = _state.LibraryConfig(
            transport=transport,
            default_ttl=default_ttl,
            key_prefix=key_prefix,
            enabled=enabled,
        )
        _state.set_config(cfg)

        from sqlalchemy import event
        from sqlmodel import Session

        from sqlmodel_cache._interceptor import (
            _async_cache_on_execute,  # type: ignore[reportPrivateUsage]
            _cache_on_execute,  # type: ignore[reportPrivateUsage]  # inside body to avoid circular import
        )
        from sqlmodel_cache._invalidation import (  # type: ignore[reportPrivateUsage]
            _after_commit_handler,  # type: ignore[reportPrivateUsage]
            _after_flush_handler,  # type: ignore[reportPrivateUsage]
            _after_rollback_handler,  # type: ignore[reportPrivateUsage]
            _async_after_commit_handler,  # type: ignore[reportPrivateUsage]
        )

        # Register the appropriate interceptor handler:
        # - async transport → _async_cache_on_execute (uses await_only greenlet bridge)
        # - sync transport  → _cache_on_execute (direct sync calls)
        # Both are registered on Session rather than AsyncSession because AsyncSession
        # does not support do_orm_execute directly — it proxies through its sync_session
        # which dispatches on the Session class. await_only() handles the async bridging.
        # When reconfiguring, remove the opposite handler to keep them mutually exclusive.
        if _state.is_async_transport(transport):
            if event.contains(Session, "do_orm_execute", _cache_on_execute):
                event.remove(Session, "do_orm_execute", _cache_on_execute)
            if not event.contains(Session, "do_orm_execute", _async_cache_on_execute):
                event.listen(Session, "do_orm_execute", _async_cache_on_execute)
        else:
            if event.contains(Session, "do_orm_execute", _async_cache_on_execute):
                event.remove(Session, "do_orm_execute", _async_cache_on_execute)
            if not event.contains(Session, "do_orm_execute", _cache_on_execute):
                event.listen(Session, "do_orm_execute", _cache_on_execute)
        if not event.contains(Session, "after_flush", _after_flush_handler):
            event.listen(Session, "after_flush", _after_flush_handler)
        # after_commit: async transport uses await_only-bridged handler; sync uses direct handler.
        # after_flush and after_rollback fire via the underlying sync session for AsyncSession too,
        # so no separate async versions are needed for those two events.
        # When reconfiguring, remove the opposite commit handler to keep them mutually exclusive.
        if _state.is_async_transport(transport):
            if event.contains(Session, "after_commit", _after_commit_handler):
                event.remove(Session, "after_commit", _after_commit_handler)
            if not event.contains(Session, "after_commit", _async_after_commit_handler):
                event.listen(Session, "after_commit", _async_after_commit_handler)
        else:
            if event.contains(Session, "after_commit", _async_after_commit_handler):
                event.remove(Session, "after_commit", _async_after_commit_handler)
            if not event.contains(Session, "after_commit", _after_commit_handler):
                event.listen(Session, "after_commit", _after_commit_handler)
        if not event.contains(Session, "after_rollback", _after_rollback_handler):
            event.listen(Session, "after_rollback", _after_rollback_handler)

    @classmethod
    def reset(cls) -> None:
        """Clear configuration and remove event listeners.

        Intended exclusively for test isolation — call in pytest fixture
        teardown to prevent state leaking between tests::

            @pytest.fixture(autouse=True)
            def reset_cache():
                yield
                SQLModelCache.reset()
        """
        from sqlalchemy import event
        from sqlmodel import Session

        from sqlmodel_cache import _state
        from sqlmodel_cache._interceptor import (
            _async_cache_on_execute,  # type: ignore[reportPrivateUsage]
            _cache_on_execute,  # type: ignore[reportPrivateUsage]  # inside body to avoid circular import
        )
        from sqlmodel_cache._invalidation import (  # type: ignore[reportPrivateUsage]
            _after_commit_handler,  # type: ignore[reportPrivateUsage]
            _after_flush_handler,  # type: ignore[reportPrivateUsage]
            _after_rollback_handler,  # type: ignore[reportPrivateUsage]
            _async_after_commit_handler,  # type: ignore[reportPrivateUsage]
        )

        if event.contains(Session, "do_orm_execute", _cache_on_execute):
            event.remove(Session, "do_orm_execute", _cache_on_execute)
        if event.contains(Session, "do_orm_execute", _async_cache_on_execute):
            event.remove(Session, "do_orm_execute", _async_cache_on_execute)
        if event.contains(Session, "after_flush", _after_flush_handler):
            event.remove(Session, "after_flush", _after_flush_handler)
        if event.contains(Session, "after_commit", _after_commit_handler):
            event.remove(Session, "after_commit", _after_commit_handler)
        if event.contains(Session, "after_commit", _async_after_commit_handler):
            event.remove(Session, "after_commit", _async_after_commit_handler)
        if event.contains(Session, "after_rollback", _after_rollback_handler):
            event.remove(Session, "after_rollback", _after_rollback_handler)

        _state.reset_config()

    @classmethod
    def get_config(cls) -> LibraryConfig:
        """Return active configuration; raises ConfigurationError if unconfigured."""
        from sqlmodel_cache import _state

        return _state.get_config()
