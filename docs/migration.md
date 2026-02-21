# Migration Guide

Adding `sqlmodel-cache` to an existing SQLModel application is designed to be
non-breaking and incremental. Each change below is independent — you can apply
them one at a time and verify your app still works before moving on.

## Prerequisites

- A working SQLModel application that uses `session.get()` for primary-key lookups
- A running Redis instance (local or remote)
- `sqlmodel-cache` installed: `pip install sqlmodel-cache`

---

## Step 1: Install the Library

```bash
pip install sqlmodel-cache
# For async FastAPI apps:
pip install "sqlmodel-cache[async]"
```

---

## Step 2: Configure at Startup

You need to call `SQLModelCache.configure()` **once** before any request is
handled. The location of this call depends on your framework.

### Plain script

**Before:**

```python
# main.py — plain SQLModel, no cache
from sqlmodel import create_engine

engine = create_engine("postgresql+psycopg://user:pass@localhost/mydb")
```

**After:**

```python
# main.py — with sqlmodel-cache
import redis
from sqlmodel import create_engine
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

engine = create_engine("postgresql+psycopg://user:pass@localhost/mydb")

SQLModelCache.configure(
    transport=RedisSyncTransport(redis.Redis.from_url("redis://localhost:6379")),
    default_ttl=300,  # 5 minutes
)
```

### FastAPI (sync transport)

```python
import redis
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

@asynccontextmanager
async def lifespan(app: FastAPI):
    r = redis.Redis.from_url("redis://localhost:6379")
    SQLModelCache.configure(
        transport=RedisSyncTransport(r),
        default_ttl=300,
    )
    yield
    r.close()

app = FastAPI(lifespan=lifespan)
```

### FastAPI (async transport)

```python
import redis.asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisAsyncTransport

@asynccontextmanager
async def lifespan(app: FastAPI):
    r = redis.asyncio.Redis.from_url("redis://localhost:6379")
    SQLModelCache.configure(
        transport=RedisAsyncTransport(r),
        default_ttl=300,
    )
    yield
    await r.aclose()

app = FastAPI(lifespan=lifespan)
```

---

## Step 3: Opt Models In to Caching

Add `__cache_config__` to any model whose `session.get()` lookups should be
cached. Models without `__cache_config__` are never cached.

**Before:**

```python
from sqlmodel import Field, SQLModel

class Hero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
```

**After:**

```python
from sqlmodel import Field, SQLModel
from sqlmodel_cache import CacheConfig

class Hero(SQLModel, table=True):
    __cache_config__ = CacheConfig(ttl=600)  # 10-minute TTL
    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
```

That's it. All `session.get(Hero, pk)` calls are now transparently cached.
No other code changes are required.

---

## Testing with sqlmodel-cache

Add an `autouse` fixture to your `conftest.py` so each test starts with a clean
configuration and state cannot bleed between tests:

```python
# tests/conftest.py
import pytest
from sqlmodel_cache import SQLModelCache

@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure every test starts with a clean cache configuration."""
    yield
    SQLModelCache.reset()
```

If you use testcontainers for a live Redis instance in integration tests and
also run in CI with a managed Redis service, allow `REDIS_URL` to override:

```python
import os
import pytest
import redis
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

@pytest.fixture(scope="session")
def redis_url():
    # In CI, REDIS_URL is set by the GitHub Actions services block.
    # Locally, fall back to testcontainers.
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

## Selectively Bypassing the Cache

```python
with Session(engine) as session:
    # Always goes to the database; never reads or writes the cache
    hero = session.get(Hero, 1, execution_options={"cache": False})

    # Caches this result for 60 seconds instead of the model's configured TTL
    hero = session.get(Hero, 1, execution_options={"cache_ttl": 60})
```

---

## What's Cached, What Isn't

| Operation | Cached? |
|---|---|
| `session.get(Hero, 1)` | ✅ Yes |
| `session.exec(select(Hero))` | ❌ No (pass-through to DB) |
| `session.exec(select(Hero).where(...))` | ❌ No (pass-through to DB) |
| INSERT / UPDATE / DELETE via `session.commit()` | ❌ Never (auto-invalidates affected cache keys) |

!!! warning "Only `session.get()` is cached"
    `sqlmodel-cache` intercepts only primary-key lookups via `session.get()`.
    All other queries pass through to the database unchanged.

---

## Troubleshooting

**`ConfigurationError: SQLModelCache is not configured`**
→ You forgot to call `SQLModelCache.configure()`. Add it to your application's
startup code or FastAPI lifespan before handling any requests.

**`ConfigurationError: SQLModelCache is already configured`**
→ `configure()` was called twice without a `reset()` in between.
In tests, add the `autouse` reset fixture shown above. In production code,
guard with a flag or call `reset()` before reconfiguring.

**Cached values not updating after a write**
→ Confirm the model has `__cache_config__` set. Without it, the after-commit
invalidation hook ignores rows of that model entirely and no cache keys are
ever written or evicted. Add `__cache_config__ = CacheConfig()` to enable
full lifecycle management.
