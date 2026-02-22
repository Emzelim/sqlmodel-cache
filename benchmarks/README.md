# sqlmodel-cache — Benchmarks

Performance benchmarks for `sqlmodel-cache` using
[pytest-benchmark](https://pytest-benchmark.readthedocs.io/).

## Overview

Three benchmark files, each targeting a different dimension:

| File | Transport | Docker required | What it measures |
|---|---|---|---|
| `bench_overhead.py` | FakeTransport (in-memory dict) | No | Pure Python interceptor overhead |
| `bench_invalidation.py` | FakeTransport (in-memory dict) | No | Write path + after-commit key deletion |
| `bench_redis.py` | Real Redis | Yes (or `REDIS_URL`) | End-to-end latency including Redis network |

## Prerequisites

```bash
# Install the benchmark extra into your active virtualenv
pip install -e ".[benchmark]"
# or via hatch (creates a dedicated benchmark env)
hatch env create benchmark
```

## Running benchmarks

### Fast suite — no Docker, no Redis

FakeTransport only. Measures Python overhead. Always runnable.

```bash
# via hatch (recommended)
hatch run benchmark:run

# or directly
pytest benchmarks/ -m "not redis_bench" \
  --benchmark-sort=mean \
  --benchmark-columns=min,mean,max,rounds,ops
```

### Full suite — includes real Redis

Requires Docker (testcontainers auto-starts a Redis container) or a running
Redis instance pointed to via `REDIS_URL`.

```bash
# via hatch
hatch run benchmark:run-redis

# or with explicit Redis URL
REDIS_URL=redis://localhost:6379 pytest benchmarks/ \
  --benchmark-sort=mean \
  --benchmark-columns=min,mean,max,rounds,ops
```

### Redis benchmarks only

```bash
pytest benchmarks/ -m redis_bench --benchmark-sort=mean
```

## Saving & comparing results

```bash
# Save results to .benchmarks/ (JSON)
hatch run benchmark:save --benchmark-name "main"

# Compare current run against saved baseline
hatch run benchmark:compare
```

## Benchmark scenarios

### `bench_overhead.py`

| Test | Description |
|---|---|
| `test_bench_no_cache` | `session.get()` with no cache configured — pure SQLite + ORM baseline |
| `test_bench_cache_hit` | `session.get()` with a warm FakeTransport — DB bypassed entirely |
| `test_bench_cache_miss` | `session.get()` cold miss — DB read + serialise + FakeTransport write |
| `test_bench_passthrough_disabled` | `session.get()` with `enabled=False` — event listener fires then returns early |

### `bench_invalidation.py`

| Test | Description |
|---|---|
| `test_bench_write_no_cache` | `session.add(row) + commit` — baseline write path |
| `test_bench_write_with_cache` | Same write with SQLModelCache active — measures listener overhead on insert |
| `test_bench_update_no_cache` | Fetch + modify attribute + commit — baseline update cycle |
| `test_bench_update_with_invalidation` | Fetch (cache hit) + modify + commit → key deleted from transport |

### `bench_redis.py` *(requires Docker or `REDIS_URL`)*

| Test | Description |
|---|---|
| `test_bench_redis_no_cache` | SQLite baseline (control group, no cache) |
| `test_bench_redis_cache_hit` | `session.get()` with warm Redis — DB bypassed, one Redis GET on loopback |
| `test_bench_redis_cache_miss` | Cold miss — Redis GET (None) + DB + serialise + Redis SET |

## Interpreting results

**SQLite in-memory** results show pure Python overhead. With a real remote
database (e.g. Postgres on a separate host with a 1–5 ms round-trip), the
**cache_hit** scenario will be **5–20× faster** than no_cache because Redis on
loopback (~0.1 ms) beats a remote DB round-trip by a large margin.

Key numbers to watch:

- **`cache_hit` vs `no_cache`**: Net benefit of a warmed cache.
- **`cache_miss` vs `no_cache`**: Cost of a cold miss — should remain within
  ~2× to avoid pathological write-heavy workloads.
- **`passthrough_disabled` vs `no_cache`**: Overhead of having the event
  listener registered even when the library is disabled.
- **`update_with_invalidation` vs `update_no_cache`**: Full write-path cost
  including key deletion.
