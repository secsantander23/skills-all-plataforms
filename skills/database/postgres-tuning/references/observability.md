# Observability and Configuration Reference

## pg_stat_statements setup

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

The extension needs its shared library in `shared_preload_libraries` before the server
starts, otherwise the extension exists but collects nothing. That setting cannot be changed
in a running session; it requires a restart.

Useful configuration parameters (all in `postgresql.conf`, names are stable, verify defaults
in the official documentation):

| Parameter | Purpose |
| --- | --- |
| `pg_stat_statements.max` | Number of distinct statements tracked. Set it at least as large as your application's statement diversity. |
| `pg_stat_statements.track` | Whether to track statements. `top` tracks client statements only; `all` also tracks nested statements. |
| `pg_stat_statements.track_utility` | Whether to track utility statements such as DDL. |
| `pg_stat_statements.track_planning` | Whether to record planning time separately. |

```sql
-- Column names have changed across major releases (for example the time columns were
-- renamed in a recent release). Inspect what your server actually exposes before scripting.
SELECT attname FROM pg_attribute
WHERE attrelid = 'pg_stat_statements'::regclass AND attnum > 0
ORDER BY attnum;

-- Reset counters when you want a clean measurement window.
SELECT pg_stat_statements_reset();
```

## Slow statement logging with auto_explain

```sql
CREATE EXTENSION IF NOT EXISTS auto_explain;
```

Session-level, which is enough for reproducing a problem:

```sql
SET auto_explain.log_min_duration = '500ms';
SET auto_explain.log_analyze = on;
SET auto_explain.log_buffers = on;
SET auto_explain.log_nested_statements = on;
SET auto_explain.sample_rate = 1.0;
```

For server-wide, persistent configuration, set the same keys in the `auto_explain.*` family in
`postgresql.conf`, or in `ALTER SYSTEM`. Note that logging every statement with
`log_analyze` has real overhead; keep the threshold high.

Parameter-level GUCs are the right way to override these per role:

```sql
ALTER ROLE app_user SET auto_explain.log_min_duration = '2s';
```

## Server logging that answers real questions

| Parameter | Question it answers |
| --- | --- |
| `log_min_duration_statement` | Which statements ran longer than N ms. The single most useful one. |
| `log_lock_waits` | Which statements waited on locks. Combine with `deadlock_timeout` for duration. |
| `log_temp_files` | Which queries spilled to disk, and how much. Set to 0 for all, with a KB minimum. |
| `log_autovacuum_min_duration` | Whether autovacuum is actually running on your tables. |
| `log_checkpoints` | Checkpoint frequency and duration spikes. |
| `log_statement` / `log_statement = 'ddl'` | DDL only, useful for auditing migrations. |
| `track_io_timing` | Real I/O timings in plans and in `pg_stat_activity`. Superuser only, set at start. |
| `compute_query_id` | Enables query identifiers; some tooling depends on them. |

## Live picture: what is happening now

```sql
-- Longest running queries with wait events.
SELECT pid, state, wait_event_type, wait_event,
       now() - xact_start AS xact_age, now() - query_start AS query_age,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE backend_type = 'client backend'
ORDER BY query_start NULLS LAST
LIMIT 20;

-- Idle in transaction sessions block vacuum.
SELECT pid, usename, application_name, now() - xact_start AS xact_age
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_start;

-- Terminate a runaway backend.
SELECT pg_cancel_backend(<pid>);
SELECT pg_terminate_backend(<pid>);
```

## Table-level vacuum and bloat signals

```sql
SELECT schemaname, relname, n_live_tup, n_dead_tup,
       vacuum_count, autovacuum_count,
       last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 0
ORDER BY n_dead_tup DESC
LIMIT 20;
```

Per-table vacuum settings:

```sql
ALTER TABLE events SET (
    autovacuum_enabled = true,
    autovacuum_vacuum_threshold = 1000,       -- absolute floor, in addition to the scale factor
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_limit = 2000,
    autovacuum_vacuum_cost_delay = 0
);
```

Thresholds are additive: vacuum triggers when dead tuples exceed
`autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * reltuples`.

## Transaction age and vacuum horizon

```sql
-- Oldest running transactions; anything long-lived here blocks cleanup cluster-wide.
SELECT pid, backend_xid, backend_xmin,
       now() - backend_xmin AS xmin_age,
       state, left(query, 120)
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL
ORDER BY backend_xmin
LIMIT 5;
```

## Memory arithmetic worth doing once

Peak memory for one query is roughly:

```
(work_mem * peak_concurrent_sort_or_hash_nodes_per_query)
  + (maintenance_work_mem * autovacuum_workers_running)
  + (shared_buffers)
  + (per-backend overhead * connections)
```

Size `work_mem` against the right-hand side, not against a rule of thumb. When you need a
big value for one class of query, scope it:

```sql
BEGIN;
SET LOCAL work_mem = '256MB';
-- the heavy report runs here with a larger work_mem and nothing else is affected
COMMIT;
```

## Statistics reset semantics

Statistics views are cumulative since the last reset or restart. `pg_stat_reset()` and
`pg_stat_statements_reset()` clear them. Snap baselines before and after a change rather than
reading absolute counters from a long-lived server.
