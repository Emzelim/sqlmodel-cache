"""Write-path and invalidation benchmarks — FakeTransport.

Each benchmark is run against three DB backends via the parametrized
``db_engine`` fixture defined in ``conftest.py``:

    test_bench_write_no_cache[sqlite-memory]
    test_bench_write_no_cache[sqlite-file]
    test_bench_write_no_cache[postgresql]   ← skipped if Docker unavailable

Scenarios
---------
write_no_cache
    session.add(new_row) + session.commit().  Pure DB write baseline.

write_with_cache
    Same write but with SQLModelCache active.  after_flush and after_commit
    listeners fire; since this is a new insert (no cached key to delete)
    the transport delete call is effectively a no-op.

update_no_cache
    Fetch a row, modify an attribute, commit.  Baseline for the update cycle.

update_with_invalidation
    Fetch a row (populates cache), modify an attribute, commit.
    The after_commit handler deletes the cached key from the transport.
    Measures the full read-modify-write + invalidation path.

Run
---
    pytest benchmarks/bench_invalidation.py -m 'not postgresql_bench' --benchmark-sort=mean
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Field, Session, SQLModel

sys.path.insert(0, str(Path(__file__).parent.parent / "tests" / "unit"))
from fakes import FakeTransport  # type: ignore[import]

from benchmarks.conftest import BmWriteHero, BmWritePlainHero
from sqlmodel_cache import SQLModelCache

# Counter used to generate unique names for each inserted row.
_counter = itertools.count(1)

# ---------------------------------------------------------------------------
# Benchmark: write new row — no cache
# ---------------------------------------------------------------------------


def test_bench_write_no_cache(
    benchmark: Any,
    db_engine: Any,
) -> None:
    """Baseline: add new row + commit with no cache configured."""
    SQLModelCache.reset()

    def call() -> None:
        hero = BmWritePlainHero(name=f"hero_{next(_counter)}", power=100)
        with Session(db_engine) as session:
            session.add(hero)
            session.commit()

    benchmark(call)


# ---------------------------------------------------------------------------
# Benchmark: write new row — cache active (no invalidation, new key)
# ---------------------------------------------------------------------------


def test_bench_write_with_cache(
    benchmark: Any,
    db_engine: Any,
) -> None:
    """Write overhead: add new row + commit with SQLModelCache active.

    New inserts don't have a cached key to delete, but the after_flush and
    after_commit event listeners still fire and search for dirty instances.
    Measures listener overhead on the write path.
    """
    transport = FakeTransport()
    SQLModelCache.configure(transport=transport, default_ttl=60)

    def call() -> None:
        hero = BmWriteHero(name=f"hero_{next(_counter)}", power=100)
        with Session(db_engine) as session:
            session.add(hero)
            session.commit()

    try:
        benchmark(call)
    finally:
        SQLModelCache.reset()
        transport.clear()


# ---------------------------------------------------------------------------
# Benchmark: update + commit — no cache
# ---------------------------------------------------------------------------


def test_bench_update_no_cache(
    benchmark: Any,
    db_engine: Any,
    write_hero_id: int,
) -> None:
    """Baseline: fetch + modify + commit with no cache configured."""
    SQLModelCache.reset()

    power_gen = itertools.count(200)

    def call() -> None:
        with Session(db_engine) as session:
            hero = session.get(BmWritePlainHero, write_hero_id)
            if hero:
                hero.power = next(power_gen)
            session.commit()

    benchmark(call)


# ---------------------------------------------------------------------------
# Benchmark: update + commit — cache hit then invalidation
# ---------------------------------------------------------------------------


def test_bench_update_with_invalidation(
    benchmark: Any,
    db_engine: Any,
    write_hero_id: int,
) -> None:
    """Full read-modify-write with cache invalidation.

    Setup (before each round):
      1. Populate the cache via session.get() so the key exists.

    Measured call:
      2. Fetch (cache hit — returns cached value without DB round-trip).
      3. Modify an attribute.
      4. Commit — triggers after_flush (collect dirty) + after_commit
         (delete stale key from transport).
    """
    transport = FakeTransport()
    SQLModelCache.configure(transport=transport, default_ttl=60)

    power_gen = itertools.count(300)

    def setup() -> None:
        """Warm the cache so the key exists before each measured call."""
        transport.clear()
        with Session(db_engine) as session:
            session.get(BmWriteHero, write_hero_id)

    def call() -> None:
        with Session(db_engine) as session:
            hero = session.get(BmWriteHero, write_hero_id)
            if hero:
                hero.power = next(power_gen)
            session.commit()

    try:
        benchmark.pedantic(call, setup=setup, rounds=200, warmup_rounds=5)
    finally:
        SQLModelCache.reset()
        transport.clear()
