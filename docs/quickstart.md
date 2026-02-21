# Quickstart

Get a working cached `session.get()` in under 5 minutes.

## Prerequisites

- Python 3.11 or later
- A running Redis instance (`redis://localhost:6379`)

## Installation

```bash
pip install sqlmodel-cache
```

For async support:

```bash
pip install "sqlmodel-cache[async]"
```

---

## Sync Quickstart

### 1. Configure at startup

Call `SQLModelCache.configure()` **once** before handling any requests.

```python
import redis
from sqlmodel_cache import SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

SQLModelCache.configure(
    transport=RedisSyncTransport(redis.Redis.from_url("redis://localhost:6379")),
    default_ttl=300,  # 5-minute default TTL
)
```

### 2. Declare a cached model

Add `__cache_config__` to any `SQLModel` table class:

```python
from sqlmodel import Field, SQLModel
from sqlmodel_cache import CacheConfig

class Hero(SQLModel, table=True):
    __cache_config__ = CacheConfig(ttl=600)  # 10-minute TTL for this model
    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
```

### 3. Use `session.get()` — caching is transparent

```python
from sqlmodel import Session, create_engine

engine = create_engine("sqlite:///heroes.db")
SQLModel.metadata.create_all(engine)

# First call — cache miss: queries DB, populates Redis
with Session(engine) as session:
    hero = session.get(Hero, 1)

# Second call — cache hit: no SQL issued, result from Redis
with Session(engine) as session:
    hero = session.get(Hero, 1)
```

### 4. Automatic invalidation on commit

When you modify and commit a cached model row, its cache key is automatically
invalidated so the next `session.get()` fetches a fresh value:

```python
with Session(engine) as session:
    hero = session.get(Hero, 1)
    hero.name = "Updated Name"
    session.commit()  # cache key invalidated automatically

with Session(engine) as session:
    hero = session.get(Hero, 1)  # cache miss → fresh DB read
```

---

## Async Quickstart (FastAPI)

Use the async transport with a FastAPI lifespan context manager:

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

Async `session.get()` calls work identically:

```python
from sqlmodel.ext.asyncio.session import AsyncSession

async with AsyncSession(async_engine) as session:
    hero = await session.get(Hero, 1)  # cached transparently
```

---

## Per-call Control

Override caching for a single call:

```python
with Session(engine) as session:
    # Bypass cache for this call only
    hero = session.get(Hero, 1, execution_options={"cache": False})

    # Use a custom TTL for this call only
    hero = session.get(Hero, 1, execution_options={"cache_ttl": 60})
```

---

!!! warning "Caching scope"
    Only `session.get(Model, pk)` calls are intercepted and cached.
    Queries using `session.exec(select(Model))` pass through to the
    database as normal.

---

## Next Steps

- [API Reference](api-reference.md) — full class and method documentation
- [Migration Guide](migration.md) — adding caching to an existing SQLModel app
- [Advanced](advanced.md) — async patterns, per-call bypass, test isolation
