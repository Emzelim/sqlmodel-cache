# sqlmodel-cache — Benchmarks

Performance benchmarks for `sqlmodel-cache` using
[pytest-benchmark](https://pytest-benchmark.readthedocs.io/).

## Latest results

| Date | Redis | PostgreSQL | SQLite :memory: | SQLite file | Report |
|---|---|---|---|---|---|
| 2026-02-23 | Docker `redis:7-alpine` | Docker `postgres:16-alpine` | ✅ | ✅ | [results/2026-02-23.md](results/2026-02-23.md) |

**Headline numbers** (2026-02-23, all 33 tests passed):

| Scenario | sqlite-memory | sqlite-file | postgresql (Docker) |
|---|---|---|---|
| `no_cache` (baseline) | 172 µs | 182 µs | 416 µs |
| `cache_hit` (FakeTransport) | **113 µs** (+34%) | **141 µs** (+23%) | **113 µs** (+268%) |
| `redis_cache_hit` (real Redis) | 273 µs | 319 µs | **260 µs** (+38%) |
| `cache_miss` (FakeTransport) | 265 µs | 335 µs | 540 µs |
| `redis_cache_miss` (real Redis) | 784 µs | 563 µs | 1,003 µs |

> With remote PostgreSQL (5 ms RTT), `cache_hit` speedup grows to **~44×** vs `no_cache`.

---

## Architecture

Every benchmark function runs against **three DB backends** via a single
parametrized `db_engine` fixture. Test IDs include the backend name:

```
test_bench_cache_hit[sqlite-memory]
test_bench_cache_hit[sqlite-file]
test_bench_cache_hit[postgresql]
```

| Backend | Engine URL | Docker needed | Notes |
|---|---|---|---|
| `sqlite-memory` | `sqlite:///:memory:` | No | Zero I/O — pure Python overhead |
| `sqlite-file` | `sqlite:///tmp/...` | No | Real disk I/O, no server |
| `postgresql` | `postgresql://...` | Yes (or `POSTGRES_URL`) | Full network stack |

Two transports are tested:

| Transport | File | Docker needed |
|---|---|---|
| `FakeTransport` (in-memory dict) | `bench_overhead.py`, `bench_invalidation.py` | No |
| `RedisSyncTransport` (real Redis) | `bench_redis.py` | Yes (or `REDIS_URL`) |

## Prerequisites

```bash
# Install the benchmark extra into your active virtualenv
pip install -e ".[benchmark]"
# or via hatch (creates a dedicated benchmark env)
hatch env create benchmark
```

## Running benchmarks

### Fastest — SQLite only, no Docker

```bash
pytest benchmarks/ -m "not redis_bench and not postgresql_bench" \
  --benchmark-sort=mean --benchmark-columns=min,mean,max,rounds,ops
```

### SQLite + real Redis (no PostgreSQL Docker)

```bash
# with Docker (testcontainers auto-starts Redis)
pytest benchmarks/ -m "not postgresql_bench" \
  --benchmark-sort=mean --benchmark-columns=min,mean,max,rounds,ops

# with explicit REDIS_URL
REDIS_URL=redis://localhost:6379 pytest benchmarks/ -m "not postgresql_bench"
```

### Full suite — all backends, all transports

Requires Docker (both Redis and PostgreSQL containers) or env vars.

```bash
# with Docker
pytest benchmarks/ --benchmark-sort=mean --benchmark-columns=min,mean,max,rounds,ops

# with explicit URLs
REDIS_URL=redis://localhost:6379 POSTGRES_URL=postgresql://user:pass@localhost/db \
  pytest benchmarks/ --benchmark-sort=mean
```

### Select individual backends or transports

```bash
# PostgreSQL benchmarks only
pytest benchmarks/ -m postgresql_bench --benchmark-sort=mean

# Redis benchmarks only (all 3 DB backends)
pytest benchmarks/ -m redis_bench --benchmark-sort=mean

# Redis + PostgreSQL (full real-world stack)
pytest benchmarks/ -m "redis_bench and postgresql_bench" --benchmark-sort=mean
```

### Via hatch

```bash
hatch run benchmark:run          # SQLite only (no Docker)
hatch run benchmark:run-redis    # SQLite + Redis
hatch run benchmark:run-full     # all backends (requires Docker)
hatch run benchmark:save         # save results to .benchmarks/ JSON
hatch run benchmark:compare      # compare against saved baseline
```

## Benchmark files

### `bench_overhead.py` — Read path (FakeTransport × 3 DB backends)

| Scenario | Description |
|---|---|
| `no_cache` | `session.get()` with no cache — raw DB + ORM baseline |
| `cache_hit` | `session.get()` warm cache — DB bypassed, FakeTransport dict lookup |
| `cache_miss` | `session.get()` cold cache — DB read + serialise + transport write |
| `passthrough_disabled` | `session.get()` with `enabled=False` — listener fires then early-returns |

### `bench_invalidation.py` — Write path (FakeTransport × 3 DB backends)

| Scenario | Description |
|---|---|
| `write_no_cache` | `add(row) + commit` — raw DB write baseline |
| `write_with_cache` | Same write with cache active — listener overhead on insert |
| `update_no_cache` | Fetch + modify + commit — raw update cycle baseline |
| `update_with_invalidation` | Cache hit + modify + commit → stale key deleted |

### `bench_redis.py` — Real Redis × 3 DB backends *(requires Docker / REDIS_URL)*

| Scenario | Description |
|---|---|
| `redis_no_cache` | DB baseline — no cache (control group) |
| `redis_cache_hit` | Warm Redis, DB bypassed — one Redis GET on loopback |
| `redis_cache_miss` | Cold Redis — Redis GET (None) + DB + serialise + Redis SET |

## Interpreting results

**SQLite in-memory** shows pure Python overhead. As the DB gets slower
(SQLite file → local Postgres → remote Postgres), the cache_hit advantage
grows dramatically:

| DB | Typical no_cache latency | cache_hit latency | Speedup |
|---|---|---|---|
| SQLite :memory: | ~170 µs | ~118 µs | ~1.4× |
| SQLite file | ~306 µs | ~142 µs | ~2.2× |
| PostgreSQL local | ~1–5 ms | ~0.3–0.5 ms | ~5–15× |
| PostgreSQL remote | ~5–50 ms | ~0.3–0.5 ms | ~20–100× |

**Key numbers to watch per backend:**
- `cache_hit` vs `no_cache` — net benefit of a warmed cache
- `cache_miss` vs `no_cache` — cold miss cost (should stay < 3×)
- `update_with_invalidation` vs `update_no_cache` — write overhead with key deletion
