# Backfill and Data Migration Cookbook

Load this when a migration needs to move data in a live table.

## Batched update by primary key window

```sql
-- One batch. Run repeatedly until it affects zero rows.
UPDATE orders
SET shipped_at = created_at,
    updated_at = now()
WHERE shipped_at IS NULL
  AND id BETWEEN 2000000 AND 2500000;
```

Why key windows rather than `OFFSET`:

- `OFFSET n` re-reads and discards `n` rows every batch, so total work grows quadratically.
- Key windows are resumable. Record the last processed key and start there.
- Key windows avoid re-locking rows an earlier batch already updated.

```sql
-- Do not do this for a large table.
UPDATE orders SET shipped_at = created_at
WHERE shipped_at IS NULL
OFFSET 1000000 LIMIT 50000;   -- correct but progressively slower
```

## Progress and safety queries to run alongside

```sql
-- Rows remaining.
SELECT count(*) FROM orders WHERE shipped_at IS NULL;

-- Batch duration and row count for the last statement, from pg_stat_statements.

-- Table growth from the update; expect it to be large before you vacuum.
SELECT pg_size_pretty(pg_total_relation_size('orders')) AS total,
       n_live_tup, n_dead_tup
FROM pg_stat_user_tables WHERE relname = 'orders';

-- Replica lag.
SELECT now() - replay_lag AS replay_delay FROM pg_stat_replication;
```

`pg_stat_replication` columns for lag are named `write_lag`, `flush_lag` and `replay_lag`, and
what they report depends on configuration and whether `track_commit_timestamp` is on. Verify
what your server exposes before depending on a specific value.

## Backfill a new column from a join

```sql
-- Batch by the new table's key, computing the value from a join.
INSERT INTO order_shipping (order_id, carrier, tracking_code)
SELECT o.id, s.carrier, s.tracking_code
FROM orders o
JOIN shipments s ON s.order_id = o.id
WHERE o.id BETWEEN $1 AND $2
  AND NOT EXISTS (
      SELECT 1 FROM order_shipping os WHERE os.order_id = o.id
  )
ON CONFLICT (order_id) DO NOTHING;
```

The `NOT EXISTS` guard plus `ON CONFLICT DO NOTHING` makes it idempotent, which matters
because you will restart it.

## Copy into a new column, then swap

For a type change or a semantic change, never rewrite in place.

```sql
-- 1. Add the new column.
ALTER TABLE orders ADD COLUMN total_cents bigint;

-- 2. Backfill it in batches.
UPDATE orders o
SET total_cents = round(o.subtotal * 100 + o.tax * 100 + o.shipping * 100)::bigint
WHERE o.total_cents IS NULL
  AND o.id BETWEEN $1 AND $2;

-- 3. Validate.
SELECT count(*) FROM orders WHERE total_cents IS NULL;

-- 4. Switch reads to the new column (application deploy).

-- 5. Enforce.
ALTER TABLE orders
    ADD CONSTRAINT orders_total_present CHECK (total_cents IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_total_present;

-- 6. Drop the old column, later.
```

## Snapshot the old values so the change is reversible

```sql
-- Same migration, before the change.
CREATE TABLE orders_pre_migration_backup AS
SELECT id, subtotal, tax, shipping
FROM orders;

-- ... do the change ...

-- Down path.
UPDATE orders o
SET subtotal = b.subtotal, tax = b.tax, shipping = b.shipping
FROM orders_pre_migration_backup b
WHERE b.id = o.id;

-- Then drop the snapshot, in a later migration once you are confident.
DROP TABLE orders_pre_migration_backup;
```

The snapshot table costs storage proportional to the table. Decide deliberately whether the
rows you need are only the affected subset, and snapshot only those.

## Handling NOT NULL columns being added to existing rows

Backfill in two halves: fill all NULLs first, then add the constraint.

```sql
-- Backfill chunk.
UPDATE orders
SET note = 'migrated'
WHERE note IS NULL
  AND id BETWEEN $1 AND $2;

-- Then add the validated check, then SET NOT NULL.
ALTER TABLE orders ADD CONSTRAINT orders_note_present CHECK (note IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_note_present;
ALTER TABLE orders ALTER COLUMN note SET NOT NULL;
```

`VALIDATE CONSTRAINT` still scans the table. On a very large table, if even that scan is
unacceptable, rely on the backfill you just performed plus a cheaper existence proof, or accept
the scan in a maintenance window. There is no free version.

## Data migrations that need application logic

Sometimes the transformation is not expressible in SQL. Options, in order of preference:

1. Express it in SQL and run it batched as above.
2. Stream rows out and apply the transformation in application code, then write back in
   batches, using the key window for idempotency.
3. Use an `UPDATE ... FROM` with a generated intermediate table if the transform can be staged.

Whatever you choose, keep it out of the migration tool. Track it as a job with its own start,
pause, resume, verification, and rollback.

## Verification queries

```sql
-- Before and after counts must match.
SELECT count(*) AS total, count(shipped_at) AS populated FROM orders;

-- Distribution check: did the logic do what you think?
SELECT date_trunc('day', created_at) AS day, count(*) AS rows
FROM orders
GROUP BY 1
ORDER BY 1 DESC
LIMIT 7;

-- Spot check against the source.
SELECT o.id, o.created_at, o.shipped_at
FROM orders o
WHERE o.id IN (SELECT id FROM orders ORDER BY random() LIMIT 20);
```

Record the before and after numbers in the pull request. "Backfill completed" without numbers
is not reviewable.

## Post-backfill cleanup

```sql
-- Dead tuples accumulate from the batched updates.
VACUUM (ANALYZE) orders;

-- Watch autovacuum keep up during the backfill; the churn is high.
SELECT n_live_tup, n_dead_tup, last_autovacuum FROM pg_stat_user_tables WHERE relname = 'orders';
```

Batched updates rewrite the same rows repeatedly across batches only if your windows overlap;
they do not, so dead tuples are bounded by the batch size times the number of batches. Still
plan for meaningful WAL volume and disk headroom.
