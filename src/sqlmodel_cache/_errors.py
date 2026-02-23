"""Library-specific exception hierarchy for sqlmodel-cache.

All exceptions raised by sqlmodel-cache are subclasses of ``CacheError``,
allowing callers to catch the entire library's error surface with a single
``except CacheError`` clause.

Hierarchy::

    Exception
    └── CacheError
        └── ConfigurationError
"""

from __future__ import annotations


class CacheError(Exception):
    """Base exception for all sqlmodel-cache errors.

    Catch this to handle any error raised by the library::

        try:
            session.get(Hero, 1)
        except CacheError:
            ...  # library-level error (not Redis errors — those are suppressed)
    """


class ConfigurationError(CacheError):
    """Raised when a cache operation is attempted before configuration.

    This is raised by ``_state.get_config()`` when ``SQLModelCache.configure()``
    has not yet been called at application startup::

        # triggers ConfigurationError:
        session.get(Hero, 1)  # before SQLModelCache.configure(...)

    Resolution: call ``SQLModelCache.configure(transport=..., default_ttl=...)``
    once at application startup before any SQLModel session operations.
    """
