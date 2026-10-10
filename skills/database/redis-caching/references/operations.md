# Redis Operations Reference

## Key design

Key design is a schema decision. Get it right and everything else is easy.

```text
Good:  user:{1001}:profile
       tenant:acme:order:88213
       cache:v3:product:sku-1234
       rl:user:1001

Bad:   user1001profile
       USR_1001_PROFILE_2024
       "SELECT * FROM users WHERE id = 1001"
```

Rules:

- Colons as separators, lowercase, no spaces, bounded length.
- Put the entity first so related keys share a prefix and can be scanned by pattern.
- Include a version segment when the value shape changes, so a deploy does not read old-format
  values.
- Never build a key from unbounded user input. Length limits and keyspace scans are real
  problems.
- If you use Cluster, decide now whether you need multi-key atomicity. If you do, use hash
  tags: `{1001}` forces `user:{1001}:profile` and `user:{1001}:settings` into the same slot.

## Memory diagnosis

```text
INFO memory
used_memory                 bytes used by Redis
used_memory_rss             resident set size; compare against used_memory for fragmentation
used_memory_peak            high water mark since start
mem_fragmentation_ratio     allocator fragmentation indicator
maxmemory                   configured ceiling, 0 means unlimited
evicted_keys                how many keys the policy has evicted (should be 0 if not over budget)
expired_keys                how many keys expired
keyspace_hits / keyspace_misses   hit ratio; a low ratio means the cache is too small or the
                                  keys are too scattered
```

```text
MEMORY USAGE mykey                    -- bytes for one key
MEMORY DOCTOR                         -- server's own analysis and advice
OBJECT ENCODING mykey                 -- listpack, hashtable, intset, embstr, skiplist, ziplist
DBSIZE
SCAN 0 COUNT 100                      -- iterate keys without blocking
redis-cli --bigkeys                   -- find large keys, by type
redis-cli --hotkeys                   -- requires an LFU maxmemory-policy
```

Interpreting memory pressure:

- `used_memory` close to `maxmemory` with rising `evicted_keys`: cache is too small or the
  value size is wrong. Check `MEMORY USAGE` on a sample.
- `used_memory_rss` far above `used_memory`: fragmentation. Restarting reclaims it; sizing
  `maxmemory` below physical RAM also helps.
- `expired_keys` far exceeding `keyspace_hits`: you are storing keys nobody reads.

Encoding matters at scale. Compact encodings (small lists, small hashes) are automatic; forcing
a plain encoding for a small collection wastes memory. Do not fight the encoder.

## Latency diagnosis

```text
SLOWLOG GET 20                 -- commands slower than slowlog-log-slower-than microseconds
LATENCY DOCTOR                 -- summary of recorded latency events
LATENCY HISTORY <event>        -- per-event history, if latency monitoring is enabled
INFO commandstats              -- per-command call counts, usec, usec/call
INFO latencystats              -- per-percentile latency distributions, when enabled
CLIENT LIST                    -- who is connected
INFO clients                   -- connected_clients, blocked_clients
```

Reading `SLOWLOG`: it stores command name, arguments (with configurable redaction), execution
time, client address, and client name. It does not store sub-command timings, so a slow `EVAL`
tells you the script is slow, not which line.

Common causes of latency, in rough order of frequency:

1. A large `KEYS` or an unbounded `SMEMBERS`/`LRANGE`/`HGETALL` on a big collection, blocking
   the single-threaded server.
2. Persistence: an RDB snapshot fork, or AOF rewrite, competing for CPU and memory bandwidth.
3. Replication: a slow replica backlog, or the primary blocking on a replica.
4. CPU saturation from too many clients or from Lua scripts.
5. Memory swapping, when `maxmemory` is set at or near physical RAM.
6. Network: too many round trips from a chatty client.

## Persistence configuration

```text
CONFIG GET save                   -- RDB snapshot schedule (server default and cron-like entries)
CONFIG GET appendonly             -- AOF enabled
CONFIG GET appendfsync            -- fsync policy; the trade-off is durability vs latency
BGSAVE                          -- take a snapshot now
BGREWRITEAOF                    -- compact the AOF now
LASTSAVE                        -- timestamp of the last successful snapshot
INFO persistence                 -- status, errors, and current operation
```

Rules of thumb:

- `appendfsync always` is the most durable and the slowest. `everysec` is the usual compromise.
  The default may be `no` on some platforms, which means the OS decides, which can lose a second
  of writes on a host crash.
- A snapshot or rewrite forks the process; the memory cost scales with dataset size because of
  copy-on-write. Large datasets need headroom above `maxmemory`.
- Verify persistence actually runs. A server that cannot fork a snapshot logs an error and
  keeps running without one.

## Replication and failover

```text
INFO replication
role                             master / slave
connected_slaves
slaveN:state                     online / send_heartbeat
slaveN:lag                       seconds behind
master_repl_offset

On a replica:
REPLICAOF host port               -- start replicating
REPLICAOF NO ONE                  -- become a primary
WAIT <count> <timeout-ms>         -- block until N replicas acknowledge
```

`WAIT` after a write gives you a stronger durability guarantee than an ordinary acknowledged
write, at the cost of latency proportional to the timeout. It is the standard building block
for "I will not lose this write".

Guard against a primary with no usable replicas:

```text
min-replicas-to-write <count>
min-replicas-max-lag <seconds>
```

With these set, writes are rejected while replicas are not sufficiently connected or too far
behind, which trades availability for durability. Verify current values before assuming which
trade you are making.

Sentinel provides monitoring and automatic failover. It requires at least three Sentinel
processes by convention so the majority is not a single point of failure. Read the Sentinel
documentation for the current configuration parameters.

## Cluster facts

- Keys map to a fixed number of hash slots by CRC16 of the key (and, when present, the hash tag
  in braces).
- Multi-key commands and multi-key transactions only work within one slot.
- A key that is too large, or a slot that is too hot, is a design problem. Hash tags spread
  related keys when you want them together; they concentrate them in one slot.
- Resharding moves slots, not data, between nodes.
- A client that does not follow redirections gets `MOVED` or `ASK` errors. Use a client with
  cluster support.

## Safe operational practices

- Never run `KEYS *` in production. It blocks the server. Use `SCAN` with a cursor.
- Never use `FLUSHALL` outside a controlled test. `FLUSHDB ASYNC` exists but is still
  destructive.
- Use `UNLINK` instead of `DEL` when freeing large collections. `UNLINK` frees the memory on a
  background thread.
- Set `client-output-buffer-limit` and keep per-client output buffers bounded, or one slow
  client can stall the server.
- Use `SLOWLOG` redaction for values that must not be logged.
- Prefer TLS and authentication on any network path that is not fully trusted.
- Monitor: `used_memory`, `evicted_keys`, `instantaneous_ops_per_sec`, `connected_clients`,
  `keyspace_hits/misses`, replication lag, persistence errors, and `latest_fork_usec`.
