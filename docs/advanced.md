# Advanced Usage

## Per-call Cache Bypass

Disable caching for a single `session.get()` without touching model configuration:

```python
with Session(engine) as session:
    # Always queries the database — never reads or writes the cache
    hero = session.get(Hero, 1, execution_options={"cache": False})
```

Useful for admin reads, consistency-critical paths, or debugging.

---

## Per-call TTL Override

Override the model's TTL for a single call:

```python
with Session(engine) as session:
    # Cache this result for 60 seconds regardless of CacheConfig.ttl
    hero = session.get(Hero, 1, execution_options={"cache_ttl": 60})
```

Precedence order (highest to lowest):

1. `execution_options(cache_ttl=N)` — per-call
2. `CacheConfig(ttl=N)` — per-model
3. `SQLModelCache.configure(default_ttl=N)` — global

---

## Async Sessions

Use `RedisAsyncTransport` with `AsyncSession` in async frameworks (FastAPI, Litestar…):

```python
import redis.asyncio
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisAsyncTransport

# Configure at startup (e.g. FastAPI lifespan)
r = redis.asyncio.Redis.from_url("redis://localhost:6379")
SQLModelCache.configure(transport=RedisAsyncTransport(r), default_ttl=300)

# Async session.get() is cached transparently
from sqlmodel.ext.asyncio.session import AsyncSession

async with AsyncSession(async_engine) as session:
    hero = await session.get(Hero, 1)  # cache miss → DB + populate cache
    hero = await session.get(Hero, 1)  # cache hit → Redis only
```

---

## Sync + Async Session Coexistence

You can configure both sync and async sessions with a single transport. They share
the same Redis keyspace so a sync commit automatically invalidates keys visible
to async sessions:

```python
import redis.asyncio
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisAsyncTransport

r = redis.asyncio.Redis.from_url("redis://localhost:6379")
SQLModelCache.configure(transport=RedisAsyncTransport(r), default_ttl=300)

# Sync write → cache invalidated → async session sees fresh value
with Session(engine) as session:
    hero = session.get(Hero, 1)
    hero.name = "New Name"
    session.commit()  # invalidates the cache key

async with AsyncSession(async_engine) as session:
    hero = await session.get(Hero, 1)  # cache miss → fresh DB read
```

!!! note "Single transport instance"
    `SQLModelCache.configure()` accepts one transport. If your app mixes sync and
    async sessions, use `RedisAsyncTransport` — the library's greenlet bridge
    makes it work for both session types.

---

## Test Isolation

Prevent cache state bleeding between tests with an `autouse` fixture:

```python
# tests/conftest.py
import pytest
from sqlmodel_cache import SQLModelCache

@pytest.fixture(autouse=True)
def reset_cache():
    yield
    SQLModelCache.reset()
```

For full integration tests against a real Redis instance:

```python
import os
import pytest
import redis
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

@pytest.fixture(scope="session")
def redis_url():
    if url := os.environ.get("REDIS_URL"):
        yield url
        return
    from testcontainers.redis import RedisContainer
    with RedisContainer() as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}"

@pytest.fixture
def configured_cache(redis_url):
    SQLModelCache.configure(
        transport=RedisSyncTransport(redis.Redis.from_url(redis_url)),
        default_ttl=60,
    )
    yield
    SQLModelCache.reset()
```

---

## Custom Transport

Implement the `CacheTransport` protocol to use any backend (Memcached, DynamoDB, in-memory…):

```python
from sqlmodel_cache.transport import CacheTransport

class InMemoryTransport:
    """Simple dict-backed transport for testing."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def set(self, key: str, value: bytes, ttl: int) -> None:
        self._store[key] = value  # TTL not enforced in this example

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

# Use it:
SQLModelCache.configure(transport=InMemoryTransport(), default_ttl=300)
```

No inheritance required — structural subtyping (Protocol) is used.

---

## Disabling the Cache Globally

Set `enabled=False` to make the library a no-op without removing configuration:

```python
SQLModelCache.configure(
    transport=RedisSyncTransport(redis.Redis.from_url("redis://localhost:6379")),
    default_ttl=300,
    enabled=False,  # all session.get() calls pass through to DB
)
```

Useful for feature flags or gradual rollouts.

---

## Cache Key Format

Keys follow this deterministic format:

```
sqlmodelcache:{ModelClassName}:{field1}={value1}:{field2}={value2}
```

- Fields are sorted **alphabetically** for composite PKs (reproducible regardless of dict insertion order)
- `ModelClassName` preserves the Python class name casing
- Override the prefix via `SQLModelCache.configure(key_prefix="myapp")`

Example for `Hero` with `id=42`:
```
sqlmodelcache:Hero:id=42
```

Example for a composite PK model `OrderItem(order_id=1, product_id=5)`:
```
sqlmodelcache:OrderItem:order_id=1:product_id=5
```

## Test Isolation

```python
@pytest.fixture(autouse=True)
def reset_cache():
    yield
    SQLModelCache.reset()
```
