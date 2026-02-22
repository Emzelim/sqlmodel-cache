"""Real-Redis benchmarks — requires Docker or REDIS_URL env var.

Each benchmark is run against three DB backends (sqlite-memory, sqlite-file,
postgresql) via the parametrized ``db_engine`` fixture, so you get results
like:

    test_bench_redis_cache_hit[sqlite-memory]
    test_bench_redis_cache_hit[sqlite-file]
    test_bench_redis_cache_hit[postgresql]

This lets you separate DB latency from Redis latency in the results:
* With SQLite in-memory, the DB portion is near-zero — pure Redis overhead.
* With SQLite file, you see real disk I/O for miss paths.
* With PostgreSQL, the miss path includes a real network DB round-trip.

All tests are marked ``redis_bench`` (requires Docker or REDIS_URL).
PostgreSQL tests are additionally marked ``postgresql_bench``.

Run
---
    # Redis + SQLite only (no PostgreSQL Docker needed)
    pytest benchmarks/bench_redis.py -m 'redis_bench and not postgresql_bench'

    # Full suite (Redis + all 3 DB backends)
    pytest benchmarks/bench_redis.py -m redis_bench

    # Explicit Redis URL
    REDIS_URL=redis://localhost:6379 pytest benchmarks/bench_redis.py -m redis_bench
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import redis as _redis
from sqlmodel import Session

from benchmarks.conftest import BmCachedHero, BmPlainHero
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

pytestmark = pytest.mark.redis_bench

# ---------------------------------------------------------------------------
# Benchmark: no cache — SQLite baseline (control group for Redis suite)
# ---------------------------------------------------------------------------


def test_bench_redis_no_cache(
    benchmark: Any,
    db_engine: Any,
    plain_hero_id: int,
) -> None:
    """DB baseline — no cache configured.  Control group for Redis suite."""
    SQLModelCache.reset()

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmPlainHero, plain_hero_id)

    benchmark(call)


# ---------------------------------------------------------------------------
# Benchmark: cache hit — warm Redis
# ---------------------------------------------------------------------------


def test_bench_redis_cache_hit(
    benchmark: Any,
    db_engine: Any,
    cached_hero_id: int,
    redis_url: str,
) -> None:
    """Cache hit with real Redis.

    One warm-up call populates the key in Redis before the benchmark loop
    starts.  All measured calls return from Redis without touching the DB.
    """
    client = _redis.Redis.from_url(redis_url)
    transport = RedisSyncTransport(client)
    SQLModelCache.configure(transport=transport, default_ttl=60)

    # Warm the cache
    with Session(db_engine) as session:
        session.get(BmCachedHero, cached_hero_id)

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmCachedHero, cached_hero_id)

    try:
        benchmark(call)
    finally:
        SQLModelCache.reset()
        client.flushdb()
        client.close()


# ---------------------------------------------------------------------------
# Benchmark: cache miss — cold Redis
# ---------------------------------------------------------------------------


def test_bench_redis_cache_miss(
    benchmark: Any,
    db_engine: Any,
    cached_hero_id: int,
    redis_url: str,
) -> None:
    """Cache miss with real Redis.

    Redis is flushed between every invocation so each measured call incurs
    a full miss: Redis GET → None → DB SELECT → serialise → Redis SET.
    """
    client = _redis.Redis.from_url(redis_url)
    transport = RedisSyncTransport(client)
    SQLModelCache.configure(transport=transport, default_ttl=60)

    def setup() -> None:
        client.flushdb()

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmCachedHero, cached_hero_id)

    try:
        benchmark.pedantic(call, setup=setup, rounds=100, warmup_rounds=3)
    finally:
        SQLModelCache.reset()
        client.flushdb()
        client.close()
