---
name: scalability-patterns
description: Horizontal and vertical scaling architecture covering stateless services, load balancing algorithms, consistent hashing, sticky sessions, read replicas, sharding and partitioning, hot partitions, thundering herds, and graceful degradation. Use when the user asks how to scale a service, choose a load balancing algorithm, shard or partition a database, add read replicas, handle a traffic spike or a hot key, or decide between scaling up and scaling out.
metadata:
  opencode/autoinvoke: true
---

# Scalability Patterns

Scaling is about where the constraint lives. Every system has one dominant constraint at a
time — CPU, a lock, a single row, a connection limit, a queue — and adding instances only
helps if that constraint is per-instance. The first job is finding it; the second is
removing shared state so instances become interchangeable; only then does horizontal
scaling actually work.

## When to use this

- A single service needs to handle more traffic than one machine can.
- You are choosing a load balancing algorithm or a sharding scheme.
- Reads dominate and you are considering replicas.
- One key, one row, or one shard is receiving disproportionate traffic.
- A deploy or restart takes the service down because of in-flight state.
- You need to decide between a bigger machine and more machines.

## Quick start

Make instances interchangeable, then let a balancer spread across them:

```nginx
# nginx upstream: least-connections load balancing with passive health detection.
upstream api {
    least_conn;

    server 10.0.1.11:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.12:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.13:8080 max_fails=3 fail_timeout=30s;

    # Keep idle connections open to upstream so each request does not pay a handshake.
    keepalive 32;
}

server {
    listen 8080;

    location / {
        proxy_pass http://api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```js
// Stateless session handling: the session lives in a shared store, not in the instance.
import { createClient } from 'redis';

const redis = createClient({ url: process.env.REDIS_URL });
await redis.connect();

app.use(async (req, res, next) => {
  const sid = req.get('x-session-id') ?? crypto.randomUUID();
  const key = `session:${sid}`;
  let raw = await redis.get(key);
  if (raw === null) {
    raw = JSON.stringify({ createdAt: Date.now(), user: null });
  }
  const session = JSON.parse(raw);
  req.session = session;
  res.setHeader('x-session-id', sid);
  res.on('finish', () => {
    redis.set(key, JSON.stringify(session), { EX: 3600 });
  });
  next();
});
```

```bash
# Confirm instances really are interchangeable: hit each one and check it has no local state.
for host in 10.0.1.11 10.0.1.12 10.0.1.13; do
  echo "== $host"; curl -sS --max-time 5 "http://$host:8080/healthz"; echo
done
```

## Stateless services and horizontal scaling

An instance is stateless if any request can be served by any instance, using only data
reachable from that request.

State that makes an instance stateful, and where each should go:

| State | Problem if left on the instance | Where it belongs |
| --- | --- | --- |
| In-memory session | Requests fail over to an instance without it | Shared store (Redis, database) or signed, encrypted cookie |
| Local file uploads | Lost on restart or on another instance | Object storage |
| In-process cache | Cold on every deploy and per-instance | Shared cache or rebuilt local cache |
| Cached DNS or config | Stale and inconsistent per instance | Shared config service, refreshed on change |
| Local timers and background jobs | Duplicate or missing work on multiple instances | A single scheduler or a lease |
| Sticky session affinity | Uneven load, loss of failover | Remove the need for affinity |

Stateless is also what makes rolling deploys and autoscaling possible. If an instance can
be destroyed at any moment with no user-visible effect, every scaling and deployment problem
becomes easier.

Test statelessness directly: kill one instance while a client is mid-request and confirm
the request either completes or fails cleanly with a retryable error, never a silent
corruption.

## Load balancing algorithms

| Algorithm | Chooses by | Good when | Bad when |
| --- | --- | --- | --- |
| Round robin | Order | Instances are identical and requests are similar | One instance is slower; requests vary wildly in cost |
| Weighted round robin | Order, with fixed weights | Instances differ in size | Weights are guessed and drift |
| Least connections | Fewest in-flight requests | Request cost and duration vary | Long-lived connections dominate; an instance with slow requests accumulates work |
| Least time (weighted) | Lowest observed response time | Instances differ in latency | Slow instance gets even less traffic, sometimes starving itself of new connections |
| IP hash | Hash of client IP | Caching per client on the instance | NAT collapses many clients into one; rebalancing moves everything at once |
| Consistent hash | Hash of a key to a point on a ring | Session affinity, sharding, or a small number of very hot keys | Slightly more complex; requires ring membership management |

```nginx
# nginx: consistent hashing on a request-derived key. 'consistent' uses ketama hashing.
upstream shards {
    hash $arg_tenant consistent;
    server shard-a.internal:8080;
    server shard-b.internal:8080;
    server shard-c.internal:8080;
}

# nginx: hash a cookie so a session sticks to one upstream.
upstream with_session_affinity {
    ip_hash;
    server app-1.internal:8080;
    server app-2.internal:8080;
}
```

Notes on nginx load balancing: `ip_hash` and `hash` are open source. Sticky sessions via
cookies (`sticky cookie`) are an NGINX Plus feature. Active health checks
(`health_check` in an upstream) are also NGINX Plus; open source nginx uses passive
detection through `max_fails` and `fail_timeout`. Confirm feature availability for your
build at <https://nginx.org/en/docs/>.

## Sticky sessions

| Property | Affinity by cookie | Affinity by IP |
| --- | --- | --- |
| Survives NAT and mobile networks | Yes | No |
| Survives a client changing networks | Usually | No |
| Load distribution | Good | Poor, many clients share an address |
| Rehash on membership change | Only that session moves | Every client on an affected hash moves |
| Spoofable | Yes, so only trust cookies you signed | No |

Costs of sticky sessions: uneven load, degraded failover, and the requirement that the
upstream keep session state — which conflicts with statelessness. If you need affinity for
a cache, prefer hashing a *key* rather than the session, and prefer storing sessions
outside the instance so affinity becomes an optimization rather than a requirement.

If you cannot remove affinity, at least make it graceful: when an instance drains, return a
redirect or a `Connection: close` plus a `Set-Cookie` that points the client at another
instance, so rebalancing is gradual rather than a cliff.

## Read replicas

Replicas serve reads from a copy, offloading the primary. The tradeoffs are real:

- **Replication lag.** A read immediately after a write may hit a replica and miss the
  write. Read-your-write consistency therefore requires routing that read to the primary,
  or waiting for the replica to catch up.
- **Lag spikes.** A long transaction or a large write on the primary stalls replication for
  everything behind it.
- **Failover.** Automatic failover needs a control plane and a fencing story; without one,
  promotion can produce split brain.
- **Reads that are not actually read-only.** A replica cannot enforce this for you.

```sql
-- PostgreSQL: which replica is furthest behind and streaming.
SELECT client_addr, state, write_lag, flush_lag, replay_lag, sync_state
  FROM pg_stat_replication;

SELECT application_name, client_addr, state, sent_lsn, replay_lsn,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind
  FROM pg_stat_replication;
```

```sql
-- Route read-your-write reads to the primary. The pattern is the important part:
-- after any write to an entity, pin subsequent reads of that entity to the primary
-- for a short window.
```

Use replicas for read-heavy, tolerable-staleness workloads: list pages, reports, analytics,
cache warming. Do not use them to scale write throughput; they do not help there.

## Write throughput: sharding and partitioning

Two different tools, often confused:

- **Partitioning** splits one logical table into physical pieces inside a single database.
  Queries that prune partitions can get faster, and maintenance becomes per-partition. It
  does not increase write capacity beyond the instance, because writes still go to one
  database.
- **Sharding** splits data across multiple databases. It does increase write capacity, at
  the cost of cross-shard queries, cross-shard transactions, and rebalancing.

```sql
-- PostgreSQL declarative partitioning by range. Primary key must include the partition key.
CREATE TABLE events (
  id         bigint NOT NULL,
  occurred_at timestamptz NOT NULL,
  kind       text NOT NULL,
  payload    jsonb,
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE events_2026_09 PARTITION OF events
  FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE events_2026_10 PARTITION OF events
  FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
```

Choose a shard key by asking: does every write have a value for it, and does that value
route related writes to the same shard?

| Key choice | Routing | Failure mode |
| --- | --- | --- |
| Tenant or account id | All of a tenant's writes colocated | One large tenant becomes one hot shard |
| User id | Even distribution, no tenant-level locality | Cross-shard queries for admin views |
| Region | Latency locality | Unbalanced regions |
| Time | Natural partitioning, easy archival | Current period is always the hottest |
| Hash of the id | Even distribution, no hotspot from sequential ids | No locality at all; every query becomes a single-shard query by construction |

Hash-based sharding spreads load evenly and removes the sequential-id hotspot, at the cost
of range queries. Range-based sharding keeps range queries fast but can create hot spots
when ids or timestamps are monotonically increasing.

Moving a shard's data between servers is a rehash. Consistent hashing limits how much
moves; otherwise a topology change re-shuffles nearly everything.

## Hot spots and thundering herds

A hot spot is disproportionate traffic on one key, row, shard, or instance. Causes and
responses:

| Hot spot | Symptom | Response |
| --- | --- | --- |
| Single popular cache key | Origin CPU spikes in a burst pattern | Single-flight, request coalescing, stale-while-revalidate |
| Monotonically increasing primary key | Last-page insert contention | Hash or UUID keys, partition by time, absorb into a log |
| One tenant far larger than others | One shard saturated | Dedicated shard or per-tenant resources |
| Queue with a periodic producer | Bursts of simultaneous work | Randomized or jittered schedules |
| Config change fans out to every instance | All instances reload at once | Randomized reload intervals |
| Cache expiry of a bulk-populated key set | Periodic origin spike | TTL jitter |
| Reconnecting clients after an outage | Synchronized reconnect storm | Exponential backoff with jitter, and server-side jitter |

```js
// Break up synchronized work with a random offset.
function jitteredInterval(baseMs, spread = 0.25) {
  const delta = baseMs * spread;
  return baseMs - delta + Math.random() * 2 * delta;
}
```

A thundering herd is the same phenomenon at the client side: many clients reacting to the
same signal at the same instant. The fix is always to desynchronize them.

## Graceful degradation

A system that degrades predictably is more useful than one that falls over.

Ordered response, cheapest first:

1. **Serve stale data** for read paths that can tolerate it.
2. **Reduce optional work.** Skip enrichment, recommendation, notification side effects.
3. **Reduce quality.** Smaller candidate sets, cheaper models, coarser results.
4. **Restrict to writes** and stop serving reads.
5. **Shed** non-essential traffic entirely.
6. **Serve a static error page** rather than a hanging request.

```js
// A single load-shedding switch the whole app can read.
const mode = { state: 'normal', since: Date.now() };

function budgetFor(priority) {
  return {
    essential: 1.0,
    standard: 0.6,
    optional: 0.0,      // off during overload
  }[priority];
}

function shouldServe(priority) {
  return Math.random() < budgetFor(priority);
}
```

Load shedding must be *observable*. A shed request is a served error, and it should be
counted and alerted on so you know degradation is happening rather than inferring it from
complaints.

## Scale up versus scale out

| | Scale up (vertical) | Scale out (horizontal) |
| --- | --- | --- |
| Complexity | Simple, no code change | Requires statelessness and coordination |
| Ceiling | The largest available machine | Practically unbounded, but bounded by the next shared resource |
| Failure impact | One machine fails, whole service fails | One instance fails, service continues |
| Cost curve | Often steepens sharply at the top | Roughly linear; smaller machines are often cheaper per unit |
| Data locality | Single machine, no distribution | Data must be partitioned and shared |
| Good for | Databases, caches, stateful systems, fast relief | Stateless services, bursty traffic, high availability |

Vertical scaling is the right first move for a database, because re-sharding a database is
a project and buying memory is an afternoon. It is the wrong first move for a stateless
HTTP service, because it buys one machine's worth of failover and one machine's ceiling.

The real limit on horizontal scaling is the next shared resource: the database primary, a
single Redis node, one lock service, one broker leader. If you scale out until that
resource saturates, you have moved the bottleneck, not removed it. Follow the bottleneck
before you scale again.

## Anti-entropy and capacity planning

- **Measure per instance.** Load, memory, and latency per instance reveal imbalance faster
  than aggregate numbers.
- **Scale on saturation, not on CPU alone.** Queue depth and concurrency limits rise
  before CPU does, because CPU is the last thing to saturate in a queueing system.
- **Scale on the constraint you can act on.** Autoscaling a service whose bottleneck is
  the database just makes the database worse faster.
- **Load test the balancer, not just the backend.** A balancer with a health check interval
  longer than its tolerance will route to dead instances after the failure, not before.

## Checklist

- [ ] Instances are stateless; killing one mid-request is safe.
- [ ] Sessions, files, and background jobs live outside the instance.
- [ ] Load balancing algorithm matches request cost variance.
- [ ] Health checks exist and are tighter than the timeout they protect.
- [ ] Upstream keepalive is enabled and tuned.
- [ ] Sticky sessions, if any, are documented with their failure behavior.
- [ ] Read-your-write consistency is handled explicitly if replicas exist.
- [ ] Shard key routes related writes together and spreads load evenly.
- [ ] Hot keys have coalescing or stale-while-revalidate protection.
- [ ] Any periodic or fan-out work is jittered.
- [ ] A degradation ladder exists and shedding is observable.
- [ ] The current bottleneck is named and monitored, not assumed.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Adding instances does nothing | Shared bottleneck (database, lock, one shard) | Find the shared resource and scale or shard that |
| Deploy causes dropped requests | Instances hold local state | Externalize state; drain connections on shutdown |
| One instance always hotter | Algorithm ignores cost variance | Use least connections or least time |
| All clients hit one instance | `ip_hash` behind a NAT or proxy | Hash a key, not the source IP |
| Stale reads after write | Read served by a lagging replica | Route post-write reads to the primary |
| Shard grew too large | Poor shard key or no rebalancing | Split the shard; consistent hashing limits reshuffling |
| Periodic latency spikes | Synchronized work across instances | Jitter every periodic timer |
| Traffic spike causes a collapse | No shedding; retries amplify | Add admission control before scaling capacity |
| Autoscale never triggers | Scaling on a signal that saturates last | Scale on queue depth or concurrency limits |
| Cache expiry storm | Bulk-populated keys share a TTL | Randomize TTLs |

## Anti-patterns

- **Sessions in instance memory.** The single most common blocker to horizontal scaling.
- **A background timer in every instance.** Every instance runs the job.
- **Scaling out before externalizing state.** More instances, same shared resource, more
  load on it.
- **Shard key chosen for query convenience** while writes are the actual bottleneck.
- **Uniform sharding on a monotonically increasing key**, which guarantees that the newest
  range is always hottest.
- **Sticky sessions used to hide a shared cache.** The cache is the scalable design.
- **Retries without a budget**, so a scaling action amplifies the overload it was meant to
  relieve.
- **Autoscaling on CPU.** CPU is the last constraint to saturate; by then the queue is
  already deep.
- **Rebalancing by resharding everything.** Without consistent hashing, every topology
  change is a full data migration.
- **Assume replicas scale writes.** They do not.

## References

- nginx documentation, upstream load balancing
  - <https://nginx.org/en/docs/http/load_balancing.html>
  - <https://nginx.org/en/docs/http/ngx_http_upstream_module.html>
- PostgreSQL documentation, partitioning and streaming replication
  - <https://www.postgresql.org/docs/current/ddl-partitioning.html>
  - <https://www.postgresql.org/docs/current/warm-standby.html>
- Amazon Builders' Library, "Avoiding insurmountable queue backlogs" and "Timeouts,
  retries and backoff with jitter"
  - <https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/>
  - <https://aws.amazon.com/builders-library/avoiding-insurmountable-queue-backlogs/>
- Google SRE book, "Load Balancing", "Cascading Failures", "Handling Overload"
  - <https://sre.google/sre-book/load-balancing/>
  - <https://sre.google/sre-book/cascading-failures/>
- Google SRE book, "Shard", "Reliable Product Launches at Google"
  - <https://sre.google/sre-book/shard-reliable-product-launches/>
- Consistent hashing paper by Karger et al.
  - <https://dl.acm.org/doi/10.1145/276675.276678>
- Little's Law
  - <https://sre.google/sre-book/littles-law-for-performance-testing/>

## Deeper detail

- `references/load-balancing-and-affinity.md` — algorithm details, health checking,
  draining, consistent hashing, and layer-4 versus layer-7 balancing.
- `references/data-scaling.md` — sharding schemes, rebalancing, replica lag, caching
  interactions, and hot-key mitigation patterns.
