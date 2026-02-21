# API Reference

## Core

### CacheConfig

::: sqlmodel_cache.CacheConfig
    options:
      show_root_heading: true

### SQLModelCache

::: sqlmodel_cache.SQLModelCache
    options:
      show_root_heading: true

---

## Errors

### CacheError

::: sqlmodel_cache.CacheError
    options:
      show_root_heading: true

### ConfigurationError

::: sqlmodel_cache.ConfigurationError
    options:
      show_root_heading: true

---

## Transports

### RedisSyncTransport

::: sqlmodel_cache.transport.RedisSyncTransport
    options:
      show_root_heading: true

### RedisAsyncTransport

::: sqlmodel_cache.transport.RedisAsyncTransport
    options:
      show_root_heading: true

---

## Per-Request Control (`execution_options`)

Pass these keys to `execution_options` on any `session.get()` call to control
caching on a per-call basis without changing the model's `CacheConfig`.

| Key | Type | Description |
|---|---|---|
| `cache` | `bool` | Set to `False` to bypass the cache for this call entirely |
| `cache_ttl` | `int` | Override TTL (seconds) for this specific call |

**Example — Bypass cache for a single call:**

```python
with Session(engine) as session:
    # Always queries the database, never reads or writes the cache
    hero = session.get(Hero, 1, execution_options={"cache": False})
```

**Example — Custom TTL for a single call:**

```python
with Session(engine) as session:
    # Cache this result for 60 seconds, ignoring the model's __cache_config__.ttl
    hero = session.get(Hero, 1, execution_options={"cache_ttl": 60})
```

---

## `CacheConfig` Field Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `True` | When `False`, all `session.get()` calls for this model skip the cache |
| `ttl` | `int \| None` | `None` | TTL in seconds; `None` falls back to `SQLModelCache.configure(default_ttl=...)` |

## `SQLModelCache.configure()` Parameter Reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `transport` | `CacheTransport \| AsyncCacheTransport` | required | Redis transport instance |
| `default_ttl` | `int` | `300` | Default TTL (seconds) for models without an explicit `CacheConfig.ttl` |
| `key_prefix` | `str` | `"sqlmodelcache"` | Redis key prefix; override per-deployment to avoid key collisions |
| `enabled` | `bool` | `True` | Global kill switch; `False` disables all caching, reads, and invalidations |

Raised when cache operations are attempted before `SQLModelCache.configure()` is called.

## `sqlmodel_cache.transport`

### `RedisSyncTransport`

Synchronous Redis transport wrapping `redis.Redis`.

### `RedisAsyncTransport`

Asynchronous Redis transport wrapping `redis.asyncio.Redis`.
