"""Overhead benchmarks — FakeTransport (no Redis required).

Each benchmark is run against three DB backends via the parametrized
``db_engine`` fixture defined in ``conftest.py``:

    test_bench_cache_hit[sqlite-memory]
    test_bench_cache_hit[sqlite-file]
    test_bench_cache_hit[postgresql]   ← skipped if Docker unavailable

Scenarios
---------
no_cache
    session.get() with no cache configured at all.  Pure DB + SQLModel baseline.

cache_hit
    session.get() when the key is already warm in the cache.
    Measures: Python interceptor path + FakeTransport dict lookup.

cache_miss
    session.get() on a cold cache.  DB queried, result serialised and written
    to the FakeTransport.  Measures: interceptor + DB read + serialise + write.

passthrough_disabled
    session.get() with SQLModelCache configured but ``enabled=False``.
    Verifies that the disabled guard adds negligible overhead vs no_cache.

Run
---
    pytest benchmarks/bench_overhead.py -m 'not postgresql_bench' --benchmark-sort=mean
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

# Allow importing FakeTransport from tests/unit/fakes.py without installing it.
sys.path.insert(0, str(Path(__file__).parent.parent / "tests" / "unit"))
from fakes import FakeTransport  # type: ignore[import]

from benchmarks.conftest import BmCachedHero, BmPlainHero
from sqlmodel_cache import SQLModelCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport() -> FakeTransport:
    return FakeTransport()


# ---------------------------------------------------------------------------
# Benchmark: no cache (pure DB baseline)
# ---------------------------------------------------------------------------


def test_bench_no_cache(
    benchmark: Any,
    db_engine: Any,
    plain_hero_id: int,
) -> None:
    """Baseline: session.get() with no cache configured.

    No SQLModelCache.configure() call — raw DB + SQLModel ORM cost.
    """
    SQLModelCache.reset()  # ensure clean state

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmPlainHero, plain_hero_id)

    benchmark(call)


# ---------------------------------------------------------------------------
# Benchmark: cache hit (warm FakeTransport)
# ---------------------------------------------------------------------------


def test_bench_cache_hit(
    benchmark: Any,
    db_engine: Any,
    cached_hero_id: int,
) -> None:
    """Cache hit: session.get() when the key is already in the transport.

    The first call outside the benchmark loop warms the cache.  All
    subsequent benchmark calls hit the in-memory dict and skip the DB.
    """
    transport = _make_transport()
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


# ---------------------------------------------------------------------------
# Benchmark: cache miss (cold FakeTransport, cleared between rounds)
# ---------------------------------------------------------------------------


def test_bench_cache_miss(
    benchmark: Any,
    db_engine: Any,
    cached_hero_id: int,
) -> None:
    """Cache miss: DB is queried and result is written to the transport.

    The FakeTransport is cleared between every invocation via
    ``benchmark.pedantic(setup=...)``, guaranteeing a cold miss each time.
    """
    transport = _make_transport()
    SQLModelCache.configure(transport=transport, default_ttl=60)

    def setup() -> None:
        transport.clear()

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmCachedHero, cached_hero_id)

    try:
        benchmark.pedantic(call, setup=setup, rounds=200, warmup_rounds=5)
    finally:
        SQLModelCache.reset()


# ---------------------------------------------------------------------------
# Benchmark: cache configured but globally disabled
# ---------------------------------------------------------------------------


def test_bench_passthrough_disabled(
    benchmark: Any,
    db_engine: Any,
    cached_hero_id: int,
) -> None:
    """Disabled cache: SQLModelCache.configure(enabled=False).

    The event listener fires but immediately returns without touching the
    transport.  Overhead should be very close to the no_cache baseline.
    """
    transport = _make_transport()
    SQLModelCache.configure(transport=transport, default_ttl=60, enabled=False)

    def call() -> Any:
        with Session(db_engine) as session:
            return session.get(BmCachedHero, cached_hero_id)

    try:
        benchmark(call)
    finally:
        SQLModelCache.reset()
