"""Unit tests for error hierarchy — Story 1.5."""

from __future__ import annotations

import pytest

import sqlmodel_cache
from sqlmodel_cache import CacheError, ConfigurationError


def test_configuration_error_is_subclass_of_cache_error() -> None:
    assert issubclass(ConfigurationError, CacheError)


def test_cache_error_is_subclass_of_exception() -> None:
    assert issubclass(CacheError, Exception)


def test_configuration_error_is_subclass_of_exception() -> None:
    # transitive: ConfigurationError → CacheError → Exception
    assert issubclass(ConfigurationError, Exception)


def test_configuration_error_carries_message() -> None:
    msg = "SQLModelCache.configure() must be called first"
    with pytest.raises(ConfigurationError) as exc_info:
        raise ConfigurationError(msg)
    assert str(exc_info.value) == msg


def test_cache_error_can_be_raised_and_caught() -> None:
    with pytest.raises(CacheError):
        raise CacheError("base error")


def test_except_cache_error_catches_configuration_error() -> None:
    caught = False
    try:
        raise ConfigurationError("test")
    except CacheError:
        caught = True
    assert caught is True


def test_both_in_dunder_all() -> None:
    assert "CacheError" in sqlmodel_cache.__all__
    assert "ConfigurationError" in sqlmodel_cache.__all__


def test_cache_error_message_empty() -> None:
    exc = CacheError()
    assert str(exc) == ""


def test_configuration_error_no_args() -> None:
    exc = ConfigurationError()
    assert isinstance(exc, CacheError)
