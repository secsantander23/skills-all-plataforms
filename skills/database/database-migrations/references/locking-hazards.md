# Lock Hazards and Deployment Safety

## How PostgreSQL DDL locking actually fails you

A DDL statement takes `ACCESS EXCLUSIVE`. Two properties combine badly:

1. A request for `ACCESS EXCLUSIVE` waits for all conflicting locks to clear.
2. While it waits, it is **queued**, and a queued `ACCESS EXCLUSIVE` blocks every subsequent
   lock request, including plain `SELECT`s that would otherwise be free.

So the failure mode is not "one statement is slow". It is "one statement waiting causes an
unrelated read to also wait". On a busy table, an idle-in-transaction session that predates
your migration is enough to cause this.

## Statement patterns and their blast radius

```sql
-- Metadata only. Brief lock. Safe.
ALTER TABLE orders ADD COLUMN note text;
ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'new';   -- constant default
ALTER TABLE orders ALTER COLUMN status SET DEFAULT 'new';
ALTER TABLE orders ALTER COLUMN status DROP NOT NULL;
ALTER TABLE orders DROP COLUMN legacy_flag;                          -- data still on disk until rewrite

-- Rewrites the whole table under ACCESS EXCLUSIVE. Not safe on a live table.
ALTER TABLE orders ALTER COLUMN id TYPE text;                       -- usually a rewrite
ALTER TABLE orders ALTER COLUMN amount TYPE numeric(12,2)
    USING amount::numeric(12,2);                                    -- rewrite plus expression
ALTER TABLE orders ADD COLUMN total numeric GENERATED ALWAYS AS (...) STORED; -- rewrite

-- Scans with a strong lock. Use NOT VALID instead.
ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE orders ADD CONSTRAINT chk CHECK (total >= 0);

-- Blocks writes for the whole build. Use CONCURRENTLY.
CREATE INDEX orders_status_idx ON orders (status);
```

Whether a specific `ALTER TYPE` rewrites depends on whether the new type is binary coercible
from the old. The rule of thumb: verify each one you actually plan to run with
`EXPLAIN`-adjacent tooling or the engine's own documentation rather than assuming.

## Retry with lock_timeout

```sql
DO $$
DECLARE
    attempt int := 0;
BEGIN
    LOOP
        BEGIN
            SET LOCAL lock_timeout = '3s';
            ALTER TABLE orders ADD COLUMN note text;
            EXIT;
        EXCEPTION WHEN lock_not_available THEN
            attempt := attempt + 1;
            IF attempt > 20 THEN
                RAISE EXCEPTION 'gave up after % attempts', attempt;
            END IF;
            -- Back off with jitter before retrying.
            PERFORM pg_sleep(random() * least(attempt * 0.5, 10));
        END;
    END LOOP;
END $$;
```

For migrations, prefer setting `lock_timeout` once in the migration session and having the
pipeline retry the whole migration, because a partially applied DDL batch is harder to reason
about than a retry loop in SQL.

## Long-running transactions block DDL and vacuum

```sql
SELECT pid, usename, application_name, state,
       now() - xact_start AS xact_age,
       now() - state_change AS state_age,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND pid <> pg_backend_pid()
ORDER BY xact_start;
```

Targets to alert on:

| Condition | Why it matters |
| --- | --- |
| `state = 'idle in transaction'` for over a minute | Holds back DDL and blocks vacuum cleanup |
| Long `xact_start` on a reporting replica | Same, on the replica, which then falls behind |
| A session in `state = 'active'` with an old `query_start` | Either a genuine long query or a stuck lock wait |

Preventions that are not schema changes: set `idle_in_transaction_session_timeout`, set
statement timeouts per role, and pool connections so forgotten sessions cannot accumulate.

## Deploy ordering with expand-contract

The safe ordering when the application and database deploy independently:

```
Release N   : expand  (additive migration, old code unaffected)
Release N   : deploy  code that writes new shape and still reads old shape
   (backfill runs here, as a job)
Release N+1 : deploy  code that reads new shape, falls back to old shape
   (validation constraint applied; NOT VALID first)
Release N+2 : deploy  code that no longer knows the old shape exists
Release N+3 : contract (drop old column, now safe because nothing reads it)
```

The number of releases is a cost. If you cannot afford four, reduce it by making the
migration purely additive (add the new column in the same release that starts writing it) and
delay the drop to whenever you next touch that table.

## Online schema change tooling

Native DDL is the fastest path when it is genuinely online. When it is not:

| Tool | Engine | Approach |
| --- | --- | --- |
| `gh-ost` | MySQL | Triggers or binary-log based shadow table, chunked row copy, atomic cutover |
| `pt-online-schema-change` | MySQL | Creates a shadow table, copies in chunks with a changelog trigger, renames at the end |
| `pg_repack` / `pg_squeeze` | PostgreSQL | Rebuild the table with `ACCESS EXCLUSIVE` held only briefly at the start and end |

These tools are slower than native DDL and add operational complexity. Reach for them when you
know the native path takes a lock you cannot take, not reflexively.

## Testing migrations against production shape

1. Restore a recent backup into a scratch database, or use a physical standby replica.
2. Record row counts for every table the migration touches.
3. Run the migration with `lock_timeout` set and time each statement.
4. While it runs, from a second session, run a tight read loop and record the worst latency.
   Any spike in read latency is your answer about lock queueing.
5. Run the backfill and measure per-batch duration, WAL volume, and table growth.

This gives you numbers instead of the "should be fine".
