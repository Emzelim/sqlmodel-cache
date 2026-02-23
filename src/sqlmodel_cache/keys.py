"""Canonical cache key construction for sqlmodel-cache.

All cache read, write, and invalidation operations MUST go through
``build_key()`` — never construct a key string inline in other modules.

Key format::

    sqlmodelcache:{ModelClassName}:{field1}={value1}:{field2}={value2}

Fields are always sorted alphabetically so composite PKs are deterministic
regardless of insertion order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect as sa_inspect

if TYPE_CHECKING:
    from sqlalchemy.orm import ORMExecuteState

CACHE_KEY_PREFIX: str = "sqlmodelcache"


def extract_pk_from_instance(instance: Any) -> dict[str, Any]:
    """Return ``{field_name: value}`` for every primary-key column of *instance*."""
    mapper: Any = sa_inspect(type(instance))  # type: ignore[reportUnknownVariableType]
    return {
        col.key: getattr(instance, col.key)  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        for col in mapper.mapper.primary_key  # type: ignore[reportUnknownMemberType]
    }


def extract_pk_from_state(state: ORMExecuteState) -> dict[str, Any]:
    """Extract primary-key field name → value mapping from a ``session.get()`` state.

    SQLAlchemy binds PK values into ``state.parameters`` ordered to match
    ``mapper.primary_key`` column order (e.g. ``{"pk_1": 1}``).
    """
    stmt: Any = (
        state.statement
    )  # cast required: pyright stubs omit SQLAlchemy internals
    descs: list[dict[str, Any]] = stmt.column_descriptions
    model_cls: type = descs[0]["entity"]
    mapper: Any = sa_inspect(model_cls)  # type: ignore[reportUnknownVariableType]
    pk_cols: list[Any] = list(mapper.mapper.primary_key)  # type: ignore[reportUnknownArgumentType]
    params: Any = (
        state.parameters or {}
    )  # may be Mapping or dict depending on SQLAlchemy version
    pk_values: list[Any] = (
        list(params.values()) if isinstance(params, dict) else list(params)
    )  # type: ignore[reportUnknownArgumentType]
    return {
        col.key: pk_values[i] for i, col in enumerate(pk_cols) if i < len(pk_values)
    }


def build_key(cls: type, pk_dict: dict[str, Any]) -> str:
    """Build a canonical Redis cache key for a model instance.

    Args:
        cls: The SQLModel class whose ``__name__`` is used as the key segment.
        pk_dict: Mapping of primary key field name(s) to their values.
            Composite PKs are supported; fields are sorted alphabetically.

    Returns:
        A string of the form ``"sqlmodelcache:{ClassName}:{f1}={v1}:..."``.

    Raises:
        ValueError: If ``pk_dict`` is empty.

    Examples:
        >>> build_key(Hero, {"id": 1})
        'sqlmodelcache:Hero:id=1'
        >>> build_key(HeroTeam, {"team_id": 42, "hero_id": 7})
        'sqlmodelcache:HeroTeam:hero_id=7:team_id=42'
    """
    if not pk_dict:
        raise ValueError(
            f"build_key() requires at least one primary key field for "
            f"{cls.__name__!r}, but pk_dict was empty."
        )

    parts: list[str] = []
    for field_name in sorted(pk_dict):
        value = pk_dict[field_name]
        if value is None:
            raise ValueError(
                f"build_key() received None for field {field_name!r} on "
                f"{cls.__name__!r}. PK must be set before building a cache key."
            )
        # uuid.UUID.__str__() always produces canonical lowercase hyphenated form
        str_value = str(value)
        parts.append(f"{field_name}={str_value}")

    return f"{CACHE_KEY_PREFIX}:{cls.__name__}:{':'.join(parts)}"
