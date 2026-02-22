"""Real-Redis benchmarks — requires Docker or REDIS_URL env var.

These benchmarks repeat the same scenarios as ``bench_overhead.py`` but with
a real Redis instance as the transport.  They show the **actual production
speedup** including network latency (loopback for local Docker, real network
for a remote Redis).

Scenarios
---------
redis_no_cache
    session.get() with no cache — SQLite baseline (same as bench_overhead).

redis_cache_hit
    session.get() with warm Redis cache.  DB is bypassed; the round-trip
    is a single Redis GET on loopback.  This is the sweet spot that makes
    caching worthwhile when the DB is slower than Redis (e.g. remote Postgres).

redis_cache_miss
    session.get() cold miss — Redis GET returns None, DB is queried, result
    serialised and written to Redis.  Total cost = Redis GET + DB SELECT +
    serialise + Redis SET.

All tests are marked ``redis_bench`` so they can be selected or deselected:

    hatch run benchmark:run -m redis_bench      # Redis benchmarks only
    hatch run benchmark:run -m "not redis_bench"  # skip Redis benchmarks

Run
---
    hatch run benchmark:run-redis
    # or
    REDIS_URL=redis://localhost:6379 hatch run benchmark:run -m redis_bench
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
    bm_engine: Any,
    plain_hero_id: int,
) -> None:
    """SQLite baseline — no cache configured.  Control group for Redis suite."""
    SQLModelCache.reset()

    def call() -> Any:
        with Session(bm_engine) as session:
            return session.get(BmPlainHero, plain_hero_id)

    benchmark(call)


# ---------------------------------------------------------------------------
# Benchmark: cache hit — warm Redis
# ---------------------------------------------------------------------------


def test_bench_redis_cache_hit(
    benchmark: Any,
    bm_engine: Any,
    cached_hero_id: int,
    redis_url: str,
) -> None:
    """Cache hit with real Redis.

    One warm-up call populates the key in Redis before the benchmark loop
    starts.  All measured calls return from Redis without touching SQLite.
    """
    client = _redis.Redis.from_url(redis_url)
    transport = RedisSyncTransport(client)
    SQLModelCache.configure(transport=transport, default_ttl=60)

    # Warm the cache
    with Session(bm_engine) as session:
        session.get(BmCachedHero, cached_hero_id)

    def call() -> Any:
        with Session(bm_engine) as session:
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
    bm_engine: Any,
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
        with Session(bm_engine) as session:
            return session.get(BmCachedHero, cached_hero_id)

    try:
        benchmark.pedantic(call, setup=setup, rounds=100, warmup_rounds=3)
    finally:
        SQLModelCache.reset()
        client.flushdb()
        client.close()
