# Redis Patterns Cookbook

## Distributed lock with a token

The safe shape: acquire with `SET key token NX PX ttl`, release with a compare-and-delete script,
and extend with a script that only extends if you still hold it.

```python
import os
import time
import uuid

ACQUIRE = "SET KEYS[1] ARGV[1] NX PX ARGV[2]"
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
EXTEND = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class Lock:
    def __init__(self, redis, name: str, ttl_ms: int = 10000):
        self.r = redis
        self.key = f"lock:{name}"
        self.token = uuid.uuid4().hex
        self.ttl_ms = ttl_ms
        self._release = redis.register_script(RELEASE)
        self._extend = redis.register_script(EXTEND)

    def acquire(self, blocking_timeout: float = 0.0) -> bool:
        deadline = time.monotonic() + blocking_timeout
        while True:
            if self.r.set(self.key, self.token, nx=True, px=self.ttl_ms):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def extend(self) -> bool:
        return bool(self._extend(keys=[self.key], args=[self.token, self.ttl_ms]))

    def release(self) -> None:
        self._release(keys=[self.key], args=[self.token])

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"could not acquire {self.key}")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
```

Failure modes to design around:

- The holder crashes. The TTL expires and another process takes the lock. The original process,
  if it is merely slow, can still be running. The token check on release prevents it from
  deleting someone else's lock, but it does not prevent it from doing the work.
- Long work outlives the TTL. Extend periodically, or the second entrant starts while the first
  is still running.
- Clock and network delays around expiry. Keep the TTL comfortably longer than the expected
  work, and treat the lock as advisory.

For correctness-critical mutual exclusion, prefer a database-level mechanism: a unique
constraint, a conditional `UPDATE`, or `SERIALIZABLE` with retry. A Redis lock is a performance
and coordination tool, not a correctness proof.

## Rate limiting

Fixed window, counter plus expiry:

```python
def allow_fixed_window(r, identity: str, limit: int, window_seconds: int) -> bool:
    key = f"rl:fixed:{identity}:{int(time.time()) // window_seconds}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_seconds + 1)
    count, _ = pipe.execute()
    return count <= limit
```

Simple, but allows 2x the limit across a window boundary. If that matters, use the sliding
window sorted-set form shown in SKILL.md.

Token bucket, smoothed:

```text
available = min(capacity, available + elapsed * refill_rate)
if available >= 1: allow, available -= 1
else: deny
```

Keep the bucket state in Redis with a hash and update it inside a Lua script so the read and
write are atomic.

## Leaderboard

```python
def submit_score(r, leaderboard: str, member: str, score: float) -> None:
    # ZINCRBY is atomic; use it when scores accumulate.
    r.zincrby(leaderboard, score, member)


def top(r, leaderboard: str, n: int = 10):
    return r.zrevrange(leaderboard, 0, n - 1, withscores=True)


def rank_of(r, leaderboard: str, member: str):
    # Zero-based rank; ties share the lower rank number.
    return r.zrevrank(leaderboard, member)
```

For very large leaderboards, shard by score range and merge, or maintain a top-N set separately
from the full ranking.

## Work queue with a list

```text
Producer:  RPUSH jobs:pending <payload>
Consumer:  BRPOP jobs:pending 5        -- returns nil after 5s
```

Bounded variant, for backpressure:

```text
Producer:  if LLEN jobs:pending < 1000 then LPUSH jobs:pending <payload> end
```

`BRPOP` blocks the connection, so do not use it on a connection shared with other commands. Use
a dedicated connection per blocking consumer.

## Work queue with a stream

```text
Create the consumer group once:
  XGROUP CREATE mystream workers $ MKSTREAM

Produce:
  XADD mystream * job_id <id> payload <json>

Consume:
  XREADGROUP GROUP workers worker-1 COUNT 10 BLOCK 5000 STREAMS mystream >

Acknowledge:
  XACK mystream workers <message-id>...        -- only after the work is durably done

Reclaim abandoned pending entries:
  XAUTOCLAIM mystream workers worker-2 60000 0 COUNT 10
```

Streams give you pending-entry tracking and consumer groups, which the list pattern lacks. Read
the Streams documentation for the exact `XAUTOCLAIM` and trimming behaviour in your version.

Trim the stream or it grows without bound:

```text
XADD mystream MAXLEN ~ 100000 * payload <json>
XTRIM mystream MAXLEN ~ 100000
```

Approximate trimming (`~`) is cheaper and can leave a few more entries than requested.

## Distributed counters and rate of unique visitors

```text
INCR page:views:2024-06-01
HINCRBY user:42:stats articles_read 1
PFADD daily:unique page:view:12345      -- HyperLogLog, fixed small footprint
PFCOUNT daily:unique                     -- estimated cardinality
```

`INCR` on a key that does not exist creates it at 1. That makes it the simplest atomic counter
available, but it means a typo in a key name creates data that will never expire. Add a TTL.

## Bloom filter for negative caching

```text
BFADD exists:article-ids 12345            -- module command; check availability in your deployment
BFEXISTS exists:article-ids 12346
```

Without a module, a bitset approximation:

```text
SETBIT exists:article-ids 12345 1
GETBIT exists:article-ids 12346            -- 0 means definitely absent (or a hash collision)
```

A bitset over a large id space is large. A probabilistic structure with multiple hash functions
gives constant memory with a false-positive rate you choose. Use it only to avoid lookups you
know will fail; never as a source of truth.

## Tag-based invalidation

```python
def cache_entity(entity_id: int, value: dict, ttl: int = 300) -> None:
    key = f"entity:{entity_id}"
    pipe = r.pipeline()
    pipe.setex(key, ttl, json.dumps(value))
    pipe.sadd("entity:index", key)        # track membership for grouped invalidation
    pipe.execute()


def invalidate_entity(entity_id: int) -> None:
    key = f"entity:{entity_id}"
    pipe = r.pipeline()
    pipe.srem("entity:index", key)
    pipe.delete(key)
    pipe.execute()
```

Membership sets need TTLs too, or they leak. And for a large key space, prefer versioned keys
(O(1) invalidation, no bookkeeping) over a tracked index.

## Sessions

```text
SET session:<sid> <json> EX 3600
GET session:<sid>
DEL session:<sid>
```

Slide the expiry on activity if you want idle-timeout semantics:

```text
GETEX session:<sid> EX 3600
```

`GETEX` is available in recent Redis versions. If you must support older versions, use
`EXPIRE` after a `GET`, or `SET ... KEEPTTL` with the value. Session data in Redis means losing
the persistence configuration means logging everyone out. Decide whether that is acceptable.

## Atomic patterns without Lua

```text
INCR counter:1                            -- atomic increment
INCRBY counter:1 5                        -- atomic increment by a delta
DECR stock:sku-1
HSETNX cart:42 sku 1                      -- set a field only if absent
SADD queue:seen <value>                   -- returns 1 if newly added; use for dedupe
SISMEMBER seen:<user> <id>                -- check
ZADD leaderboard <score> <member> NX      -- NX / XX / GT / LT options
ZPOPMIN work 5                            -- pop the lowest-scored entries atomically
```

`ZPOPMIN` (and its blocking variant) is a clean priority-queue primitive.
