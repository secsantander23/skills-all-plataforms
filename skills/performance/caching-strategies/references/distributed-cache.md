# Distributed Caches

## Choosing a store

| Property | Redis | Memcached |
| --- | --- | --- |
| Data model | Strings, hashes, lists, sets, sorted sets, streams | Flat string key/value only |
| Persistence | Optional RDB snapshots, optional AOF | None by design |
| Expiry | Per-key, and volatile/all-keys policies | Per-key, lazily and actively expired |
| Pub/Sub | Yes | No |
| Transactions/Lua | Yes | No |
| Typical use | Cache, sessions, rate limits, queues, pub/sub | Pure cache, high fan-out, multi-threaded |

Both are in-memory. If you need durability, that is a database decision, not a cache one.

## Redis expiry and eviction

```bash
redis-cli CONFIG GET maxmemory
redis-cli CONFIG GET maxmemory-policy
redis-cli CONFIG SET maxmemory 2gb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

Set `maxmemory` and a policy deliberately. Without `maxmemory`, Redis grows until the
kernel OOM killer intervenes. With `maxmemory` but no policy, writes fail with
`OOM command not allowed`, which turns a cache into an availability dependency.

Policies: `noeviction`, `allkeys-lru`, `allkeys-lfu`, `volatile-lru`, `volatile-lfu`,
`allkeys-random`, `volatile-random`, `volatile-ttl`.

Choosing:

| Policy | Pick when |
| --- | --- |
| `allkeys-lru` / `allkeys-lfu` | Everything in the instance is cache; evict whatever is coldest |
| `volatile-lru` | You rely on TTLs; never evict a key with no expiry |
| `volatile-ttl` | You want the shortest-lived keys evicted first, for example to free memory for long-lived ones |
| `noeviction` | The instance is also a queue or session store; fail loudly instead of evicting |

LFU reacts faster to shifting hot-key sets; LRU is simpler and usually good enough.

## Commands worth knowing

```bash
redis-cli SET user:42 '{"name":"ada"}' EX 300          # set with expiry in seconds
redis-cli SET user:42 '{"name":"ada"}' PX 300000       # set with expiry in milliseconds
redis-cli SET lock:x token NX PX 5000                  # set only if absent, with expiry
redis-cli SETEX user:42 300 '{"name":"ada"}'           # explicit set-with-expiry form
redis-cli GETEX user:42 EX 600                         # get and reset the TTL (Redis 6.2+)
redis-cli GET user:42
redis-cli TTL user:42                                  # -1 no expiry, -2 key missing
redis-cli PERSIST user:42
redis-cli DEL user:42
redis-cli MGET user:1 user:2 user:3                    # one round trip for many keys
redis-cli MSET user:1 a user:2 b                       # one round trip for many writes
redis-cli EXISTS user:42
redis-cli UNLINK user:42                               # free memory in the background
redis-cli INCRBY rate:42 1                             # counters without read-modify-write races

# Pipeline: send many commands in one round trip.
printf 'GET a\nGET b\nGET c\n' | redis-cli --pipe

# Keyspace scan. Never use KEYS in production; it blocks the server.
redis-cli --scan --pattern 'v1:product:*' | head
redis-cli SCAN 0 MATCH 'v1:product:*' COUNT 200

# Server-side work on a value, atomically.
redis-cli EVAL "local v = redis.call('GET', KEYS[1]); if not v then return 0 end; redis.call('DEL', KEYS[1]); return v" 1 user:42
```

`MGET`/`MSET` are the cheapest read/write pattern for cache warming. Pipelining gives the
same round-trip benefit without atomicity guarantees.

## Cache-aside with an in-process LRU

Put the distributed cache behind a small in-process LRU so the hot fraction of reads never
touch the network:

```js
// A bounded LRU prevents unbounded local growth and caps what you must invalidate.
const local = new Map();
const LOCAL_MAX = 1000;

function localGet(key) {
  if (!local.has(key)) return undefined;
  const value = local.get(key);
  local.delete(key);
  local.set(key, value);            // re-insert to make it most-recently-used
  return value;
}

function localSet(key, value) {
  local.set(key, value);
  while (local.size > LOCAL_MAX) {
    const oldest = local.keys().next().value;
    local.delete(oldest);
  }
}

function invalidate(key) {
  local.delete(key);
}
```

Correctness rule: local and distributed caches must be invalidated together. A local cache
that survives an invalidation event is a stale-data bug that only reproduces on one
instance.

## Pub/Sub invalidation

```bash
redis-cli PUBLISH cache:invalidate v1:product:42
redis-cli SUBSCRIBE cache:invalidate
```

```js
// Publisher, after a successful write.
await redis.publish('cache:invalidate', key);

// Subscriber, in every instance.
const sub = redis.duplicate();
await sub.connect();
await sub.subscribe('cache:invalidate', (message, channel) => {
  local.delete(message);
});
```

Pub/Sub is fire-and-forget. Subscribers that are disconnected miss messages entirely, and
a message is not retained for a subscriber that connects later. This makes Pub/Sub a fast
path for freshness, never the only one. Keep a short TTL as the backstop.

If you need delivery guarantees for invalidation, use a stream instead:

```bash
redis-cli XADD invalidations '*' key v1:product:42
redis-cli XREAD BLOCK 5000 STREAMS invalidations '$'
redis-cli XACK invalidations <group> <last-delivered-id>
```

Streams give at-least-once delivery with consumer groups. The cost is that you now have
to handle idempotent replay and consumer lag.

## Rate limiting with cache counters

```bash
# Fixed window: increment and set the expiry only when the window is new.
# (Done in Lua or a transaction so INCR and EXPIRE are atomic together.)
```

```lua
-- KEYS[1] = the rate key, ARGV[1] = window seconds, ARGV[2] = limit
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
if current > tonumber(ARGV[2]) then
  return {0, redis.call('TTL', KEYS[1])}
end
return {1, redis.call('TTL', KEYS[1])}
```

```js
const [allowed, ttl] = await redis.eval(RATE_SCRIPT, {
  keys: [`ratelimit:${userId}`],
  arguments: [String(windowSeconds), String(limit)],
});
if (!allowed) reply(429, { 'Retry-After': String(ttl) });
```

Run this with `redis-cli --eval rate.lua rate.lua , user:42 , 60 , 100` if you want to try
it from the shell. Check `EVAL`/`EVALSHA` in the Redis command docs for your version.

## Serialization

| Format | Good for | Watch out for |
| --- | --- | --- |
| JSON | Interop, debuggability | Slower parse, bigger payloads, loses types |
| MessagePack / CBOR | Compact binary, faster parse | Language-specific, harder to debug |
| Protocol buffers | Strong schema, very compact | Requires matching schemas on both sides |

Do not store a serialized ORM row. Store an explicit DTO with named fields. It survives
schema changes and it is readable in the cache when something goes wrong.

## Serialization of the cache contract

Write down, per cached value: the key format, the payload format and its schema version,
the TTL, the invalidation trigger, and the acceptable staleness. When the payload format
changes, bump the version in the key. This is cheaper than discovering a stale-shape bug
from user reports.

## Verifying before relying on this

Command availability depends on the Redis version (`GETEX` and `GETDEL` require 6.2 or
later). Confirm with `redis-cli --version` and <https://redis.io/commands/>.
