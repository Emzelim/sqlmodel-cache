"""Pydantic v2 serialization layer for sqlmodel-cache.

Provides ``serialize()`` and ``deserialize()`` as the exclusive interface for
converting SQLModel instances to/from the bytes stored in Redis.

Pydantic v2's ``model_dump_json()`` / ``model_validate_json()`` handles all
standard field types natively — ``uuid.UUID``, ``datetime``, ``Enum``,
``Optional[T]``, nested models — with no custom logic required.

.. note::

    SQLModel ``table=True`` models set ``arbitrary_types_allowed=True`` in
    their Pydantic config, which prevents Pydantic from coercing ``uuid.UUID``
    fields from JSON strings automatically.  :func:`deserialize` applies a
    post-validation coercion pass to restore native types.
"""

from __future__ import annotations

import contextlib
import uuid as _uuid
from typing import TypeVar, Union, get_args, get_origin

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def serialize(instance: BaseModel) -> bytes:
    """Serialize a SQLModel/Pydantic model instance to UTF-8 JSON bytes.

    Args:
        instance: Any Pydantic v2 ``BaseModel`` subclass instance (including
            all SQLModel models).

    Returns:
        UTF-8 encoded JSON bytes suitable for storage in Redis.
    """
    return instance.model_dump_json().encode()


def deserialize(data: bytes, cls: type[ModelT]) -> ModelT:
    """Deserialize UTF-8 JSON bytes back into a model instance.

    Args:
        data: Bytes previously produced by ``serialize()``.
        cls: The target model class.  Must be a Pydantic v2 ``BaseModel``
            subclass.

    Returns:
        A fully hydrated instance of ``cls`` with all fields restored to their
        native Python types (``uuid.UUID``, ``datetime``, ``Enum``, etc.).
    """
    instance = cls.model_validate_json(data)
    _coerce_uuid_fields(instance)
    return instance


def _coerce_uuid_fields(instance: BaseModel) -> None:
    """Coerce ``str`` values back to ``uuid.UUID`` for UUID-annotated fields.

    SQLModel ``table=True`` models use ``arbitrary_types_allowed=True``, which
    disables Pydantic's UUID validator during ``model_validate_json()``.
    This helper applies the coercion after the fact so callers always receive
    proper ``uuid.UUID`` instances for UUID-typed fields.

    No-op for fields that already contain a ``uuid.UUID`` or are ``None``.
    """
    for field_name, field_info in type(instance).model_fields.items():
        annotation = field_info.annotation
        # Unwrap Optional[uuid.UUID] / Union[uuid.UUID, None]
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                annotation = non_none[0]
        if annotation is _uuid.UUID:
            val = getattr(instance, field_name, None)
            if isinstance(val, str):
                with contextlib.suppress(ValueError, AttributeError):
                    setattr(instance, field_name, _uuid.UUID(val))
