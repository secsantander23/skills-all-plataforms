# Plan Anatomy Reference

Reference material for reading PostgreSQL query plans. Load this when a plan is complex and
the SKILL.md cheat table is not enough.

## Node types you will meet

| Node | What it does | Watch for |
| --- | --- | --- |
| `Seq Scan` | Reads the whole heap in physical order | Fine on a small table, on a table where most rows are needed, or after a fresh insert-only load. Bad as a surprise on a selective filter over millions of rows. |
| `Index Scan` | Walks the index in order, fetching heap rows | `Rows Removed by Filter` means the predicate is not fully covered by the index. |
| `Index Only Scan` | Answers from the index alone | Only truly index-only if `Heap Fetch` is absent or tiny. Requires a current visibility map. |
| `Bitmap Index Scan` + `Bitmap Heap Scan` | Builds a bitmap of matching TIDs, then fetches heap pages in physical order | Wins when the selectivity is moderate. A bitmap built in memory is fine; check `temp read/written` in `BUFFERS`. |
| `Tid Scan` | Scans by physical row location | Used by `VACUUM`, `CLUSTER` and CTID-based maintenance queries. |
| `Subquery Scan` | Runs a subplan and exposes it as a relation | Correlated subplans in the `Filter` are a common N+1 pattern. |
| `Values Scan` | Emits literal rows from a `VALUES` list | Used by `INSERT ... VALUES (...), (...)` rewriting. |
| `Function Scan` | Streams rows out of a set-returning function | In `FROM`, not in the select list, so cost estimates can be poor. |
| `CTE Scan` | Materializes or inlines a CTE | See the CTE materialization note below. |
| `Limit` | Stops after N rows | Makes a plan look fast in `EXPLAIN ANALYZE` when the LIMIT is at the top; the true cost is what was produced underneath. |
| `Result` | Computes a constant | Often a planning artifact for `Append` of partitioned tables. |

## Join nodes

`Nested Loop`, `Merge Join` and `Hash Join` appear under their join nodes as `Join` when
using the default textual format. The rules of thumb:

- **Nested Loop.** Cheap when the outer side is small and the inner side has an index.
  Cost multiplies by `loops`, so read `actual time` on the inner node together with `loops`
  on the outer.
- **Hash Join.** Builds a hash table of the inner side. Needs `work_mem`. Watch
  `Hash Batches` greater than 1, which means it spilled.
- **Merge Join.** Requires both inputs sorted. Often chosen for large sorted scans and for
  ordered output. Shows up as `Sort` nodes feeding `Merge Join` when inputs are not already
  ordered.
- **Memoize.** A planner node that caches inner results for repeated outer keys. It appears
  in recent PostgreSQL versions on parameterized nested loops; verify availability against
  your server's documentation.

## Buffers

`EXPLAIN (ANALYZE, BUFFERS)` prints per-node and total buffer counters:

```
Buffers: shared hit=1204 read=83 dirtied=12
```

- `shared hit`: served from PostgreSQL's own shared buffer pool.
- `shared read`: had to be fetched from the operating system, which may or may not be disk.
- `dirtied`: pages written back to disk because of this node. A large `dirtied` on a read-only
  plan means memory pressure evicted pages.
- `temp read` / `temp written`: temporary file traffic. Non-zero means a sort or hash spilled.

Add `track_io_timing` (superuser only, effective at server start) to get `I/O Timing` numbers,
or `WAL` in recent versions to see WAL generated. Both are diagnostic, not permanent settings.

## Sorting and hashing

```
Sort  (cost=... rows=...) (actual time=...)
  Sort Key: o.created_at DESC
  Sort Method: quicksort  Memory: 25kB
```

`Sort Method` tells you the whole story:

- `quicksort`, `top-N heapsort`, `external merge` with a small `Disk` value is fine.
- `external merge Disk: 51200kB` means `work_mem` was exceeded. Raise it for that statement.

```
Hash  (actual time=... rows=... loops=1)
  Buckets: 1024  Batches: 1  Memory Usage: 248kB
```

`Batches: 1` is good. Anything above 1 means the hash spilled and had to be partitioned to
disk, which is usually the single biggest cause of a slow join.

## Deferred and parameterized plans

A prepared statement can get a generic plan after several executions. If a query is fast the
first few times and then slow, look for the generic plan in the log and check whether the
generic estimate is worse than the custom one. The PostgreSQL documentation section on
prepared statements and plan caching covers the exact thresholds, which are configurable.

## CTEs

A CTE written without `MATERIALIZED` may be inlined by the planner; older behaviour and
`WITH ... AS MATERIALIZED` forces materialization. `WITH RECURSIVE` always executes as a
recursive CTE. When a CTE is materialized you will see a `CTE Scan` and it may become the
driving table of the join.

## Partitioned tables

Each partition has its own indexes; `EXPLAIN` on a partitioned table shows `Append` over the
partitions, and only non-pruned partitions appear when pruning works. If all partitions are
listed, the pruning predicate is not sargable for at least one dimension.

## Seeing the plan of a parameterized query

`EXPLAIN` without `ANALYZE` on a query with parameters guesses values. Compare the estimate
against a run with literal values substituted to see whether the guess changes the plan.

## Verifying what a parameter actually is

```sql
PREPARE orders_by_customer(bigint, date) AS
SELECT id, created_at FROM orders
WHERE customer_id = $1 AND created_at >= $2
ORDER BY created_at DESC
LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS) EXECUTE orders_by_customer(42, DATE '2024-01-01');
DEALLOCATE orders_by_customer;
```
