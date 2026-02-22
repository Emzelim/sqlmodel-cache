"""Shared fixtures and models for sqlmodel-cache benchmarks.

DB backends
-----------
Each benchmark function is run against **three database backends** via a
session-scoped parametrized ``db_engine`` fixture:

* ``sqlite-memory`` — SQLite in-memory (zero I/O, pure overhead)
* ``sqlite-file``   — SQLite on-disk temp file (real I/O, no server)
* ``postgresql``    — PostgreSQL via testcontainers or ``POSTGRES_URL``
                      (auto-skipped if Docker is unavailable)

Benchmark test IDs include the backend name, e.g.:
    test_bench_cache_hit[sqlite-memory]
    test_bench_cache_hit[sqlite-file]
    test_bench_cache_hit[postgresql]
"""
from __future__ import annotations

import os
import tempfile
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
# PostgreSQL URL helper — returns None instead of raising if Docker is absent
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _pg_url() -> Generator[str | None, None, None]:
    """Return a PostgreSQL DSN, or None if Docker/POSTGRES_URL is unavailable.

    Does NOT call pytest.skip() — that decision belongs to ``db_engine``
    so that only the postgresql-parametrized variant is skipped.
    """
    url = os.environ.get("POSTGRES_URL")
    if url:
        yield url
        return

    try:
        from testcontainers.postgres import PostgresContainer

        with PostgresContainer("postgres:16-alpine") as container:
            yield container.get_connection_url()
    except Exception:
        yield None


# ---------------------------------------------------------------------------
# SQLite file path — session-scoped temp file
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _sqlite_file_path() -> Generator[str, None, None]:
    """Provide a temp-file path for the SQLite-file backend.

    Uses ``tempfile`` instead of ``tmp_path`` because ``tmp_path`` is
    function-scoped and cannot be used by session-scoped fixtures.
    """
    fd, path = tempfile.mkstemp(suffix="-bm.db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# db_engine — parametrized across sqlite-memory / sqlite-file / postgresql
# ---------------------------------------------------------------------------


@pytest.fixture(
    scope="session",
    params=[
        pytest.param("sqlite-memory", id="sqlite-memory"),
        pytest.param("sqlite-file", id="sqlite-file"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.postgresql_bench,
        ),
    ],
)
def db_engine(
    request: pytest.FixtureRequest,
    _sqlite_file_path: str,
    _pg_url: str | None,
) -> Generator[Any, None, None]:
    """Session-scoped SQLAlchemy engine parametrized across three DB backends.

    Benchmark test IDs automatically include the backend id, e.g.:
        test_bench_cache_hit[sqlite-memory]
        test_bench_cache_hit[sqlite-file]
        test_bench_cache_hit[postgresql]

    The postgresql variant is auto-skipped when Docker is unavailable and
    ``POSTGRES_URL`` is not set in the environment.
    """
    backend: str = request.param

    if backend == "sqlite-memory":
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
    elif backend == "sqlite-file":
        engine = create_engine(
            f"sqlite:///{_sqlite_file_path}",
            connect_args={"check_same_thread": False},
        )
    elif backend == "postgresql":
        if _pg_url is None:
            pytest.skip(
                "PostgreSQL unavailable — start Docker or set POSTGRES_URL"
            )
        engine = create_engine(_pg_url)
    else:
        raise ValueError(f"Unknown backend param: {backend!r}")

    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Pre-inserted seed rows — one set per backend (propagated by parametrize)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cached_hero_id(db_engine: Any) -> int:
    """Insert one BmCachedHero row; return its primary key."""
    with Session(db_engine) as session:
        hero = BmCachedHero(name="Alice", power=9000)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


@pytest.fixture(scope="session")
def plain_hero_id(db_engine: Any) -> int:
    """Insert one BmPlainHero row; return its primary key."""
    with Session(db_engine) as session:
        hero = BmPlainHero(name="Bob", power=8000)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


@pytest.fixture(scope="session")
def write_hero_id(db_engine: Any) -> int:
    """Insert one BmWriteHero row; return its primary key."""
    with Session(db_engine) as session:
        hero = BmWriteHero(name="Carol", power=7500)
        session.add(hero)
        session.commit()
        session.refresh(hero)
        assert hero.id is not None
        return hero.id


# ---------------------------------------------------------------------------
# Redis URL — session-scoped; skips if Docker unavailable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_url() -> Generator[str, None, None]:
    """Yield a Redis connection URL.

    Checks ``REDIS_URL`` env-var first; falls back to testcontainers.
    Skips cleanly when Docker is unavailable and ``REDIS_URL`` is not set.
    """
    url = os.environ.get("REDIS_URL")
    if url:
        yield url
        return

    try:
        from testcontainers.redis import RedisContainer

        with RedisContainer() as container:
            host = container.get_container_host_ip()
            port = container.get_exposed_port(6379)
            yield f"redis://{host}:{port}"
    except Exception as exc:
        pytest.skip(f"Redis unavailable — start Docker or set REDIS_URL: {exc}")
