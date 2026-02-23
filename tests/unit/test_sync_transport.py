"""Unit tests for RedisSyncTransport.

All tests use unittest.mock.MagicMock — no live Redis connection is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sqlmodel_cache.transport.sync import RedisSyncTransport


@pytest.fixture()
def mock_redis() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def transport(mock_redis: MagicMock) -> RedisSyncTransport:
    return RedisSyncTransport(client=mock_redis)


class TestRedisSyncTransportGet:
    def test_delegates_to_client_get(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        mock_redis.get.return_value = b"cached"
        result = transport.get("sqlmodelcache:Hero:id=1")
        mock_redis.get.assert_called_once_with("sqlmodelcache:Hero:id=1")
        assert result == b"cached"

    def test_returns_none_on_miss(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        mock_redis.get.return_value = None
        assert transport.get("missing_key") is None

    def test_returns_bytes_on_hit(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        mock_redis.get.return_value = b'{"id": 1}'
        result = transport.get("any_key")
        assert isinstance(result, bytes)


class TestRedisSyncTransportSet:
    def test_delegates_to_client_set_with_ex(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        transport.set("sqlmodelcache:Hero:id=1", b'{"id": 1}', ttl=300)
        mock_redis.set.assert_called_once_with(
            "sqlmodelcache:Hero:id=1", b'{"id": 1}', ex=300
        )

    def test_ttl_passed_as_ex_kwarg(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        transport.set("key", b"value", ttl=600)
        _, kwargs = mock_redis.set.call_args
        assert kwargs.get("ex") == 600


class TestRedisSyncTransportDelete:
    def test_single_key(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        transport.delete("sqlmodelcache:Hero:id=1")
        mock_redis.delete.assert_called_once_with("sqlmodelcache:Hero:id=1")

    def test_multiple_keys(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        transport.delete("key1", "key2", "key3")
        mock_redis.delete.assert_called_once_with("key1", "key2", "key3")

    def test_no_keys_skips_call(
        self, transport: RedisSyncTransport, mock_redis: MagicMock
    ) -> None:
        transport.delete()
        mock_redis.delete.assert_not_called()


class TestRedisSyncTransportImports:
    def test_import_from_transport_subpackage(self) -> None:
        from sqlmodel_cache.transport import RedisSyncTransport as T

        assert T is RedisSyncTransport

    def test_protocols_importable(self) -> None:
        from sqlmodel_cache.transport._protocols import CacheTransport  # noqa: F401

    def test_protocols_has_correct_methods(self) -> None:
        from sqlmodel_cache.transport._protocols import CacheTransport

        # Protocol members verify the interface contract
        assert hasattr(CacheTransport, "get")
        assert hasattr(CacheTransport, "set")
        assert hasattr(CacheTransport, "delete")
