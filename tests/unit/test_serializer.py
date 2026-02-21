"""Unit tests for serialize/deserialize — Story 1.4."""
from __future__ import annotations

import enum
import json
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from sqlmodel_cache.serializer import deserialize, serialize

# ---------------------------------------------------------------------------
# Test models — plain Pydantic BaseModel (no DB, no SQLModel table=True)
# ---------------------------------------------------------------------------


class Hero(BaseModel):
    id: int
    name: str


class ItemWithUUID(BaseModel):
    id: uuid.UUID
    label: str


class EventWithDatetime(BaseModel):
    name: str
    occurred_at: datetime


class Status(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ThingWithEnum(BaseModel):
    status: Status


class RecordWithOptional(BaseModel):
    nickname: str | None = None


# ---------------------------------------------------------------------------
# AC1 — serialize returns bytes
# ---------------------------------------------------------------------------


def test_serialize_returns_bytes() -> None:
    hero = Hero(id=1, name="Deadpond")
    result = serialize(hero)
    assert isinstance(result, bytes)


# serialize output is valid UTF-8 JSON
def test_serialize_is_valid_json() -> None:
    hero = Hero(id=1, name="Deadpond")
    data = serialize(hero)
    parsed = json.loads(data)
    assert parsed["id"] == 1
    assert parsed["name"] == "Deadpond"


# AC2 — deserialize returns correct type and values
def test_deserialize_returns_correct_instance() -> None:
    hero = Hero(id=1, name="Deadpond")
    data = serialize(hero)
    result = deserialize(data, Hero)
    assert isinstance(result, Hero)
    assert result.id == 1
    assert result.name == "Deadpond"


# AC3 — UUID round-trip
def test_uuid_round_trip() -> None:
    original_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    item = ItemWithUUID(id=original_id, label="sword")
    data = serialize(item)
    result = deserialize(data, ItemWithUUID)
    assert isinstance(result.id, uuid.UUID)
    assert result.id == original_id


# AC4 — datetime round-trip
def test_datetime_round_trip() -> None:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    event = EventWithDatetime(name="launch", occurred_at=now)
    data = serialize(event)
    result = deserialize(data, EventWithDatetime)
    assert isinstance(result.occurred_at, datetime)
    assert result.occurred_at == now


# AC5 — Enum round-trip
def test_enum_round_trip() -> None:
    thing = ThingWithEnum(status=Status.ACTIVE)
    data = serialize(thing)
    result = deserialize(data, ThingWithEnum)
    assert isinstance(result.status, Status)
    assert result.status == Status.ACTIVE


# AC6 — Optional[str] = None round-trip
def test_optional_none_round_trip() -> None:
    record = RecordWithOptional(nickname=None)
    data = serialize(record)
    result = deserialize(data, RecordWithOptional)
    assert result.nickname is None


# AC6 — Optional[str] with value round-trip
def test_optional_str_round_trip() -> None:
    record = RecordWithOptional(nickname="Pow")
    data = serialize(record)
    result = deserialize(data, RecordWithOptional)
    assert result.nickname == "Pow"
