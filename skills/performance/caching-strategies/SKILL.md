---
name: caching-strategies
description: Cache layer and invalidation design across browser, CDN, reverse proxy, application, and data stores, covering cache keys, TTL versus explicit invalidation, cache-aside, read-through, write-through, stampede protection, negative caching, stale-while-revalidate, and hit-rate measurement. Use when the user asks how to add a cache, reports stale data or a cache stampede, wants to tune cache headers, or needs to measure hit rate in Redis or Memcached.
metadata:
  opencode/autoinvoke: true
---

# Caching Strategies

A cache is a bet that a later read will be satisfied by an older value. The whole design
question is not "should we cache" but three narrower ones: which layer sees the most reads,
what exactly is the key, and how does the value become invalid without serving wrong data.
A cache without an invalidation story is a correctness bug with a performance veneer.

## When to use this

- The same expensive query or render dominates read traffic.
- You are adding Redis or Memcached and need to pick a key scheme.
- A deploy caused stale content and you need a coherent invalidation design.
- A hot key is melting down under a burst of requests.
- You need to tune `Cache-Control`, `ETag`, `s-maxage`, or CDN settings.
- You must answer "what is our hit rate" and the number keeps moving.

## Quick start

Cache-aside with an explicit TTL, a bounded key space, and instrumentation:

```js
// cacheAside.js
import { RedisClientType } from 'redis';

const client = createClient({ url: process.env.REDIS_URL });
await client.connect();

const DEFAULT_TTL_SECONDS = 60;

export async function getProduct(id) {
  const key = `v1:product:${id}`;
  const cached = await client.get(key);
  if (cached !== null) {
    metrics.increment('cache.hit', { layer: 'redis', resource: 'product' });
    return JSON.parse(cached);
  }
  metrics.increment('cache.miss', { layer: 'redis', resource: 'product' });

  const row = await db.query('SELECT * FROM products WHERE id = $1', [id]);
  const value = row.rows[0] ?? null;
  // Negative caching: store a sentinel with a short TTL so misses stop hammering the DB.
  await client.set(key, JSON.stringify(value), { EX: value === null ? 5 : DEFAULT_TTL_SECONDS });
  return value;
}
```

```bash
# Inspect a key's remaining TTL and confirm it was actually written.
redis-cli TTL v1:product:42
redis-cli --raw GET v1:product:42

# Global hit/miss counters exposed by the Redis INFO command.
redis-cli INFO stats | grep -E 'keyspace_hits|keyspace_misses'
```

Compute the ratio from those two counters rather than trusting a library's self-report:

```bash
hits=$(redis-cli --no-raw INFO stats | awk -F: '/keyspace_hits/{print $2}' | tr -d '\r')
misses=$(redis-cli --no-raw INFO stats | awk -F: '/keyspace_misses/{print $2}' | tr -d '\r')
awk -v h="$hits" -v m="$misses" 'BEGIN{ if (h+m>0) printf "hit rate = %.2f%%\n", 100*h/(h+m) }'
```

## Cache layers

Put the cache as close to the reader as the data's change rate allows. A read that never
crosses the network is cheaper than one that crosses it and hits Redis.

| Layer | Scope | Invalidation trigger | Typical controls |
| --- | --- | --- | --- |
| In-process (heap) | One instance, one machine | Process restart, size/TTL eviction | Bounded LRU, TTL |
| Framework/ORM | One process | TTL, explicit clear | Query cache with a max size |
| Reverse proxy (nginx) | One node | TTL, `purge`, cache bypass | `proxy_cache`, `proxy_cache_valid` |
| CDN / edge | Global | Purge API, TTL | `s-maxage`, stale-while-revalidate, surrogate keys |
| Distributed cache (Redis/Memcached) | All app instances | TTL, explicit delete, pub/sub message | `SET ... EX`, `DEL`, TTL jitter |
| Database buffer pool | One DB node | Internal | Sizing, not application-visible |

Adding a lower layer without removing the upper one multiplies invalidation paths. Pick
the fewest layers that get you to the latency target.

## Which reads to cache

Cache reads that are (a) frequent, (b) expensive relative to a cache read, and (c)
tolerant of a bounded staleness window. The third criterion is the one people skip.

| Read | Cache? | Why |
| --- | --- | --- |
| Hot config or feature flags | Yes, TTL in seconds to minutes | Small, read constantly, cheap to refresh |
| Reference/lookup data (countries, plans) | Yes, TTL longer | Rarely changes, wide fanout |
| User profile by id | Yes, key includes tenant and user id | Read on nearly every request |
| Per-request authorization decision | Yes, very short TTL | Expensive, but staleness is a security decision, not just a performance one |
| Aggregate analytics rollup | Yes | Expensive, tolerates delay |
| Single object read right after a write | Usually not | Guarantees the read-your-own-write question is answered somewhere else |
| Unique per-request tokens or nonces | Never | No reuse |
| Anything whose value the user just changed | Careful | You need write-through, explicit invalidation, or an explicit consistency contract |

## Cache keys

A key must encode everything that changes the answer. The classic bug is a key missing the
tenant, the locale, the currency, the schema version, or the authorization scope.

```
v{schema_version}:{entity}:{tenant_id}:{entity_id}:{variant}
```

- **Version first.** Bumping the version invalidates an entire generation atomically
  without a scan. This is far cheaper than `KEYS`-style deletion.
- **Tenant and locale in the key.** Two users on the same `entity_id` in different tenants
  must not collide.
- **Auth scope when the answer is user-specific.** If the payload is filtered by
  permissions, either include a permission-set hash in the key or cache the unfiltered
  value and filter after the read.
- **No unbounded parts.** Never put a request ID, timestamp, or raw query string with
  attacker-controlled cardinality in the key. That is a memory-exhaustion vector.

Namespace by environment and prefix, so you can point a second Redis instance at staging
without a migration.

## TTL versus explicit invalidation

| Approach | Behavior | Use when |
| --- | --- | --- |
| TTL only | Value expires after a fixed window; no delete needed | Data changes slowly; a bounded staleness window is acceptable |
| Explicit invalidation only | Delete on write; no expiry | Data changes often but reads are low-volume; deletion is guaranteed |
| Both | Expiry as a safety net, delete for freshness | The normal production choice |
| No cache | Always read the source | Correctness beats latency, or the read is already cheap |

If you can only pick one, pick TTL. Expiring data is stale data. Deleting data is a
missing key, which degrades to a source-of-truth read, not a wrong answer. Explicit
invalidation is a distributed-systems problem; TTL is local and always works.

Add jitter to TTLs so a bulk-populated key set does not expire simultaneously:

```js
const ttl = BASE_TTL_SECONDS + Math.floor(Math.random() * JITTER_SECONDS);
await redis.set(key, payload, { EX: ttl });
```

## Cache patterns

### Cache-aside (lazy loading)

The application checks the cache, and on a miss loads from the source of truth and writes
the cache. The default choice: the source of truth stays authoritative and the cache is
purely an optimization, so a lost cache is never a correctness problem.

```js
const value = (await cache.get(key)) ?? (await loadFromDb() && await cache.set(key, value));
```

Cost: every miss pays both the read and the write. Benefit: no cache-filling logic runs on
the write path unless you ask for it.

### Read-through

A cache client encapsulates the load, so callers never see a miss. Same tradeoffs, better
encapsulation. Typically implemented as a wrapper around a "loader" function.

### Write-through

Every write goes to the source of truth *and* the cache, synchronously. Reads never miss
for recently written data. Cost: write latency includes the cache write, and a cache
failure now fails the write unless you deliberately make it best-effort.

### Write-behind (write-back)

The write lands in the cache and is flushed to the source of truth asynchronously. This
absorbs write bursts and can coalesce many updates into one, but it means the source of
truth can be stale or, if the process dies before the flush, permanently wrong. Use it only
where losing recent writes is genuinely acceptable, and always make the flush observable.

Decision table:

| Pattern | Read freshness | Write latency | Failure mode | Pick it when |
| --- | --- | --- | --- | --- |
| Cache-aside | Up to TTL stale | Unaffected | Slower reads while cold | Default for most systems |
| Read-through | Up to TTL stale | Unaffected | Slower reads while cold | You want the abstraction, not different semantics |
| Write-through | Fresh if cache is the read path | Includes cache write | Cache failure can fail writes | Read-your-write matters |
| Write-behind | Stale until flush | Lowest | Data loss on crash | Write bursts you can afford to lose |
| No cache | Always fresh | Unaffected | Slowest reads | Correctness-critical, cheap read |

## Stampede protection

A stampede is many requests missing the same hot key at the same instant and all loading it
simultaneously. On a popular key this can turn a mild cache miss into a database outage.

Four defenses, usually combined:

1. **Single-flight (in-process).** Deduplicate concurrent loads within one process.
2. **Distributed lock.** Only the lock holder loads; others wait or serve stale.
3. **Stale-while-revalidate.** Serve the stale value immediately, refresh in the
   background. Best UX, no lock needed.
4. **Jittered pre-expiry.** Refresh a key slightly before it expires so misses spread out.

```js
// Single-flight in-process, with a small negative-result memo.
const inflight = new Map();

async function loadOnce(key, loader) {
  if (inflight.has(key)) return inflight.get(key);
  const p = loader()
    .catch((err) => { inflight.delete(key); throw err; })
    .finally(() => setTimeout(() => inflight.delete(key), 0));
  inflight.set(key, p);
  return p;
}
```

```js
// Distributed lock via SET NX with an expiry, so a crashed holder cannot deadlock the key.
const lockKey = `lock:${key}`;
const lockTtlMs = 5000;
const acquired = await redis.set(lockKey, processToken, { NX: true, PX: lockTtlMs });
if (acquired) {
  try {
    const fresh = await loadFromDb();
    await redis.set(key, JSON.stringify(fresh), { EX: DEFAULT_TTL_SECONDS });
    return fresh;
  } finally {
    // Only delete our own lock; someone else may have taken it after expiry.
    await redis.del(lockKey);
  }
}
const stale = await redis.get(key);
if (stale !== null) return JSON.parse(stale);
return loadFromDb();
```

Two caveats that bite in practice. The lock TTL must exceed the expected load time, or
two holders run concurrently; and the `del` must be conditional on the token you wrote,
or you will delete someone else's lock. `GETDEL` exists in Redis 6.2 and later and does
the compare-and-delete atomically.

## Negative caching

A missing key is still an answer. Without negative caching, a repeatedly requested
nonexistent ID becomes a permanent database load generator.

```js
// Distinguish "absent" from "cache miss" in the payload itself.
const MISS = Symbol('miss');
const payload = value === null
  ? { __neg__: true }
  : { v: value };

// Read side
const raw = await cache.get(key);
if (raw === null) return MISS;               // not cached at all
const parsed = JSON.parse(raw);
return parsed.__neg__ ? null : parsed.v;     // cached "definitely absent"
```

Keep negative TTLs short (seconds) and much shorter than positive TTLs. A key that appears
should become visible quickly.

## HTTP and CDN caching

```http
HTTP/1.1 200 OK
Cache-Control: public, max-age=60, s-maxage=300, stale-while-revalidate=60, stale-if-error=86400
ETag: "a1b2c3d4"
Last-Modified: Sat, 10 Oct 2026 09:00:00 GMT
Vary: Accept-Encoding, Accept-Language
```

| Directive | Meaning |
| --- | --- |
| `max-age=N` | Fresh for N seconds in any cache, including the browser |
| `s-maxage=N` | Fresh for N seconds in shared caches (CDN, proxy) only; overrides `max-age` for them |
| `no-cache` | May be stored, but must be revalidated before reuse (often still a 304) |
| `no-store` | Must not be stored at all. Use for anything sensitive. |
| `private` | Only for a single user's cache; shared caches must not store it |
| `must-revalidate` | Once stale, do not serve stale even when disconnected |
| `stale-while-revalidate=N` | Serve stale for up to N seconds while refreshing in the background |
| `stale-if-error=N` | Serve stale for up to N seconds if the origin errors |
| `immutable` | The response will not change during its freshness lifetime; do not revalidate |
| `Vary` | List of request headers the cache key must include |

`stale-while-revalidate` and `stale-if-error` are defined in RFC 5861. They are the
simplest stampede defense available at the edge, because only one refresh request needs to
succeed while everyone else is served.

Authenticated responses are a frequent leak: if you cache a personalized response in a
shared cache without `private`, one user can be served another user's page. Decide the
cache key carefully before enabling a shared cache at all.

## Coherency with the source of truth

Pick one contract per data set and write it down:

1. **Cache is authoritative.** Values are written only to the cache; the database is a
   rebuild target. Fast reads, but you own durability.
2. **Database is authoritative (default).** The cache is a derived view with a bounded
   staleness window. Deletes and short TTLs are the invalidation mechanism.
3. **Event-driven invalidation.** On write, publish an event; every instance evicts or
   refreshes its local copy. Lower staleness, but you now have a message-delivery problem
   and still need a TTL as the backstop.

A hybrid is normal and safe: TTL as the guaranteed bound, plus event or delete as the fast
path. What is not acceptable is relying on an invalidation channel with no expiry
backstop, because a dropped message becomes permanent staleness.

## Measuring hit rate

Track `hits`, `misses`, `miss_duration`, `evictions`, and `key_count`, each labeled by
cache layer and resource family. Label by *family*, never by key or user.

- **Hit rate by layer.** A low CDN hit rate with a high Redis hit rate means the CDN is
  bypassed or the key is too variant-heavy.
- **Miss cost.** Time spent in the loader on a miss, not the hit. This is the number that
  shows whether the cache is actually earning its keep.
- **Eviction rate.** Rising evictions mean the working set exceeds the cache. Growing the
  cache hides this; measure instead.

Beware of the split in your instrumentation: counting a hit before the payload is
deserialized hides parse cost, and counting after hides network cost. Decide which one you
are optimizing and instrument that consistently.

## Checklist

- [ ] Every cache has a documented key format including tenant, version, and auth scope.
- [ ] Every cache has a TTL, even when you also delete explicitly.
- [ ] The source of truth is named, and the consistency contract for it is written down.
- [ ] Personalized responses are never shared-cacheable without `private`.
- [ ] Hot keys have stampede protection (single-flight, a lock, or stale-while-revalidate).
- [ ] Negative results are cached with a short TTL.
- [ ] Key cardinality is bounded; no request IDs or timestamps in keys.
- [ ] Hit rate, miss cost, and eviction rate are instrumented and dashboarded.
- [ ] Redis has `maxmemory` and an eviction policy set deliberately.
- [ ] A schema or format version is in the key so a deploy can invalidate atomically.
- [ ] Sensitive responses send `Cache-Control: no-store`.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Users see each other's data | Shared cache serving a personalized response | Add `Cache-Control: private`, or include the auth scope in the key |
| One tenant sees another tenant's data | Key missing tenant id | Put tenant id in the key, and assert it in the cache wrapper |
| Data never refreshes after a deploy | Invalidated by deleting keys that no longer exist | Version the key prefix and bump the version |
| Cache grows until Redis OOMs | No `maxmemory`, or keys include unique request data | Set `maxmemory` and a policy; remove unbounded key parts |
| Database dies under load for a few seconds every minute | Expiring keys and stampede | Stale-while-revalidate or a lock; jitter TTLs |
| Writes appear to vanish | Write-behind with a lost flush | Prefer cache-aside or write-through; make the flush observable |
| Hit rate near zero | Keys include a per-request or per-timestamp component | Audit key construction; log a sample of real keys |
| Stale data for a long time after a delete | Delete failed silently | Log and alert on failed invalidation; keep a TTL backstop |
| Cache is slower than the database | Payload is large or serialization dominates | Store IDs not documents, or cache the rendered result, not the row |

## Anti-patterns

- **Caching everything.** A cache with no hit-rate budget becomes an extra network hop on
  the critical path. Decide what you are caching and why.
- **TTL as a magic number copied from a blog post.** Pick the number from the staleness
  the product can tolerate, and record it.
- **Cache with no invalidation path.** Time-to-live without a write-side story is
  "eventually consistent at an arbitrary interval".
- **Two caches with different keys for the same data.** They will disagree. One layer,
  one key scheme, or an explicit reason for the duplication.
- **Using a distributed lock as a general-purpose mutex.** Locks add a failure mode and a
  latency hop; prefer making loads cheap or serving stale.
- **Logging cache keys.** Keys frequently contain identifiers you do not want in logs, and
  log volume scales with traffic.
- **Treating a cache as a durability mechanism.** If losing the cache loses data, you have
  built a single point of failure that is also a data-loss point.
- **Publishing to Redis Pub/Sub for critical invalidation and assuming delivery.** Pub/Sub
  is fire-and-forget; keep the TTL backstop.

## References

- RFC 9111, HTTP Caching
  - https://www.rfc-editor.org/rfc/rfc9111.html
- RFC 5861, `stale-while-revalidate` and `stale-if-error`
  - https://www.rfc-editor.org/rfc/rfc5861.html
- MDN, Cache-Control
  - https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control
- MDN, Caching guide
  - https://developer.mozilla.org/en-US/docs/Web/Performance/Guides/Caching
- nginx proxy caching
  - https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache
- Redis eviction policies and key expiry
  - https://redis.io/docs/latest/develop/pc/expire/
  - https://redis.io/docs/latest/operate/rs/databases/memory-optimization/
- Redis `GETDEL` and other commands
  - https://redis.io/commands/getdel/
- Google SRE book, "Caching" and "Handling Overload"
  - https://sre.google/sre-book/caching/
  - https://sre.google/sre-book/handling-overload/
- RFC 7234 predecessor note: newer HTTP caching semantics live in RFC 9111 and RFC 9110
  - https://www.rfc-editor.org/rfc/rfc9110.html

## Deeper detail

- `references/http-caching.md` — request and response directives, revalidation with
  `ETag`/`Last-Modified`, conditional requests, Vary, and CDN-specific behavior.
- `references/distributed-cache.md` — Redis data structures for caching, expiry
  configuration, memory policies, pub/sub invalidation, and cache warming.
