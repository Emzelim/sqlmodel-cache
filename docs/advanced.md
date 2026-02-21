# Advanced Usage

> Full advanced topics will be written in Story 6.5.

## Per-call Cache Bypass

```python
hero = session.get(Hero, 1, execution_options={"cache": False})
```

## Per-call TTL Override

```python
hero = session.get(Hero, 1, execution_options={"cache_ttl": 60})
```

## Async Sessions

```python
from sqlmodel_cache.transport import RedisAsyncTransport
import redis.asyncio

SQLModelCache.configure(
    transport=RedisAsyncTransport(redis.asyncio.Redis.from_url("redis://localhost:6379")),
)

async with AsyncSession(engine) as session:
    hero = await session.get(Hero, 1)
```

## Test Isolation

```python
@pytest.fixture(autouse=True)
def reset_cache():
    yield
    SQLModelCache.reset()
```
