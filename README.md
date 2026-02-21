# sqlmodel-cache

[![CI](https://github.com/emzelim/sqlmodel-cache/actions/workflows/ci.yml/badge.svg)](https://github.com/emzelim/sqlmodel-cache/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sqlmodel-cache)](https://pypi.org/project/sqlmodel-cache/)

Transparent Redis caching for SQLModel — cache `session.get()` lookups with a single class attribute.

## Installation

```bash
pip install sqlmodel-cache
# With async support:
pip install "sqlmodel-cache[async]"
```

## Quick Example

```python
import redis
from sqlmodel import Field, Session, SQLModel, create_engine
from sqlmodel_cache import CacheConfig, SQLModelCache
from sqlmodel_cache.transport import RedisSyncTransport

SQLModelCache.configure(
    transport=RedisSyncTransport(redis.Redis.from_url("redis://localhost:6379")),
    default_ttl=300,
)

class Hero(SQLModel, table=True):
    __cache_config__ = CacheConfig(ttl=600)
    id: int | None = Field(default=None, primary_key=True)
    name: str = ""

engine = create_engine("sqlite:///heroes.db")
SQLModel.metadata.create_all(engine)

with Session(engine) as session:
    hero = session.get(Hero, 1)  # cache miss → queries DB
with Session(engine) as session:
    hero = session.get(Hero, 1)  # cache hit → returns from Redis
```

## Documentation

Full documentation is available at [emzelim.github.io/sqlmodel-cache](https://emzelim.github.io/sqlmodel-cache).

## License

MIT
