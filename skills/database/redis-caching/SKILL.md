---
name: Redis Caching and Data Structures
description: Covers cache-aside versus write-through versus write-behind, TTL and eviction policies, cache stampede and thundering herd protection, invalidation strategies, Redis data structures including hashes sets sorted sets and streams, persistence and replication, and when Redis is the wrong tool. Use when the user asks about caching, cache invalidation or TTLs, cache stampedes, rate limiting, leaderboards, job queues with Redis, or diagnosing Redis memory and eviction problems.
metadata:
  opencode/autoinvoke: true
---

# Redis Caching and Data Structures

Redis is an in-memory key-value store with data structures and single-command atomicity. It is
excellent at a narrow set of jobs: serving hot reads, counting, expiring data, ordering,
rate limiting, and small work queues. It is not a database with a Redis front end.

## When to use this

- A read path hits the primary database too often and you want to cut it.
- You need TTL-based expiry rather than an application cron job.
- You need atomic counters, rate limiters, leaderboards, or a lightweight job queue.
- A hot key is causing database load spikes and you want to absorb the burst.
- You are debugging `OOM command not allowed` or keys disappearing without explanation.
- You need to choose between caching, and the answer is not obviously "yes".

## Quick start

Cache-aside with a TTL, which is the pattern you will use most.

```python
import json
import redis

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

CACHE_TTL_SECONDS = 300
KEY = f"user:{user_id}:profile"


def get_user_profile(user_id: int) -> dict:
    key = f"user:{user_id}:profile"
    cached = r.get(key)
    if cached is not None:
        return json.loads(cached)

    row = db.execute(
        "SELECT id, email, display_name FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    if row is None:
        return None

    profile = {"id": row.id, "email": row.email, "display_name": row.display_name}
    r.setex(key, CACHE_TTL_SECONDS, json.dumps(profile))
    return profile


def invalidate_user_profile(user_id: int) -> None:
    r.delete(f"user:{user_id}:profile")
```

Set the TTL in the same command as the write (`SET ... EX`, `SETEX`, `GETEX`). A `SET` followed
by a separate `EXPIRE` leaves a window where the key has no TTL, and a crash in that window
produces a permanent key.

## The four caching patterns

| Pattern | Who populates the cache | Read latency | Staleness window | Complexity |
| --- | --- | --- | --- | --- |
| Cache-aside (lazy loading) | The reader, on a miss | Misses pay the origin cost | Until the next read after invalidation | Lowest. The default. |
| Read-through | The cache layer itself | Low | Same | Needs cache-layer support |
| Write-through | The writer | Low | Small | Write path is slower and now depends on Redis availability |
| Write-behind (write-back) | The writer queues; a worker persists | Lowest read | Potentially long, and data is lost if Redis dies | Highest. Needs a durable queue. |

**Cache-aside is the right default.** It keeps the cache optional: if Redis is down, the
application falls back to the database and stays correct, just slower.

```python
def update_user_profile(user_id: int, **fields) -> None:
    db.execute("UPDATE users SET email=%s, display_name=%s WHERE id=%s", (..., user_id))
    # Invalidate after the commit, not before. Invalidating before the write creates a window
    # where a concurrent reader repopulates the cache with pre-write data.
    r.delete(f"user:{user_id}:profile")
```

Write-behind is defensible only when the origin write is genuinely expensive and the queue
itself is durable. If the queue lives in the same Redis instance that might lose data, you have
converted a consistency problem into a data-loss problem.

## TTL and eviction

TTL is a correctness mechanism, not only a memory mechanism: it bounds how long an entry can be
wrong.

```text
Every cached key needs a TTL. An entry with no TTL is a memory leak with a business impact.
```

`maxmemory` is the ceiling above which the eviction policy runs. Set `maxmemory` to a value
that leaves headroom for replication buffers and fork memory, not to 100% of RAM.

| Policy | Evicts | Note |
| --- | --- | --- |
| `noeviction` | Nothing; writes return an error | Correct for a Redis used as a primary datastore, wrong for a cache |
| `allkeys-lru` | Any key, least recently used | Safe default for a pure cache |
| `allkeys-lfu` | Any key, least frequently used | Better hit rate for skewed access; counters start from zero after a restart |
| `allkeys-random` | Any key at random | Only useful as a control or for uniformly cold workloads |
| `volatile-lru` / `volatile-lfu` / `volatile-ttl` | Only keys with a TTL | Keys without a TTL become unevictable, which is a memory leak |
| `volatile-random` | Only keys with a TTL, random | Same caveat |

The `volatile-*` family is a trap: a key without a TTL can never be evicted, so a single
`SET` without an `EX` under memory pressure turns into `OOM command not allowed`.

```text
CONFIG GET maxmemory
CONFIG GET maxmemory-policy
INFO memory
```

Cache sizing: measure your key count, average value size, and desired TTL, then compute
expected resident size. Add headroom for the copy-on-write overhead of background saves and
replication. Do not size by guesswork.

## Cache stampede and thundering herd

A stampede is a hot key expiring (or being deleted) while thousands of requests are in flight.
All of them miss, all of them hit the origin, and the origin falls over.

Defences, in order of cost:

**1. Serve the stale value while one request refreshes.** Jitter the TTL so keys do not all
expire together.

```python
import random

ttl = BASE_TTL_SECONDS * (0.8 + random.random() * 0.4)   # +/- 20% jitter
r.setex(key, ttl, value)
```

**2. Request coalescing in the application.** One refresh per key per process.

```python
_inflight: dict[str, asyncio.Task] = {}

async def get_fresh(key: str):
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(refresh(key))
        _inflight[key] = task
        try:
            return await task
        finally:
            _inflight.pop(key, None)
    return await asyncio.shield(task)
```

**3. A short-lived mutex key in Redis.**

```python
LOCK_TTL_MS = 5000   # must exceed the refresh time, must stay far below the cache TTL

def get_or_refresh(key: str):
    cached = r.get(key)
    if cached is not None:
        return cached

    if r.set(f"lock:{key}", "1", nx=True, px=LOCK_TTL_MS):
        try:
            value = load_from_origin(key)
            r.setex(key, CACHE_TTL_SECONDS, value)
            return value
        finally:
            r.delete(f"lock:{key}")   # only safe because you hold the lock
    # Someone else is refreshing. Serve stale if you have it, otherwise wait briefly.
    stale = r.get(f"stale:{key}")
    if stale is not None:
        return stale
    time.sleep(0.05)
    return get_or_refresh(key)
```

The mutex pattern has a failure mode: if the holder dies, the lock TTL expires and the next
request takes over, but requests in the meantime all miss. Keep the mutex TTL short and always
try to serve something.

**4. Probabilistic early expiration.** Recompute slightly before the TTL expires, chosen at
random, so only one request wins.

```python
if cached is None or (ttl_left and random.random() < 0.01):
    # early refresh
    ...
```

**5. Prewarm at deploy time** for keys you know will be hot, so the stampede happens before
traffic arrives.

## Invalidation strategies

| Strategy | How | When |
| --- | --- | --- |
| TTL only | Entries expire and are recomputed | Simple, bounded staleness, some wasted reads |
| Delete on write | The writer deletes affected keys | Correct and cheap when you know the key set |
| Versioned keys | Embed a version counter in the key; bump the counter to invalidate a whole group | Many keys must go together |
| Tag-based groups | Maintain a set of keys per entity and delete the set | Fan-out deletes where you track membership |
| Pub/Sub notification | Publish a change, subscribers drop their local copy | Multi-node local caches |
| Eventual refresh | TTL plus background warmer | Hot keys where a miss is unacceptable |

Versioned keys, which is the cleanest invalidation for grouped caches:

```python
def version(namespace: str) -> int:
    v = r.get(f"ver:{namespace}")
    if v is None:
        r.set(f"ver:{namespace}", 1, nx=True)
        v = r.get(f"ver:{namespace}")
    return int(v)


def cache_key(namespace: str, ident: str) -> str:
    return f"{namespace}:v{version(namespace)}:{ident}"


def invalidate(namespace: str) -> None:
    # Every key for this namespace becomes unreachable. Old entries expire on their own TTL.
    r.incr(f"ver:{namespace}")
```

This makes invalidation O(1) at the cost of temporarily holding memory for the old version
until its TTL expires. You do not need to know which keys existed.

Pub/Sub for multi-node local caches:

```python
import redis

subscriber_pubsub = redis.Redis.from_url(REDIS_URL).pubsub(ignore_subscribe_messages=True)
subscriber_pubsub.subscribe("cache.invalidate")

for message in subscriber_pubsub.listen():
    if message["type"] == "message":
        key = message["data"]
        local_cache.pop(key, None)
```

Pub/Sub in Redis is fire-and-forget. Subscribers that are disconnected miss messages. Treat it
as a hint to re-read or drop, never as a delivery guarantee. Use Streams when you need at-least
-once delivery and consumer groups.

## Data structures and when each fits

| Structure | Commands | Fits |
| --- | --- | --- |
| String | `GET`, `SET`, `INCR`, `INCRBY`, `DECR`, `APPEND`, `MGET`, `GETDEL` | Cached values, counters, serialized documents |
| Hash | `HSET`, `HGET`, `HMGET`, `HINCRBY`, `HGETALL`, `HDEL`, `HEXISTS` | Objects with named fields, partial reads without deserializing everything |
| Set | `SADD`, `SREM`, `SMEMBERS`, `SISMEMBER`, `SCARD`, `SRANDMEMBER`, `SSCAN`, `SDIFF`, `SINTER`, `SUNION` | Membership, tags, unique sets, set operations |
| Sorted set | `ZADD`, `ZINCRBY`, `ZRANGE`, `ZRANGEBYSCORE`, `ZREVRANK`, `ZRANK`, `ZREM`, `ZPOPMIN`, `ZCARD` | Leaderboards, rate limiters by time, priority queues |
| List | `LPUSH`, `RPUSH`, `LPOP`, `RPOP`, `LRANGE`, `BLPOP`, `BRPOP` | Simple queues and stacks; blocking pops for workers |
| Stream | `XADD`, `XLEN`, `XRANGE`, `XREAD`, `XREADGROUP`, `XACK`, `XAUTOCLAIM`, `XGROUP CREATE` | Durable-ish queues with consumer groups and pending-entry tracking |
| Bitmap | `SETBIT`, `GETBIT`, `BITCOUNT`, `BITOP` | Boolean-per-user flags, coarse presence, simple bloom filters |
| HyperLogLog | `PFADD`, `PFCOUNT`, `PFMERGE` | Cardinality estimation with a small fixed footprint |

```text
Counter:            INCR key
Object:             HSET user:42 name "Ada" email "ada@example.com"
Membership:         SADD article:7:tags postgres indexing
Leaderboard:        ZADD leaderboard 1200 user:42
Rate limit window:  ZREMRANGEBYSCORE rl:user:42 0 <window_start>  then  ZCARD rl:user:42
```

Sorted set rate limiter, the sliding-window form:

```python
def allow(identity: str, limit: int, window_seconds: int) -> bool:
    key = f"rl:{identity}"
    now = int(time.time() * 1000)
    window_start = now - window_seconds * 1000

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)   # drop events outside the window
    pipe.zadd(key, {f"{now}": random.random()})   # unique member per request
    pipe.zcard(key)
    pipe.pexpire(key, window_seconds * 1000)
    _, _, count, _ = pipe.execute()
    return count <= limit
```

Use a unique member per event so repeated requests in the same millisecond do not collapse into
one. The exact pipeline composition varies by client; check your client's API.

## Atomicity, pipelining and Lua

Redis executes commands one at a time, so single commands are atomic. Multi-step operations
are not, unless you make them atomic.

```text
MULTI / EXEC                transaction, queued then executed; no rollback on error
Pipeline                    batch several round trips; not atomic by itself
EVAL / EVALSHA               run a Lua script atomically on the server
Functions (FCALL)           server-side functions, a newer alternative to scripting
```

```lua
-- compare-and-delete, safe against releasing someone else's lock
-- KEYS[1] = lock key, ARGV[1] = expected token
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
```

```python
release_lock = r.register_script("""
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
""")
release_lock(keys=[lock_key], args=[my_token])
```

Never release a lock with a bare `DEL` after the TTL may have expired and someone else taken
it. Check the token.

## Persistence, replication and failure

| Mechanism | What it does | Gap |
| --- | --- | --- |
| RDB | Point-in-time snapshot | Data since the last snapshot is lost |
| AOF | Appends writes to a log; fsync policy trades durability for latency | Replaying a long AOF is slow; rewrite compacts it |
| Replication | Async copies to replicas | By default a failover can lose recent writes |
| Sentinel | Monitoring and automatic failover | |
| Cluster | Sharding across nodes, hash slots | Multi-key operations require keys in the same slot |

Key facts:

- Replication is asynchronous by default. A primary failover can lose acknowledged writes. If
  you cannot lose writes, you need a durability configuration and you should understand exactly
  what it costs.
- Cluster partitions the key space into a fixed number of hash slots. Multi-key commands and
  transactions only work within a slot. Use hash tags: `user:{42}:profile` and
  `user:{42}:settings` always land in the same slot because the substring inside braces is
  hashed.
- Persistence configuration is a durability decision. Verify what your current settings are and
  what they mean; do not assume.

```text
INFO replication
INFO memory
INFO stats
CONFIG GET appendonly
CONFIG GET appendfsync
CONFIG GET save
```

Treat Redis as authoritative only if you have configured and tested persistence and failover,
and you have accepted the acknowledged-write-loss window.

## When Redis is the wrong tool

| Need | Why Redis is wrong |
| --- | --- |
| Relational queries, joins, arbitrary ad-hoc filters | Redis has no general query language; you are writing application code to emulate it |
| Cross-key transactional guarantees across a keyspace | Cluster multi-key transactions are single-slot only; the model is very different from a relational transaction |
| Durability with no acknowledged-write loss | Async replication means a failover can lose recent writes unless you configure and accept the cost |
| Unbounded data growth | In-memory, bounded by `maxmemory`; eviction then silently drops data |
| Full-text search across many fields | Redis search exists as a module with its own model; a search engine or the database is usually a better fit |
| Anything requiring a secondary index over cached data | Redis keys are the only index; a secondary index is a separate structure you must maintain |
| Low latency for writes under heavy durability requirements | Every write is durable work plus replication |

Also wrong: using Redis as the only copy of data you cannot regenerate. If Redis is a cache,
losing it must cost latency, not correctness. Test that by turning it off.

## Diagnosing Redis

```text
INFO memory                         used memory, fragmentation, eviction counts
MEMORY USAGE key                   size of one key
MEMORY DOCTOR                      advice on memory issues
OBJECT ENCODING key                internal representation of a value
DBSIZE                             number of keys
SLOWLOG GET 10                     commands slower than slowlog-log-slower-than
LATENCY DOCTOR                     event latency histogram summary
redis-cli --bigkeys                scan for large keys by type
redis-cli --hotkeys                requires an LFU maxmemory-policy
CLIENT LIST                        connections and their state
```

`used_memory_rss` well above `used_memory` means fragmentation or unreturned allocator memory.
`evicted_keys` climbing means `maxmemory` is too low or keys have no TTL with a `volatile-*`
policy.

## Checklist

- Every key has a TTL unless it is deliberately permanent and documented.
- `maxmemory` is set below physical RAM with headroom, and the policy is `allkeys-lru` or
  `allkeys-lfu` for a pure cache.
- Invalidation happens after the database commit, never before.
- Hot keys have TTL jitter and, where a miss is unacceptable, a coalescing or mutex layer.
- Multi-step read-modify-write uses a pipeline, `MULTI/EXEC`, or a script, not separate round
  trips.
- Cache keys use hash tags where cluster slots or atomicity across keys matter.
- Lock release compares the token inside a script.
- `evicted_keys`, `SLOWLOG`, and `INFO memory` are monitored.
- The application degrades correctly when Redis is unavailable: cache misses hit the database
  and nothing breaks.
- A periodic test exists that runs with Redis switched off.

## References

- Redis documentation home: https://redis.io/docs/latest/
- Caching patterns and cache-aside: https://redis.io/docs/latest/develop/use-cases/cache-aside/
- Prefetch and warming: https://redis.io/docs/latest/develop/use-cases/prefetch-cache/
- Rate limiting: https://redis.io/docs/latest/develop/use-cases/rate-limiter/
- Leaderboards: https://redis.io/docs/latest/develop/use-cases/leaderboard/
- Job queues: https://redis.io/docs/latest/develop/use-cases/job-queue/
- Session storage: https://redis.io/docs/latest/develop/use-cases/session-store/
- Pub/Sub: https://redis.io/docs/latest/develop/pubsub/
- Data types overview: https://redis.io/docs/latest/develop/data-types/
- Strings: https://redis.io/docs/latest/develop/data-types/strings/
- Hashes: https://redis.io/docs/latest/develop/data-types/hashes/
- Sorted sets: https://redis.io/docs/latest/develop/data-types/sorted-sets/
- Streams: https://redis.io/docs/latest/develop/data-types/streams/
- Probabilistic data structures: https://redis.io/docs/latest/develop/data-types/probabilistic/
- Eviction policies: https://redis.io/docs/latest/develop/reference/eviction/
- Key specifications: https://redis.io/docs/latest/develop/reference/key-specs/
- Scripting with Lua: https://redis.io/docs/latest/develop/programmability/eval-intro/
- `SET` command: https://redis.io/docs/latest/commands/set/
- `EVAL` command: https://redis.io/docs/latest/commands/eval/
- `SLOWLOG`: https://redis.io/docs/latest/commands/slowlog/
- `OBJECT` command: https://redis.io/docs/latest/commands/object/
- Configuration: https://redis.io/docs/latest/operate/oss_and_stack/management/config/
- Persistence: https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
- Replication: https://redis.io/docs/latest/operate/oss_and_stack/management/replication/
- Sentinel: https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/
- Scaling and clustering: https://redis.io/docs/latest/operate/oss_and_stack/management/scaling/
- Memory optimization: https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/
- Debugging and diagnostics: https://redis.io/docs/latest/operate/oss_and_stack/management/debugging/
