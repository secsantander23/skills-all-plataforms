# Connection Pools

## The sizing constraint you must respect first

```
total_connections_to_downstream = (number_of_app_instances) x (pool_max_per_instance)
```

That total must be comfortably below the downstream's limit, with headroom for
administration, replication, and jobs. For PostgreSQL, `max_connections` is the number you
must respect; check `SHOW max_connections;`. Ten app instances with a pool of 50 each is 500
connections, which will exhaust most defaults.

If the arithmetic does not fit, the answer is a pooler in front of the database
(PgBouncer in transaction mode) or connection multiplexing in the driver, not a larger pool.

## Why pools exist

Connection establishment is expensive relative to a simple query:

- DNS lookup and TCP handshake.
- TLS handshake, which is a multi-round-trip asymmetric-cryptography operation.
- Authentication.
- Database-side session setup, plan caches, and any per-connection state.

A pool amortizes that across many operations, and it caps concurrent sessions. Both effects
matter: without a cap, concurrency that helps throughput in your app becomes a queue inside
the database.

## Choosing a max size

There is no universal number. The workable procedure:

1. Start low, for example 5 to 10 per instance for a web workload.
2. Load test at that size and record throughput and p95/p99 latency.
3. Increase and re-measure. Look for the point where latency starts rising for a small
   throughput gain. That point is near the downstream's own saturation.
4. Stop there. Beyond it you are converting your queue into the database's queue.

Database-side signals that you have gone too far: `wait_event_type = 'Lock'` appears,
CPU climbs without throughput gain, and lock-wait or I/O-wait time grows. Cross-check
with `pg_stat_activity` counts by state.

## Configuration by stack

```js
// node-postgres (pg). Confirm option names against your driver's version.
import pg from 'pg';
const pool = new pg.Pool({
  host: process.env.PGHOST,
  database: process.env.PGDATABASE,
  user: process.env.PGUSER,
  password: process.env.PGPASSWORD,
  max: 10,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
  statement_timeout: 15_000,
});
pool.on('error', (err) => console.error('pg idle client error:', err.message));
await pool.end();   // required for graceful shutdown
```

```python
# psycopg (v3) connection pool.
from psycopg_pool import ConnectionPool

with ConnectionPool(
    conninfo="postgresql://db.internal/app",
    min_size=2,
    max_size=10,
    timeout=5.0,          # seconds to wait for a free connection
    max_waiting=0,        # 0 = unlimited waiting; set a bound to shed instead
    kwargs={"connect_timeout": 5, "options": "-c statement_timeout=15000"},
    open=False,
) as pool:
    pool.open()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
```

```yaml
# HikariCP (JVM), commonly used via Spring Boot.
spring:
  datasource:
    url: jdbc:postgresql://db.internal/app
    hikari:
      maximum-pool-size: 10
      minimum-idle: 2
      connection-timeout: 5000
      idle-timeout: 300000
      max-lifetime: 1200000
      pool-name: app-pool
```

```sql
-- MySQL/MariaDB: the server-side ceiling you must stay under.
SHOW VARIABLES LIKE 'max_connections';
SHOW STATUS LIKE 'Threads_connected';
```

Confirm property names for your driver version rather than assuming; pool option names
differ between libraries even when the concepts match.

## Timeouts, in order of scope

| Timeout | Protects against | Symptom when missing |
| --- | --- | --- |
| TCP connect timeout | Unreachable hosts burning a worker slot | Hangs on network partition |
| TLS handshake timeout | Handshakes that never complete | Slot held indefinitely |
| Pool acquire timeout | Pool exhaustion | Requests hang forever with no error |
| Query / statement timeout | A single slow query holding a slot | One slow query removes capacity |
| Transaction idle-in-transaction timeout | A transaction left open, holding locks | Locks never released |
| Request deadline | The whole call tree outliving the caller | Wasted work nobody will read |

Set the innermost timeout you can, not only the outermost. A single slow query with no
statement timeout will hold a pool slot for as long as it runs, and at a few of those the
pool is gone.

PostgreSQL statement and lock timeouts are configured server-side and can also be set per
session:

```sql
SET statement_timeout = '15s';
SET lock_timeout = '3s';
SET idle_in_transaction_session_timeout = '30s';
```

## Diagnosing pool exhaustion

```sql
-- PostgreSQL: how many sessions, and in what state.
SELECT state, count(*) FROM pg_stat_activity GROUP BY state;
SELECT max_conn FROM pg_settings WHERE name = 'max_connections';
SELECT application_name, count(*)
  FROM pg_stat_activity
 GROUP BY 1 ORDER BY 2 DESC;

-- Longest-running queries right now.
SELECT pid, now() - query_start AS runtime, state, wait_event_type, wait_event, left(query, 120)
  FROM pg_stat_activity
 WHERE state <> 'idle'
 ORDER BY runtime DESC LIMIT 20;
```

```js
// In your app: expose pool saturation as a metric, not just a log line.
const total = pool.totalCount;
const idle = pool.idleCount;
const waiting = pool.waitingCount;
metrics.gauge('db_pool_connections_total', { state: 'total' }, total);
metrics.gauge('db_pool_connections_total', { state: 'idle' }, idle);
metrics.gauge('db_pool_waiters', {}, waiting);
```

If `waitingCount` is persistently above zero, your pool is the bottleneck or is leaking.
If `idleCount` is always near zero while CPU is low, requests are waiting for a connection,
not for work.

### Leaks

A leak is a connection that is checked out and never returned. Symptoms: pool size stays
at max, `waitingCount` climbs, and the database session count equals the pool total across
all instances.

```js
// Every path must return the client, including errors and cancellation.
let client;
try {
  client = await pool.connect();
  await client.query('BEGIN');
  await client.query('SELECT 1');
  await client.query('COMMIT');
} catch (err) {
  if (client) await client.query('ROLLBACK').catch(() => {});
  throw err;
} finally {
  client?.release();          // release in finally, unconditionally
}
```

Common leak sources: `throw` between connect and release, returning early inside a loop,
a callback that never fires, an aborted request that skips cleanup, and a connection held
across a long `await` on something slow.

## Poolers

A pooler (PgBouncer, ProxySQL, MaxScale, cloud-native poolers) sits between your app and
the database and recycles database sessions across many client connections. Two modes
matter:

- **Session pooling.** One client connection is bound to one server connection for the
  whole session. Safe with all client features; saves less.
- **Transaction pooling.** A server connection is held only for the duration of a
  transaction. Saves a great deal, but breaks session-scoped state: `SET` without
  `SET LOCAL`, temporary tables, `LISTEN`, advisory locks held across transactions,
  session-level `PREPARE`, and cursors that span transactions.

Rule: if you cannot enumerate every session-scoped feature your application uses, stay on
session pooling. Verify behavior against your driver's documentation and the pooler's own
docs, since support varies.

```ini
# PgBouncer: transaction pooling for PostgreSQL.
[databases]
app = host=db.internal port=5432 dbname=app

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 20
reserve_pool_size = 5
server_reset_query = DISCARD ALL
```

Because PgBouncer adds a hop, it is itself a single point of failure. Run more than one
instance, or place it behind a health-checked address.

## HTTP and RPC connection pools

The same reasoning applies outside databases. HTTP keep-alive removes the TCP and TLS
cost per request; a client pool bounds how many sockets you hold against a downstream.
Connection: keep-alive with an idle timeout that is shorter than the downstream's own idle
closing timeout avoids races where the server closes a socket exactly as you reuse it.

For HTTP/2 and HTTP/3, the multiplexing model changes the arithmetic: one connection
carries many streams, so per-connection limits bind less often and per-stream flow control
becomes the backpressure mechanism. See the `reverse-proxies-load-balancers` and
`websocket-realtime` skills.

## Verifying before relying on this

Pool option names and defaults change across driver versions. Confirm against the
documentation for your specific client library and database version rather than copying
option names between stacks.
