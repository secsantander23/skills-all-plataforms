---
name: PostgreSQL Performance Tuning
description: Covers reading EXPLAIN and EXPLAIN ANALYZE output, choosing between B-tree, GIN, GiST and BRIN indexes, composite index column order, covering and partial indexes, planner statistics and autovacuum, connection pooling, pg_stat_statements, connection-level settings, and the usual causes of slow queries. Use when the user asks why a PostgreSQL query is slow, when adding or reviewing indexes, when reading a query plan, when tuning work_mem or autovacuum, when setting up pg_stat_statements or auto_explain, or when diagnosing lock waits and bloat.
metadata:
  opencode/autoinvoke: true
---

# PostgreSQL Performance Tuning

PostgreSQL is a cost-based optimizer with MVCC, autovacuum, and a statistics-driven
planner. Almost every performance problem is one of four things: no usable index, stale
statistics, the wrong plan shape, or a resource setting that starves one node of the plan.
This skill teaches you to find which one applies before changing anything.

## When to use this

- A query is slow and someone pasted an `EXPLAIN` or `EXPLAIN ANALYZE` plan.
- Someone asks which index type to use, or whether an existing index is used at all.
- Table bloat, growing `n_dead_tup`, vacuum never finishing, or autovacuum falling behind.
- Connection errors, "too many clients", or a server pinned at high CPU with many idle clients.
- You need per-statement timing to find the top offenders (`pg_stat_statements`).
- You need slow statements logged without restarting the application (`auto_explain`).
- A migration or batch job takes an exclusive lock and stalls production.

## Quick start

Find the expensive statements, then explain the worst one with real I/O counters.

```sql
-- 1. Enable once per database (needs the shared library loaded; see References).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 2. Top statements by total time spent.
SELECT queryid,
       calls,
       total_exec_time,
       mean_exec_time,
       rows,
       shared_blks_hit,
       shared_blks_read,
       query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;

-- 3. Explain the worst offender with execution and buffer statistics.
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, o.created_at, o.total_cents
FROM orders o
WHERE o.customer_id = 42
  AND o.created_at >= DATE '2024-01-01'
ORDER BY o.created_at DESC
LIMIT 50;
```

`ANALYZE` actually executes the statement. Never run it on `INSERT`, `UPDATE`, `DELETE`,
or any statement with side effects inside a transaction you intend to roll back. Wrap it in
a transaction and roll back when the statement is a writer.

## Reading a query plan

Read plans top-down, then bottom-up for cost. Focus on these lines:

```
Limit  (cost=0.42..8.31 rows=50 width=24) (actual time=0.031..1.204 rows=50 loops=1)
  ->  Index Scan using orders_customer_created_idx on orders o
        (cost=0.42..812.44 rows=20481 width=24) (actual time=0.028..1.180 rows=50 loops=1)
        Index Cond: (customer_id = 42)
        Buffers: shared hit=9
Planning Time: 0.152 ms
Execution Time: 1.239 ms
```

| Line | What to check |
| --- | --- |
| `rows=` vs `actual rows=` | A gap of orders of magnitude means stale or insufficient statistics. Fix statistics, not the query. |
| `loops=` | A nested loop with a large `loops` count multiplies the inner cost. `loops` above the inner `actual time` explains why it is slow. |
| `Buffers: shared hit/read` | `read` is I/O, not necessarily physical disk: the OS page cache can absorb it. Correlate with `EXPLAIN (ANALYZE, BUFFERS)` plus `track_io_timing` and system metrics. |
| `Sort Method: external merge Disk: ...` | The sort or hash spilled to disk. That node needed more `work_mem`. |
| `Hash Batches: 4` | The hash join was batched. Same cause. |
| `Heap Fetch: N` | An index-only scan had to visit the heap for many rows, usually because the visibility map is stale (vacuum has not run). |
| `Rows Removed by Filter: ...` | The index is being scanned and then filtered. The index does not cover the predicate. |
| `Planning Time` >> `Execution Time` | Many joins, a correlated subquery, or a large `IN` list. Consider rewriting or pre-computing. |

Useful `EXPLAIN` options. `ANALYZE`, `BUFFERS`, `VERBOSE` and `FORMAT JSON` are widely
available; `SETTINGS` and `WAL` exist in recent PostgreSQL releases, so confirm against your
server's documentation before relying on them.

```sql
EXPLAIN (COSTS OFF) SELECT ...;                 -- shape only, easiest to read
EXPLAIN (ANALYZE, BUFFERS, VERBOSE) SELECT ...; -- the workhorse
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT ...; -- for tooling and plan diffing
```

## Choosing an index type

| Type | Fits | Notes |
| --- | --- | --- |
| B-tree | Equality, ranges, `ORDER BY`, prefix `LIKE 'x%'` | Default. Supports `INCLUDE` covering columns, expression indexes, partial indexes, and multicolumn keys. |
| Hash | Equality only | Rarely wins over B-tree because B-tree also handles equality and never regresses to a scan. Use when a column has many equal-length keys. |
| GIN | Arrays, JSON/JSONB containment and key-existence, full-text search, trigram similarity | Write-heavy with a high `gin_pending_list_limit` backlog gets slow. Check pending list length before assuming the index is broken. |
| GiST | Geometry, ranges, nearest-neighbour (`<->`), text search with `gist_trgm_ops` | Lossy for some operator classes, so it re-checks with a filter. KNN needs the right operator class. |
| SP-GiST | Non-balanced structures: quadtrees, text prefix ranges, IP nets | Niche; use when the data distribution is inherently hierarchical or sparse. |
| BRIN | Very large, append-ordered tables where a physical-range summary is enough | Tiny index, cheap to maintain, but only prunes when values are physically correlated with row order. |

```sql
-- Full-text search: GIN over tsvector, built from an IMMUTABLE expression.
CREATE INDEX documents_fts_idx
    ON documents
    USING gin (to_tsvector('english'::regconfig, title || ' ' || body));

-- Trigram / fuzzy search, requires the pg_trgm extension.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX documents_title_trgm_idx ON documents USING gin (title gin_trgm_ops);

-- Events table that is only ever appended, queried by time window.
CREATE INDEX events_occurred_at_brin_idx
    ON events USING brin (occurred_at) WITH (pages_per_range = 32);
```

BRIN is the right answer for time-series and audit logs where a B-tree on the timestamp would
be a large write-amplifying structure and the table is physically ordered by that column. It is
the wrong answer the moment rows are updated in place or loaded out of order.

## Composite index column order

One rule covers most cases: **equality columns first, then the range or sort column.**
The index can seek on equality predicates and then walk the ordered suffix.

```sql
-- WHERE customer_id = ? AND created_at >= ? ORDER BY created_at DESC
CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC);
```

- Every column after the first range column is only usable for ordering, not for seeking.
- If a query filters only on `created_at`, this index does not serve it. Add
  `CREATE INDEX orders_created_idx ON orders (created_at DESC);`
- In PostgreSQL, skipping a leading column in a B-tree is generally not efficient; do not
  assume skip-scan behaviour. Check your version's documentation before designing around it.
- Sort direction only matters when the query has a matching `ORDER BY`. Indexes scan backwards,
  so one of `ASC` or `DESC` on the last key is usually enough.
- Optional class `NULLS FIRST` / `NULLS LAST` matters only when the query needs that exact
  order and your default is the opposite.

Verify the planner agrees:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders
WHERE customer_id = 42 AND created_at >= DATE '2024-01-01'
ORDER BY created_at DESC LIMIT 50;
```

## Covering, partial and expression indexes

**Covering (INCLUDE)** avoids heap visits for the columns you select. Included columns are
stored in leaf pages only, so they speed up reads without widening the seek key.

```sql
CREATE INDEX orders_covering_idx ON orders (customer_id, created_at DESC)
    INCLUDE (id, total_cents, status);
```

Preconditions for the index-only scan to actually happen: the visibility map must be current,
so autovacuum must be keeping up, and you must not select `*` (that selects every column).

**Partial** indexes cut size and maintenance cost when you only ever query a subset.

```sql
-- Only unprocessed rows are ever polled by the worker.
CREATE INDEX orders_unprocessed_idx ON orders (created_at)
    WHERE status = 'unprocessed';
```

A partial index is only usable when the planner can prove the query predicate implies the
index predicate. `WHERE status = 'unprocessed'` will not be used by a query that passes
`$1` as a parameter. Write the literal into the query, or use a plan-stable form the planner
can match.

**Expression** indexes index a computed value. The expression must be IMMUTABLE.

```sql
CREATE INDEX users_lower_email_idx ON users (lower(email));
-- then query with the identical expression:
SELECT * FROM users WHERE lower(email) = 'a@example.com';
```

## Statistics and autovacuum

Stale statistics are the number one cause of a plan that flips after a data volume change.

```sql
-- What the planner believes vs. what is there.
SELECT relname, n_live_tup, n_dead_tup, last_analyze, last_autovacuum
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;

-- Columns where the planner's distinct estimate is most wrong.
SELECT attname, n_distinct, correlation
FROM pg_stats
WHERE tablename = 'orders'
ORDER BY abs(n_distinct) DESC;
```

Raise the sampling depth for the columns that matter:

```sql
ALTER TABLE orders ALTER COLUMN status SET STATISTICS 1000; -- default is 100
ANALYZE orders;
```

Correlated columns break independent estimation. Declare the dependency:

```sql
CREATE STATISTICS orders_customer_status_stats (dependencies, ndistinct, mcv)
    ON customer_id, status FROM orders;
ANALYZE orders;
```

Autovacuum tuning is per-table when a table's write rate outruns the scale-factor defaults:

```sql
ALTER TABLE orders SET (
    autovacuum_vacuum_scale_factor = 0.02,   -- default 0.2
    autovacuum_analyze_scale_factor = 0.01, -- default 0.1
    autovacuum_vacuum_cost_limit = 2000,    -- default 200
    autovacuum_vacuum_cost_delay = 0        -- default 2 ms
);
```

Defaults scale with row count: 20% of a billion-row table is two hundred million dead tuples
before vacuum starts. That is the failure mode the per-table settings above exist for.

**Autovacuum cannot reclaim rows whose `xmin` is still visible to some running transaction.**
A long-running reporting query or an idle-in-transaction session silently freezes vacuum and
bloat growth for the whole database. Check `pg_stat_activity` for
`state = 'idle in transaction'` and `xact_start` on every incident investigation.

```sql
SELECT pid, usename, state, now() - xact_start AS xact_age, left(query, 120)
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_start
LIMIT 10;
```

## Connection pooling and connection settings

Every PostgreSQL backend is a process. At a few hundred concurrent connections you are
memory-bound on `work_mem` multiplication and scheduler-bound on context switching, before
you are CPU-bound on queries. Put a pooler (PgBouncer and equivalents) in front and set the
application's connection count accordingly.

| Pooler mode | Fits | Breaks |
| --- | --- | --- |
| `session` | Connection-affine apps, apps using `LISTEN`/`NOTIFY` or session advisory locks | Little; you still pay for one server connection per client |
| `transaction` | Most web apps and stateless workers | Session state: `SET` without `LOCAL`, temp tables, session advisory locks. Recent pooler versions can track protocol-level prepared statements; check your version's docs. |
| `statement` | Very short single-statement queries | Multi-statement transactions entirely |

Set protective timeouts per role or database so one bad query cannot exhaust the cluster:

```sql
ALTER ROLE app_user SET statement_timeout = '30s';
ALTER ROLE app_user SET lock_timeout = '5s';
ALTER ROLE app_user SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE app_user SET application_name = 'checkout-api';
```

`lock_timeout` matters more than it looks: it converts a queue behind a blocking lock from a
silent stall into a fast, retryable error.

## Resource settings by class, not by folklore

`postgresql.conf` knobs change everything at once and require a reload or restart. Change one
class at a time and measure.

| Setting class | Controls | How to reason about it |
| --- | --- | --- |
| Memory | `shared_buffers`, `work_mem`, `maintenance_work_mem`, `effective_cache_size` | `work_mem` is per sort/hash node per query, not per connection. Prefer `SET LOCAL work_mem` for known-heavy statements over a global raise. |
| Planner | `random_page_cost`, `seq_page_cost`, `effective_cache_size`, `default_statistics_target` | These change cost assumptions, which is what drives index vs. scan choice. Wrong values cause the planner to stop using indexes that are actually better. |
| WAL | `wal_compression`, `max_wal_size`, `checkpoint_timeout` | Write-heavy workloads: bursts of checkpoint I/O are a common "it's slow at 3am" cause. |
| Autovacuum | `autovacuum_*` worker settings | Prefer per-table overrides over global ones. |
| Connections | `max_connections`, `reserved_connections` | Size against `work_mem` multiplication. Pool rather than raise. |
| Logging | `log_min_duration_statement`, `log_lock_waits`, `log_temp_files`, `track_io_timing` | These are diagnostic settings. Turn them on to answer a question, not permanently. |
| Parallelism | `max_worker_processes`, `max_parallel_workers`, `max_parallel_workers_per_gather` | Parallel query helps large analytical scans and hurts small transactional ones. |

Durable settings:

```sql
SHOW work_mem;
SELECT name, setting, unit, source, sourcefile, sourceline
FROM pg_settings WHERE name IN ('work_mem', 'shared_buffers', 'max_connections');

ALTER SYSTEM SET log_min_duration_statement = '500ms';
SELECT pg_reload_conf();
```

`ALTER SYSTEM` writes `postgresql.auto.conf`; some settings need a restart, others only
`pg_reload_conf()`. Check the "context" column in the official configuration docs.

## Common mistakes

- **`SET enable_seqscan = 0` as a fix.** It hides the symptom and produces a plan that will
  collapse back to a scan when the planner gets better statistics. Use it once, read the
  difference, then reset it.
- **Reading `rows=` as the truth.** It is the estimate. The gap between estimate and
  `actual rows` is the diagnostic, and it usually points at statistics rather than at the
  query text.
- **`ANALYZE` on a writer inside your transaction.** `EXPLAIN ANALYZE UPDATE ...` performs
  the update for real before you roll back. It is easy to forget in a session where the
  autocommit is off.
- **Assuming `shared_blks_read` means physical disk.** It means read() from shared buffers,
  which the OS page cache usually satisfies. Combine with `track_io_timing` and `iostat`
  or your host metrics before concluding you have a storage problem.
- **Creating an index on every foreign key column.** The referencing side needs an index for
  parent deletes and for joins that drive from the child. The referenced side only needs a
  unique index, which it already has as its primary key.
- **Raising `work_mem` globally to fix one report.** A per-node setting times the number of
  nodes times the number of concurrent queries. Use `SET LOCAL work_mem` inside the report's
  transaction.
- **Fixing a stale-statistics problem with a new index.** If the plan is wrong because the
  planner thinks `status` has 50 values when it has 3, an extra index will not help. Fix
  statistics or add extended statistics.
- **Treating autovacuum as automatic and therefore not a concern.** It is automatic, and it
  still falls behind on high-churn tables, on tables with a long initial threshold, and on
  any database with a long-running transaction.

## Anti-patterns

- One giant index on every column, on the theory that indexes are free. They are not: every
  index is written on every insert and updated on every update, and bloats the heap.
- `SELECT *` combined with an index-only scan plan. The covering index stops covering the
  moment you ask for a column it does not include.
- Wrapping a set-based update in a per-row application loop. One `UPDATE ... WHERE id = ANY(...)`
  or a single `INSERT ... SELECT` beats N round trips.
- Buffering everything with PgBouncer while also relying on session-level `SET`, temp tables,
  or `LISTEN`/`NOTIFY`. Pick a pooling mode your application can live with.
- Rebuilding the statistics of every table on a schedule without checking
  `last_analyze`. Targeted `ANALYZE` on the tables whose distribution shifted is cheaper and
  less disruptive.
- Reporting improvement from `EXPLAIN ANALYZE` alone. It is a different plan path with real
  concurrency effects; confirm with `pg_stat_statements` deltas after the change ships.
- Tuning `effective_cache_size` and `random_page_cost` "to make the planner use indexes"
  without checking whether the working set actually fits in RAM. The right fix for a cold
  index on a large table is more RAM or fewer rows, not a cost model that lies.

## Checklist

- Every slow statement captured by `pg_stat_statements` or `auto_explain`, with `BUFFERS`.
- Estimate-versus-actual gap checked on each node; statistics or extended statistics fixed
  where the gap is large.
- Index type chosen deliberately; BRIN only where physical order supports it.
- Composite indexes ordered equality-then-range-then-sort; a covering index only when the
  query returns a fixed, narrow column set.
- Partial indexes carry a predicate the planner can actually prove from the query.
- `n_dead_tup` and `last_autovacuum` monitored; no long-running transactions.
- `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` set per role.
- Pooler in front, with `application_name` set so `pg_stat_activity` is readable.
- One change at a time, measured before and after with the same workload.

## References

- Using EXPLAIN: https://www.postgresql.org/docs/current/using-explain.html
- Indexes overview: https://www.postgresql.org/docs/current/indexes.html
- Index types (B-tree, Hash, GiST, SP-GiST, GIN, BRIN): https://www.postgresql.org/docs/current/indexes-types.html
- Multicolumn indexes: https://www.postgresql.org/docs/current/indexes-multicolumn.html
- Partial indexes: https://www.postgresql.org/docs/current/indexes-partial.html
- Index-only scans: https://www.postgresql.org/docs/current/indexes-index-only-scans.html
- Expression indexes: https://www.postgresql.org/docs/current/indexes-expressional.html
- Examining index usage: https://www.postgresql.org/docs/current/indexes-examine.html
- GIN indexes: https://www.postgresql.org/docs/current/gin.html
- GiST indexes: https://www.postgresql.org/docs/current/gist.html
- BRIN indexes: https://www.postgresql.org/docs/current/brin.html
- `pg_trgm` extension: https://www.postgresql.org/docs/current/pgtrgm.html
- `pg_stat_statements`: https://www.postgresql.org/docs/current/pgstatstatements.html
- `auto_explain`: https://www.postgresql.org/docs/current/auto-explain.html
- Monitoring statistics: https://www.postgresql.org/docs/current/monitoring-stats.html
- Routine vacuuming: https://www.postgresql.org/docs/current/routine-vacuuming.html
- `VACUUM`: https://www.postgresql.org/docs/current/sql-vacuum.html
- `ANALYZE`: https://www.postgresql.org/docs/current/sql-analyze.html
- Extended statistics: https://www.postgresql.org/docs/current/sql-createstatistics.html
- Query planner configuration: https://www.postgresql.org/docs/current/runtime-config-query.html
- Resource configuration: https://www.postgresql.org/docs/current/runtime-config-resource.html
- Logging configuration: https://www.postgresql.org/docs/current/runtime-config-logging.html
- Connection parameters: https://www.postgresql.org/docs/current/libpq-connect.html
- Error and status codes: https://www.postgresql.org/docs/current/errcodes-appendix.html
