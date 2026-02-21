# sqlmodel-cache

Transparent Redis caching for SQLModel — zero call-site changes required.

## Overview

`sqlmodel-cache` adds automatic Redis caching to `session.get()` calls on SQLModel models.
Declare caching intent on a model class; the library handles the rest.

> **Status:** Alpha — under active development. API subject to change before 1.0.

## Installation

```bash
pip install sqlmodel-cache
```

For async support:

```bash
pip install "sqlmodel-cache[async]"
```

## Quick Example

```python
from sqlmodel_cache import CacheConfig, SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport
import redis

# Configure once at startup
SQLModelCache.configure(
    transport=RedisSyncTransport(redis.Redis.from_url("redis://localhost:6379")),
    default_ttl=300,
)

# Declare caching on your model
class Hero(SQLModel, table=True):
    __cache_config__ = CacheConfig(ttl=300)
    id: int | None = Field(default=None, primary_key=True)
    name: str

# session.get() automatically reads from / writes to Redis
with Session(engine) as session:
    hero = session.get(Hero, 1)  # cache miss → DB + populate Redis
    hero = session.get(Hero, 1)  # cache hit → Redis only, no DB query
```

See the [Quickstart guide](quickstart.md) for full setup instructions.
