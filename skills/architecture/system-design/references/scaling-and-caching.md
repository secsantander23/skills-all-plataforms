# Scaling and Caching Detail

## Making services stateless

State that must leave the process:

| State | Where it goes |
|---|---|
| Session data | Signed cookie, or a shared store |
| Uploaded files | Object storage |
| In-progress workflow | Durable store or queue |
| Rate limit counters | Shared store |
| Feature flags with sticky targeting | Evaluated from shared config, not process memory |
| Caches | Shared cache service |

If a component holds state in memory, scaling out requires the load balancer to route a given
user to the same replica. That is sticky sessions, and it breaks on deploy, on restart, and on
replica loss.

## Database scaling ladder

Apply in order. Each step is cheaper than the next.

1. **Add an index.** Usually the whole answer.
2. **Fix the query.** N+1 queries, no predicate, unbounded result sets.
3. **Cache reads.** Bounded TTL, correct key.
4. **Read replicas.** Move reads. Watch replica lag for read-your-own-writes.
5. **Vertical resize.** Fast, has a ceiling.
6. **Partition or shard.** Now you own rebalancing and cross-shard queries.
7. **Different storage for a different access pattern.** A search index or a wide-column store
   for a key-value read path.

Most "we need to shard" conclusions are step 1 or 2 that nobody measured.

## Read replicas

Good for: analytics, search indexing, cache warming, long-running reads.

Costs:

- Replication lag, so a read after a write may miss it. Solve with a short "read from primary"
  window after writes, or a sticky-to-primary flag.
- Failover needs a new primary election, and you own that logic.
- Write throughput is unchanged.

If read-your-own-writes matters, say how you handle lag. "It is eventually consistent" is not a
plan.

## Sharding triggers

Shard when you have measured a limit, not when you imagine one:

- Write throughput is at the engine's practical single-writer limit.
- The dataset no longer fits with the index in memory.
- A tenant's volume would degrade everyone else.
- Regulatory or tenant isolation requires physical separation.

Sharding strategies:

| Strategy | Good for | Cost |
|---|---|---|
| Range by tenant | Few large tenants | Hot tenants, uneven load |
| Hash by user | Even distribution | Cross-shard queries, rebalancing |
| Directory lookup | Flexible | An extra service, and it is a SPOF unless replicated |

Whatever you pick, you are choosing to accept cross-shard queries and to own rebalancing.
Say so.

## Cache layering

```
request
  -> CDN            cache key must include every variant that changes the body
  -> reverse proxy   cheap, static-ish
  -> app cache       per-service, per-tenant keys, bounded TTL
  -> database
```

Key design:

```
cache key = tenant_id + ":" + user_scope + ":" + entity_id + ":" + schema_version
```

Every element that changes the response belongs in the key. Omitting the tenant is an access
control incident. Omitting a schema version gives you stale shapes after a deploy.

Eviction:

- **TTL** for freshness.
- **LRU or equivalent** for memory bounds.
- **Both.** TTL alone does not bound memory when traffic is skewed; LRU alone does not bound
  staleness.

## Stampede protection

When a hot key expires, every concurrent request misses at once.

| Technique | How |
|---|---|
| Early expiry | Refresh slightly before the TTL ends, serving stale meanwhile |
| Probabilistic refresh | A small fraction of misses attempt the refresh |
| Lock on refresh | One refresher, others serve stale or wait |
| Prewarm | Refresh known hot keys ahead of time |

Pick one per cache. The early-expiry shape:

```python
def get_or_refresh(cache, key: str, ttl: int, early: int, loader) -> Any:
    entry = cache.get(key)
    now = time.time()
    if entry is not None:
        value, expires_at = entry
        if now < expires_at - early:
            return value
        if not acquire_soft_lock(cache, key):
            return value  # someone else is refreshing; serve stale
    fresh = loader(key)
    cache.set(key, (fresh, now + ttl))
    release_soft_lock(cache, key)
    return fresh
```

`acquire_soft_lock` should be a short-lived key with a set-if-not-exists semantic, with an
expiry so a crashed refresher cannot deadlock the key.

## What never to cache

- Anything with an authorization scope you cannot reliably include in the key.
- Failures and empty responses as if they were values.
- Anything without a bound.
- Data that changes faster than the cache can be invalidated, unless eventual staleness is
  acceptable for that specific read.

## Measuring

| Metric | What it tells you |
|---|---|
| Hit rate per layer | Whether the cache is earning its complexity |
| Bytes saved at the origin | Actual cost and load reduction |
| Latency split by layer | Where the time goes |
| Eviction rate | Memory pressure |
| Stale-read reports | Whether TTLs are too long |

A cache with no hit-rate metric is an unmeasured dependency in your request path.