"""Unit tests for CacheConfig dataclass (Story 1.2) and SQLModelCache (Story 1.6)."""

from __future__ import annotations

import dataclasses
from collections.abc import Generator

import pytest
from fakes import FakeTransport

from sqlmodel_cache import CacheConfig, SQLModelCache, _state
from sqlmodel_cache._errors import ConfigurationError


# AC1 ── basic TTL field and defaults
def test_cacheconfig_defaults() -> None:
    cfg = CacheConfig()
    assert cfg.enabled is True
    assert cfg.ttl is None


def test_cacheconfig_ttl_field() -> None:
    cfg = CacheConfig(ttl=300)
    assert cfg.ttl == 300
    assert cfg.enabled is True


# AC1 ── frozen (immutable)
def test_cacheconfig_is_frozen() -> None:
    cfg = CacheConfig(ttl=60)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.enabled = False  # type: ignore[misc]


# AC2 ── enabled=False
def test_cacheconfig_enabled_false() -> None:
    cfg = CacheConfig(enabled=False)
    assert cfg.enabled is False


# AC3 ── default ttl is None
def test_cacheconfig_default_ttl_is_none() -> None:
    assert CacheConfig().ttl is None


# AC4 ── hasattr detection — positive
def test_hasattr_detection_positive() -> None:
    class ModelWithCache:
        __cache_config__ = CacheConfig(ttl=60)

    assert hasattr(ModelWithCache, "__cache_config__") is True
    assert ModelWithCache.__cache_config__.enabled is True


# AC4 ── hasattr detection — negative
def test_hasattr_detection_negative() -> None:
    class PlainModel:
        pass

    assert hasattr(PlainModel, "__cache_config__") is False


# AC2 ── interceptor check pattern
def test_enabled_check_pattern() -> None:
    """Mirrors exactly what the interceptor checks — AC2."""

    class CachedModel:
        __cache_config__ = CacheConfig(enabled=False)

    result = (
        hasattr(CachedModel, "__cache_config__")
        and CachedModel.__cache_config__.enabled
    )
    assert result is False


# AC5 ── importable from root
def test_cacheconfig_importable_from_root() -> None:
    from sqlmodel_cache import CacheConfig as CC

    assert CC is CacheConfig


# ── SQLModelCache tests (Story 1.6) ─────────────────────────────────────────


@pytest.fixture()
def reset_cache() -> Generator[None, None, None]:
    yield
    SQLModelCache.reset()


def test_configure_stores_transport(reset_cache: None) -> None:
    transport = FakeTransport()
    SQLModelCache.configure(transport=transport)
    assert _state.get_config().transport is transport


def test_configure_stores_default_ttl(reset_cache: None) -> None:
    SQLModelCache.configure(transport=FakeTransport(), default_ttl=120)
    assert _state.get_config().default_ttl == 120


def test_configure_stores_key_prefix(reset_cache: None) -> None:
    SQLModelCache.configure(transport=FakeTransport(), key_prefix="myapp")
    assert _state.get_config().key_prefix == "myapp"


def test_configure_default_key_prefix(reset_cache: None) -> None:
    SQLModelCache.configure(transport=FakeTransport())
    assert _state.get_config().key_prefix == "sqlmodelcache"


def test_get_config_before_configure_raises() -> None:
    SQLModelCache.reset()
    with pytest.raises(ConfigurationError):
        _state.get_config()


def test_configure_twice_no_error(reset_cache: None) -> None:
    t1 = FakeTransport()
    t2 = FakeTransport()
    SQLModelCache.configure(transport=t1)
    SQLModelCache.configure(transport=t2)
    assert _state.get_config().transport is t2


def test_sqlmodelcache_importable() -> None:
    from sqlmodel_cache import SQLModelCache as SC

    assert SC is SQLModelCache
