# Data Scaling

## Choosing between partitioning, replicas, and shards

| You need | Use | Because |
| --- | --- | --- |
| Faster queries that prune by date or region | Partitioning | Data is still one database; pruning is cheaper |
| Simpler maintenance (drop old data fast) | Partitioning | `DROP PARTITION` is metadata-only |
| More read capacity | Replicas | Offloads reads with a lag caveat |
| More write capacity | Sharding | Only sharding adds write throughput |
| Isolation between tenants | Sharding (per tenant) | Failure and load are contained |
| To avoid a single point of failure | Replicas plus failover | Replicas provide a copy to promote |

Do not shard because the table is large. A large table in one well-indexed database is
usually cheaper than the operational cost of a distributed one.

## Shard key selection

Procedure:

1. List the queries that matter most by weight.
2. For each, identify the routing key it can use.
3. Check that every write has a value for that key.
4. Check that the key's value distribution is even enough. If a key is a tenant id and one
   tenant is much larger, size shards by tenant size, not by count.
5. Check that cross-shard queries are rare and acceptable. If the top query needs data from
   every shard, sharding has made it worse.
6. Check rebalancing cost: how much data moves when one shard is split.

| Key | Distribution | Locality | Cross-shard queries |
| --- | --- | --- | --- |
| Tenant id | Skewed by tenant size | Whole tenant on one shard | Rare for tenant-scoped queries |
| User id | Usually even | Per-user history together | Common for admin and analytics views |
| Hash of id | Very even | None | Any range or sort query |
| Sequential id | Very uneven, newest hottest | Ordered range scans on primary | None |
| Region | Skewed by population | Latency locality | Global queries |
| Time | Current period hottest | Easy archival | Recent data always hot |

If you need both even distribution and locality, hash a composite key that encodes the
locality dimension, for example `hash(tenant_id, bucket)` where `bucket` is derived from a
time window.

## Read-your-write consistency with replicas

Replicas apply changes asynchronously, so a read after a write can return stale data.
Options, cheapest first:

1. **Route the post-write read to the primary.** The client or the application tracks that
   a given entity was just written and pins reads of that entity to the primary for a short
   window.
2. **Session affinity to the primary.** Every read in the same session goes to the primary.
   Simple, wastes replica capacity.
3. **Wait for the replica to catch up.** Compare the replica's replay LSN (PostgreSQL) or
   the GTID position to the write's position before serving.
4. **Accept staleness and design the UI for it.** Legitimate for feeds and dashboards, not
   for anything where a user expects to see their own change.

```js
// Postgres-style position check before serving a read-your-write read.
async function waitForReplica(replicaSql, targetLsn, { timeoutMs = 2000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const [{ replay_lsn }] = await replicaSql.query('SELECT replay_lsn FROM pg_stat_replication LIMIT 1');
    if (replay_lsn >= targetLsn) return true;
    if (Date.now() > deadline) return false;
    await new Promise((r) => setTimeout(r, 50));
  }
}
```

Also watch for the long-running transaction problem: a transaction that stays open on the
primary prevents the replication slot from advancing, so lag grows without any write
volume. Alert on replication lag age and on idle-in-transaction sessions.

## Hot keys

Detection first. A hot key shows up as uneven load: one instance hot in front of a hash, one
shard's CPU or I/O far above the rest, one row with far more accesses than its peers.

```sql
-- PostgreSQL: which relations are taking the most work.
SELECT relname, seq_scan, idx_scan, n_tup_upd, n_tup_ins
  FROM pg_stat_user_tables
 ORDER BY n_tup_upd + n_tup_ins DESC
 LIMIT 20;

-- Table/index sizes.
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total
  FROM pg_catalog.pg_statio_user_tables
 ORDER BY pg_total_relation_size(relid) DESC
 LIMIT 20;
```

Mitigations:

| Strategy | Mechanism | Cost |
| --- | --- | --- |
| Cache with single-flight | One load per key, everyone else waits | Brief latency spike for the first request |
| Stale-while-revalidate | Serve stale, refresh one in background | Bounded staleness |
| Key replication | Store the same key on N stores, read from any | Invalidation complexity; write amplification |
| Key splitting | Split one logical key into N sub-keys by a dimension | Requires the dimension to be in the query |
| Request coalescing at the CDN | Collapse concurrent requests for the same URL | Only helps cacheable GETs |
| Precompute | Move the work off the request path entirely | Staleness; a pipeline to maintain |
| Replicate the shard | Move the hot shard's data to more servers | Rebalancing work |

Key replication deserves a caution: with N copies, an invalidation must reach all N or they
disagree. It is only safe when the value is read-mostly and updates are rare.

## Rebalancing

Rebalancing moves data between shards. Options:

| Method | Approach | Fits |
| --- | --- | --- |
| Split a shard | Add a shard, move half the key range, update routing | Range or hash sharding with known boundaries |
| Consistent hash remap | Add nodes to the ring; only their segments move | Large clusters with uneven growth |
| Double read and backfill | Read from old and new, write to both, then cut over | Migrating an entire dataset |
| Resharding in place | Move data between existing shards | Capacity balancing without topology change |
| Partition split | Split a time or range partition | Time-series data |

A safe cutover sequence:

1. Deploy code that knows about the new layout and reads from the old one.
2. Enable dual writes.
3. Backfill historical data, throttled so it does not starve production traffic.
4. Verify counts and checksums per shard.
5. Flip reads to the new layout behind a flag.
6. Keep dual writes until you have verified under real traffic.
7. Remove the old path only after the verification window, not immediately.

Every step must be reversible. A rebalance you cannot roll back is an outage waiting for a
bad day.

## Sharding and caches together

Two traps:

- **Scattered keys.** If every cache key includes a globally unique id, the cache is a flat
  keyspace that a sharded application can no longer partition. Use the shard id in the key
  so related entries land together.
- **Cross-shard cache invalidation.** A write to shard A that changes data on shard B needs
  an invalidation message to B's cache. Publish an event; do not make the write path call
  every shard synchronously.

## Multi-tenant isolation

| Model | Isolation | Cost | Use when |
| --- | --- | --- | --- |
| Shared schema, tenant column | Row-level | Lowest | Many small tenants |
| Schema per tenant | Schema-level | Migration cost per tenant | Few tenants with distinct needs |
| Database per tenant | Full | Highest | Large tenants or strict isolation needs |
| Shard key = tenant | Partial | Medium | A few very large tenants |

Row-level isolation needs `tenant_id` in every key and a defense against forgetting it. A
query builder that injects the tenant predicate is far safer than discipline. Add a
composite index that leads with `tenant_id`, and consider a row-level security policy as a
backstop rather than relying on application code alone.

## When to stop scaling the data tier

Scaling out the application while the database is the bottleneck makes things worse. The
signals that you have reached the practical end of the single-database design:

- Write throughput plateaus while CPU is not the limit.
- Lock waits and I/O waits dominate.
- Table or index size makes maintenance operations (vacuum, index rebuild) disruptive.
- Backup and restore no longer fit in your maintenance window.
- Schema changes require downtime because of the size of the table.

At that point the choices are: a purpose-built distributed database, deliberate
application-level sharding, or accepting a product constraint on data volume. Choosing a
distributed database because it is fashionable is not one of the options.

## Verifying before relying on this

Sharding syntax, replication metadata, and management tooling are database-specific and
version-specific. Confirm against the documentation for your exact database version:
<https://www.postgresql.org/docs/current/> for PostgreSQL.
