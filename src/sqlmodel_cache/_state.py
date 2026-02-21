"""Global configuration singleton for sqlmodel-cache.

This module holds the single write point for library state.  All other modules
call ``get_config()`` (read-only).  Only ``_config.py`` (via
``SQLModelCache.configure()``) calls ``set_config()``.

``reset_config()`` is provided exclusively for test isolation — it must NOT
be called in production code.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel_cache._errors import ConfigurationError

if TYPE_CHECKING:
    from sqlmodel_cache.transport._protocols import AsyncCacheTransport, CacheTransport


@dataclass
class LibraryConfig:
    """Runtime configuration snapshot set by ``SQLModelCache.configure()``."""

    transport: CacheTransport | AsyncCacheTransport
    default_ttl: int
    key_prefix: str
    enabled: bool = True


_config: LibraryConfig | None = None


def get_config() -> LibraryConfig:
    """Return the active library configuration.

    Raises:
        ConfigurationError: If ``SQLModelCache.configure()`` has not been called.
    """
    if _config is None:
        raise ConfigurationError(
            "SQLModelCache.configure() must be called before any cache "
            "operations.  Call it once at application startup with a "
            "transport and optional default_ttl."
        )
    return _config


def set_config(cfg: LibraryConfig) -> None:
    """Set the active library configuration (called only by configure())."""
    global _config
    _config = cfg


def reset_config() -> None:
    """Clear the active configuration. For test isolation only."""
    global _config
    _config = None


def is_async_transport(transport: object) -> bool:
    """Return ``True`` if the transport uses async methods (AsyncCacheTransport).

    Uses ``inspect.iscoroutinefunction`` on the ``get`` method — the canonical
    check for structural async transport detection without importing
    ``AsyncCacheTransport`` at runtime.
    """
    return inspect.iscoroutinefunction(getattr(transport, "get", None))
