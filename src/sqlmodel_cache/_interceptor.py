"""do_orm_execute interceptor for transparent session.get() caching.

Registered by ``SQLModelCache.configure()`` and removed by ``SQLModelCache.reset()``.
Never imported at module level outside of ``_config.py``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData
from sqlalchemy.orm import make_transient_to_detached

from sqlmodel_cache import _state
from sqlmodel_cache.keys import build_key, extract_pk_from_state
from sqlmodel_cache.serializer import deserialize, serialize

if TYPE_CHECKING:
    from sqlalchemy.orm import ORMExecuteState

    from sqlmodel_cache._config import CacheConfig

logger = logging.getLogger("sqlmodel_cache")

__all__ = ["_async_cache_on_execute", "_cache_on_execute"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_cache_config(state: ORMExecuteState) -> CacheConfig | None:
    """Return the ``CacheConfig`` if the statement targets a cache-eligible model.

    Returns ``None`` for any non-ORM, non-SELECT, or uncached statement so
    that ``_cache_on_execute`` can short-circuit immediately.
    """
    if not state.is_orm_statement or not state.is_select:
        return None
    descs = getattr(state.statement, "column_descriptions", None)
    if not descs:
        return None
    model_cls = descs[0].get("entity")
    if model_cls is None:
        return None
    cache_cfg = getattr(model_cls, "__cache_config__", None)
    if cache_cfg is None or not cache_cfg.enabled:
        return None
    return cache_cfg  # type: ignore[return-value]


def _resolve_ttl(cache_cfg: CacheConfig, global_default_ttl: int) -> int:
    """Resolve effective TTL in seconds.

    Priority:

    1. ``CacheConfig.ttl`` — model-level override (highest priority).
    2. ``LibraryConfig.default_ttl`` — global default set in ``configure()``.
       The Python default for ``configure(default_ttl=300)`` covers the
       "library default" tier without a third code branch.

    Args:
        cache_cfg: The ``CacheConfig`` instance from the model's ``__cache_config__``.
        global_default_ttl: ``LibraryConfig.default_ttl`` from the active config.

    Returns:
        TTL in seconds to pass to ``transport.set()``.
    """
    return cache_cfg.ttl if cache_cfg.ttl is not None else global_default_ttl


def _make_cache_hit_result(
    state: ORMExecuteState,
    instance: Any,
    model_cls: type,
) -> IteratorResult[Any]:
    """Wrap a deserialized cached instance as a SQLAlchemy ``Result``.

    Uses ``make_transient_to_detached`` + ``session.merge(load=False)`` to
    place the instance into the session identity map without a DB round-trip,
    then wraps it in an ``IteratorResult`` with scalar-source semantics so
    the ORM returns it directly to the caller.
    """
    make_transient_to_detached(instance)
    merged = state.session.merge(instance, load=False)
    meta = SimpleResultMetaData([model_cls.__name__])
    return IteratorResult(meta, iter([merged]), _source_supports_scalars=True)


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


def _cache_on_execute(state: ORMExecuteState) -> Any:
    """``do_orm_execute`` event handler — transparent cache hit/miss logic.

    Returns a SQLAlchemy ``Result`` to short-circuit the DB on a cache hit,
    or ``None`` to pass through ineligible statements unchanged.

    Never raises — any transport or deserialization failure is logged at
    WARNING level and the call falls through to the database (fail-open).
    """
    cache_cfg = _get_cache_config(state)
    if cache_cfg is None:
        return None  # not eligible — let the ORM proceed normally

    config = _state.get_config()
    exec_opts = state.execution_options

    if not config.enabled:
        return None  # caching fully disabled — let the ORM proceed to the DB

    # Key name "cache" is the authoritative contract.
    if not exec_opts.get("cache", True):
        logger.debug("sqlmodel-cache: bypassing cache for query")
        return None  # per-call bypass — skips both cache read and cache write

    stmt: Any = state.statement  # cast required: pyright stubs omit SQLAlchemy internals
    descs: list[dict[str, Any]] = stmt.column_descriptions
    model_cls: type = descs[0]["entity"]
    pk_dict = extract_pk_from_state(state)
    key = build_key(model_cls, pk_dict)

    # ── 1. Attempt cache read (fail-open on transport error) ─────────────────
    raw: bytes | None = None
    try:
        raw = config.transport.get(key)  # type: ignore[assignment]  # sync transport confirmed by configure()
    except Exception as exc:
        logger.warning(
            "sqlmodel-cache: cache_read failed key=%s exc_type=%s",
            key,
            type(exc).__name__,
        )
        # raw stays None → falls through to DB

    # ── 2. Cache HIT — return deserialized instance without DB query ──────────
    if raw is not None:
        try:
            instance: Any = deserialize(raw, model_cls)  # type: ignore[reportUnknownVariableType]
            return _make_cache_hit_result(state, instance, model_cls)
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: cache_read deserialize failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )
            # fall through to DB on deserialization failure

    # ── 3. Cache MISS — execute DB query and write-back ───────────────────────
    result = state.invoke_statement()
    frozen = result.freeze()
    instance: Any = next(iter(frozen().scalars()), None)  # type: ignore[reportUnknownVariableType]

    if instance is not None:
        # Key name "cache_ttl" is the authoritative contract.
        per_call_ttl: int | None = exec_opts.get("cache_ttl", None)
        effective_ttl = (
            per_call_ttl
            if per_call_ttl is not None
            else _resolve_ttl(cache_cfg, config.default_ttl)
        )
        try:
            config.transport.set(key, serialize(instance), ttl=effective_ttl)
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: cache_write failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )
            # do NOT re-raise — caller still receives the DB result

    return frozen()


# ---------------------------------------------------------------------------
# Async-transport handler (uses greenlet bridging via await_only)
# ---------------------------------------------------------------------------


def _async_cache_on_execute(state: ORMExecuteState) -> Any:
    """``do_orm_execute`` handler for async transports — greenlet-bridged cache.

    Registered on ``Session`` (not ``AsyncSession``) by ``configure()`` when
    an ``AsyncCacheTransport`` is supplied.  When ``AsyncSession.get()``
    dispatches this event, SQLAlchemy's greenlet machinery is already in place,
    so ``await_only()`` may safely schedule coroutines back onto the running
    event loop without blocking.

    Behaviour mirrors ``_cache_on_execute`` exactly: fail-open on any transport
    or deserialisation error, honour ``cache=False`` and ``enabled=False``,
    resolve TTL priority the same way.

    Never raises — all transport failures are logged at WARNING level.

    .. note::
        This handler is a regular synchronous function even though it drives
        an async transport.  ``await_only`` handles the coroutine scheduling;
        the handler itself must be ``def`` because SQLAlchemy's sync event
        system does not support native ``async def`` listeners on
        ``Session.do_orm_execute``.
    """
    from sqlalchemy.util._concurrency_py3k import await_only  # greenlet bridge

    cache_cfg = _get_cache_config(state)
    if cache_cfg is None:
        return None

    config = _state.get_config()
    exec_opts = state.execution_options

    if not config.enabled:
        return None

    if not exec_opts.get("cache", True):
        logger.debug("sqlmodel-cache: async bypassing cache for query")
        return None

    stmt: Any = state.statement
    descs: list[dict[str, Any]] = stmt.column_descriptions
    model_cls: type = descs[0]["entity"]
    pk_dict = extract_pk_from_state(state)
    key = build_key(model_cls, pk_dict)

    # ── 1. Attempt cache read via greenlet bridge ────────────────────────────
    raw: bytes | None = None
    try:
        raw = await_only(config.transport.get(key))  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning(
            "sqlmodel-cache: async_cache_read failed key=%s exc_type=%s",
            key,
            type(exc).__name__,
        )

    # ── 2. Cache HIT — return without DB query ───────────────────────────────
    if raw is not None:
        try:
            instance: Any = deserialize(raw, model_cls)  # type: ignore[reportUnknownVariableType]
            return _make_cache_hit_result(state, instance, model_cls)
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: async_cache_read deserialize failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )

    # ── 3. Cache MISS — execute DB query and write-back ──────────────────────
    result = state.invoke_statement()
    frozen = result.freeze()
    instance: Any = next(iter(frozen().scalars()), None)  # type: ignore[reportUnknownVariableType]

    if instance is not None:
        per_call_ttl: int | None = exec_opts.get("cache_ttl", None)
        effective_ttl = (
            per_call_ttl
            if per_call_ttl is not None
            else _resolve_ttl(cache_cfg, config.default_ttl)
        )
        try:
            await_only(config.transport.set(key, serialize(instance), ttl=effective_ttl))  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "sqlmodel-cache: async_cache_write failed key=%s exc_type=%s",
                key,
                type(exc).__name__,
            )

    return frozen()
