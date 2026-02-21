"""Unit tests for build_key() — Story 1.3."""
from __future__ import annotations

import uuid

import pytest

from sqlmodel_cache.keys import build_key


class Hero:
    pass


class HeroTeam:
    pass


class Item:
    pass


class UserProfile:
    pass


class Post:
    pass


# AC1 ── single int PK
def test_single_int_pk() -> None:
    result = build_key(Hero, {"id": 1})
    assert result == "sqlmodelcache:Hero:id=1"


# AC2 ── composite PK sorted alphabetically (unordered input)
def test_composite_pk_sorted_alphabetically() -> None:
    result = build_key(HeroTeam, {"team_id": 42, "hero_id": 7})
    assert result == "sqlmodelcache:HeroTeam:hero_id=7:team_id=42"


# AC2 ── composite PK already sorted (idempotent)
def test_composite_pk_already_sorted() -> None:
    result = build_key(HeroTeam, {"hero_id": 7, "team_id": 42})
    assert result == "sqlmodelcache:HeroTeam:hero_id=7:team_id=42"


# AC3 ── UUID value → lowercase hyphenated string
def test_uuid_pk_lowercase_hyphenated() -> None:
    pk = uuid.UUID("12345678-1234-5678-1234-567812345678")
    result = build_key(Item, {"id": pk})
    assert result == "sqlmodelcache:Item:id=12345678-1234-5678-1234-567812345678"


# AC5 ── empty dict raises ValueError
def test_empty_pk_dict_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        build_key(Hero, {})


# M1 fix ── None PK value raises ValueError (guards against pre-flush objects)
def test_none_pk_value_raises_value_error() -> None:
    with pytest.raises(ValueError, match="None"):
        build_key(Hero, {"id": None})


# class name case preserved
def test_class_name_case_preserved() -> None:
    result = build_key(UserProfile, {"id": 99})
    assert "UserProfile" in result
    assert result == "sqlmodelcache:UserProfile:id=99"


# string PK
def test_string_pk() -> None:
    result = build_key(Post, {"slug": "hero-one"})
    assert result == "sqlmodelcache:Post:slug=hero-one"
