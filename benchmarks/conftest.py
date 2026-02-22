"""Shared fixtures and models for sqlmodel-cache benchmarks.

Models are defined here once so that all benchmark files share the same
SQLAlchemy mapper registry and avoid duplicate-table-name errors when
several benchmark modules are collected in a single pytest session.

Engine scope is ``session`` — one SQLite in-memory DB for the full run.
The DB is populated with one row per model during fixture setup so that
every benchmark can call ``session.get()`` on a pre-existing primary key.
"""
from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from sqlmodel import Field, Session, SQLModel, create_engine

from sqlmodel_cache import CacheConfig

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# Used by bench_overhead.py and bench_redis.py (read benchmarks).
# Two variants: one cache-enabled, one plain, so we can benchmark
# with-cache vs no-cache on the same DB engine.


class BmCachedHero(SQLModel, table=True):
    """Cache-enabled model for read benchmarks."""

    __tablename__ = "bm_cached_hero"
    __cache_config__ = CacheConfig(ttl=60)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    power: int = 100


class BmPlainHero(SQLModel, table=True):
    """Cache-disabled model for baseline read benchmarks (no __cache_config__)."""

    __tablename__ = "bm_plain_hero"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    power: int = 100


# Used by bench_invalidation.py (write benchmarks).
# Separate table so it doesn't interfere with read-benchmark row counts.


class BmWriteHero(SQLModel, table=True):
    """Cache-enabled model for write / invalidation benchmarks."""

    __tablename__ = "bm_write_hero"
    __cache_config__ = CacheConfig(ttl=60)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    power: int = 100


class BmWritePlainHero(SQLModel, table=True):
    """Plain model for write baseline benchmarks (no __cache_config__)."""

    __tablename__ = "bm_write_plain_hero"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    power: int = 100


# ---------------------------------------------------------------------------
# Engine — session-scoped (one in-memory DB for the full benchmark run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def bm_engine() -> Generator[Any, None, None]:
    """SQLite in-memory engine with all benchmark tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Pre-inserted rows — session-scoped (inserted once, read many times)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cached_hero_id(bm_engine: Any) -> int:
    """Insert a BmCachedHero row once; return its primary key."""
    with Session(bm_engine) as session:
        hero = BmCachedHero(name="Alice", power=9000)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


@pytest.fixture(scope="session")
def plain_hero_id(bm_engine: Any) -> int:
    """Insert a BmPlainHero row once; return its primary key."""
    with Session(bm_engine) as session:
        hero = BmPlainHero(name="Bob", power=8000)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


@pytest.fixture(scope="session")
def write_hero_id(bm_engine: Any) -> int:
    """Insert a BmWriteHero row once; used as seed for update benchmarks."""
    with Session(bm_engine) as session:
        hero = BmWriteHero(name="Carol", power=7500)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


# ---------------------------------------------------------------------------
# Redis URL — function-scoped (new container or env-var URL per test)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_url() -> Generator[str, None, None]:
    """Yield a Redis connection URL.

    In CI the ``REDIS_URL`` environment variable is set by the GitHub Actions
    ``services: redis:`` sidecar.  Locally, a throwaway container is started
    via testcontainers.

    Scope is ``session`` — one Redis instance shared across all Redis
    benchmark functions.  Each benchmark clears (FLUSHDB) its own keys.
    """
    url = os.environ.get("REDIS_URL")
    if url:
        yield url
        return

    from testcontainers.redis import RedisContainer

    with RedisContainer() as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}"
