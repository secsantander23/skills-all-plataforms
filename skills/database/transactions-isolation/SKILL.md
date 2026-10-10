---
name: Transactions and Isolation Levels
description: Covers ACID, the four standard isolation levels and how each engine actually implements them, MVCC snapshots, phantom reads, lost updates, write skew, deadlocks, SELECT FOR UPDATE and row locking, optimistic concurrency with version columns, and retry strategies for serialization failures. Use when the user asks about isolation levels, when debugging a lost update or inconsistent read, when seeing a deadlock or serialization failure, or when choosing an isolation level for a workload.
metadata:
  opencode/autoinvoke: true
---

# Transactions and Isolation

Concurrency bugs are rare, intermittent, and expensive. This skill gives you the vocabulary to
name the bug you have, the mechanism that produces it, and the smallest correct fix.

## When to use this

- Two requests read-modify-write the same row and one update disappears.
- A report shows an inconsistent snapshot, or a constraint was violated by data that "could
  not exist".
- You hit `deadlock_detected` or `serialization failure` and need to know whether to retry.
- Choosing an isolation level for a workload (reporting, checkout, counters, job queues).
- Deciding between pessimistic locking and optimistic version checks.
- An ORM is retrying internally and hiding that your transaction was non-idempotent.

## Vocabulary first

| Term | Meaning |
| --- | --- |
| Dirty read | Reading a row another transaction wrote but has not committed |
| Non-repeatable read | Two reads in one transaction see different values for the same row |
| Phantom read | Two reads in one transaction see a different set of rows matching the same predicate |
| Lost update | Two transactions read a value, both write a result computed from it, one write is silently overwritten |
| Write skew | Two transactions each pass a check against different rows, and together they violate an invariant |
| Deadlock | Two transactions each hold a resource the other needs, and neither can proceed |
| Serialization failure | The engine detected that the interleaving is not equivalent to any serial order, and aborted one transaction |

## ACID, briefly and precisely

- **Atomicity.** All of a transaction's writes become visible, or none do. Implemented by
  undo logs (rollback segments) or by keeping the old version of each row.
- **Consistency.** The committed state satisfies declared constraints. Note the trap: engine
  constraints (`CHECK`, foreign keys, unique indexes) are enforced by every engine. Application
  invariants expressed only in code are not, which is why write skew exists.
- **Isolation.** Concurrent transactions appear to execute serially, to the degree your
  isolation level promises. This is the whole subject of this skill.
- **Durability.** Committed writes survive a crash. Requires the engine's commit path to flush
  to durable storage according to its configuration.

## Isolation levels

| Level | Dirty reads | Non-repeatable read | Phantoms | Typical use |
| --- | --- | --- | --- | --- |
| Read Uncommitted | Possible | Possible | Possible | Almost never |
| Read Committed | Prevented | Possible | Possible | Default for PostgreSQL, MySQL's lower option, most web apps |
| Repeatable Read | Prevented | Prevented | Possible without locking reads | Reports, MySQL InnoDB default |
| Serializable | Prevented | Prevented | Prevented | Correctness-critical concurrent logic |

**Critical caveat: the SQL standard defines these as a minimum guarantee. Engines differ, and
the differences dominate in practice.**

PostgreSQL specifics you must know:

- `READ UNCOMMITTED` is accepted but behaves as `READ COMMITTED`. There is no dirty read.
- The default is `READ COMMITTED`. Under it, **each statement** takes a fresh snapshot, so two
  statements in one transaction can see different data. That is the source of most
  "impossible" reports.
- `REPEATABLE READ` gives a transaction-wide snapshot taken at the first statement. Phantoms
  are still possible for plain `SELECT`, because a snapshot is not a lock.
- `SERIALIZABLE` uses Serializable Snapshot Isolation with predicate-lock style tracking
  (SSI). It does not lock rows up front. Instead it allows transactions to run concurrently and
  aborts one of them if a dangerous structure is detected at commit time.
- `REPEATABLE READ` in PostgreSQL can also raise serialization failures, not only `SERIALIZABLE`.

MySQL and other engines have different defaults and different locking behaviour. InnoDB's
default `REPEATABLE READ` uses next-key (gap) locks on range scans, which prevents certain
phantoms but also means a plain `SELECT` on a range can block inserts. Verify what your engine
actually does rather than assuming the textbook table.

## Setting the level

```sql
-- Per transaction (must be before the first query in the transaction).
BEGIN;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SELECT count(*) FROM inventory WHERE sku = 'ABC' AND available >= requested;
-- ... logic ...
COMMIT;
```

```sql
-- For the whole session or connection.
SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ;
```

Keep it per transaction. Changing the session default changes behaviour for code you do not
own, including the ORM's own statements.

## MVCC and snapshots

MVCC lets readers not block writers and writers not block readers, by storing multiple versions
of each row and giving each transaction a snapshot that determines which versions it sees.

```
reader snapshot:  sees row version A
writer:           writes row version B, commits
reader:           still sees A, because its snapshot predates B
next statement:    a new snapshot, now seeing B
```

The cost is the maintenance behind the scenes:

- Dead row versions must eventually be removed, or everything slows down and disk grows.
- Removal requires knowing that no live transaction can still need those versions. A
  long-running transaction, or an `idle in transaction` session, **holds back that cleanup for
  the entire database**. This is the single most common cause of "the database got slow last
  week" with no query changes.
- Long-running transactions mean a growing snapshot, which is why reports should not run
  unbounded read transactions in the middle of the working day.

PostgreSQL exposes this directly:

```sql
SELECT pid, state, now() - xact_start AS xact_age, backend_xmin, left(query, 100)
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL
ORDER BY xact_start;
```

## The three classic anomalies, with fixes

### Lost update

```sql
-- Both transactions read balance = 100.
-- T1: UPDATE accounts SET balance = 100 - 30 WHERE id = 1;   -> 70
-- T2: UPDATE accounts SET balance = 100 - 20 WHERE id = 1;   -> 80   (T1's write is lost)
```

The read-modify-write spans a gap that no single statement covers. Three fixes:

**Fix 1: compute in the statement, hold the row lock.**

```sql
UPDATE accounts SET balance = balance - 30 WHERE id = 1 AND balance >= 30;
```

The read of `balance` and the write happen inside one statement, under a row lock. This is the
correct and simplest fix for counters and balances, and it also makes the check-and-set
conditional.

**Fix 2: pessimistic lock with `SELECT ... FOR UPDATE`.**

```sql
BEGIN;
SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;
-- business logic here
UPDATE accounts SET balance = 100 - 30 WHERE id = 1;
COMMIT;
```

**Fix 3: optimistic concurrency with a version column.**

```sql
UPDATE accounts
SET balance = 70, version = version + 1
WHERE id = 1 AND version = 5;   -- version read earlier

-- If rowcount = 0, someone else won. Re-read and retry.
```

### Write skew

```sql
-- Business rule: at least one doctor must be on call at all times.
-- Initially one doctor: dr_a is on call, dr_b is not.

-- T1 (dr_b switches on):
SELECT 1 FROM on_call WHERE doctor_id = 1 AND on_call = true FOR UPDATE;  -- 0 rows
INSERT INTO on_call (doctor_id, on_call) VALUES (2, true);
COMMIT;

-- T2 (dr_a switches off), running concurrently:
SELECT 1 FROM on_call WHERE doctor_id = 2 AND on_call = true FOR UPDATE;  -- 0 rows
UPDATE on_call SET on_call = false WHERE doctor_id = 1;
COMMIT;

-- Result: nobody is on call. Each transaction was individually correct.
```

The root cause is that the invariant spans multiple rows and neither transaction read the rows
the other wrote. Fixes, in order of preference:

1. **Model the invariant as a database constraint.** Unique indexes, `CHECK`, foreign keys, and
   exclusion constraints cannot be bypassed by application bugs.
2. **Use `SERIALIZABLE`**, which detects the dangerous structure and aborts one transaction.
3. **Lock a common row first.** Both transactions lock the same "roster" row before checking,
   which serializes them.
4. **Advisory lock on a shared key** for the invariant, if you cannot restructure.

### Phantom

```sql
-- Under REPEATABLE READ, two SELECTs can see different row sets.
SELECT count(*) FROM orders WHERE status = 'pending';   -- 5
-- another transaction inserts a pending order and commits
SELECT count(*) FROM orders WHERE status = 'pending';   -- 6
```

Fixes: `SERIALIZABLE`, or a locking read that takes a predicate lock:

```sql
SELECT count(*) FROM orders WHERE status = 'pending' FOR UPDATE;
```

`FOR UPDATE` under Repeatable Read locks the rows the scan touched, which in PostgreSQL is the
rows matching the predicate as seen by the snapshot, so concurrent inserts into the same range
are not fully prevented. For a hard guarantee use `SERIALIZABLE` or an exclusion constraint.

## Row locking

```sql
SELECT * FROM queue
WHERE status = 'ready'
ORDER BY created_at
LIMIT 10
FOR UPDATE SKIP LOCKED;
```

| Clause | Lock |
| --- | --- |
| `FOR UPDATE` | Blocks other updates and locks; strongest |
| `FOR NO KEY UPDATE` | Blocks updates that change the key or that take `FOR UPDATE`, weaker than `FOR UPDATE` |
| `FOR SHARE` | Blocks updates, allows other readers |
| `FOR KEY SHARE` | Weakest; blocks deletes and key changes only |
| `SKIP LOCKED` | Skip rows already locked instead of waiting; the work-queue pattern |
| `NOWAIT` | Error immediately if the row is locked instead of waiting |

`SKIP LOCKED` is what makes many-worker queue processing safe and fast. Without it, workers
queue behind each other on the same head row.

Locking clauses only make sense inside an explicit transaction in PostgreSQL, since the lock is
released at transaction end. In an autocommit statement the lock is taken and released
immediately, which is usually not what you intended.

**Two rules to keep:**

1. **Locks are held until the transaction ends.** Never hold a transaction open across an HTTP
   call, a message publish, or a user think. That is how lock tables form.
2. **Lock in a consistent order.** Ordering by key before locking removes most deadlocks:

```sql
-- Always transfer in ascending id order.
BEGIN;
SELECT balance FROM accounts WHERE id = 1 FOR UPDATE;
SELECT balance FROM accounts WHERE id = 2 FOR UPDATE;
-- ...
COMMIT;
```

If you cannot know the order in advance, sort the ids in the application before issuing the
locking statements.

## Deadlocks

A deadlock is not a bug in your query. It is the engine correctly refusing to let two
transactions make no progress.

```sql
-- T1: UPDATE accounts SET balance = balance - 10 WHERE id = 1;
-- T2: UPDATE accounts SET balance = balance - 10 WHERE id = 2;
-- T1: UPDATE accounts SET balance = balance - 10 WHERE id = 2;  -- waits for T2
-- T2: UPDATE accounts SET balance = balance - 10 WHERE id = 1;  -- waits for T1 -> deadlock
```

The engine detects it, picks a victim, and aborts it with `deadlock_detected`. SQLSTATE is
`40P01`. Note that in PostgreSQL `40P01` is also reported in some lock-timeout situations, so
log the SQLSTATE and message rather than branching on the name alone.

Fixes:

- Consistent lock ordering, always.
- Keep transactions short; never span an external call.
- Take locks in a single statement where you can, for example a set-based `UPDATE ... WHERE id
  = ANY(...)` instead of a loop.
- Retry the aborted transaction. A deadlock victim is safe to retry **provided** the transaction
  body is idempotent.

Detect and inspect:

```sql
-- In PostgreSQL, view the deadlock report in the server log.
SELECT pid, wait_event_type, wait_event, left(query, 200)
FROM pg_stat_activity
WHERE wait_event_type = 'Lock';
```

## Optimistic concurrency

Add a version column and make every write conditional on the version you read.

```sql
CREATE TABLE documents (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title      text NOT NULL,
    body       text NOT NULL,
    version    integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Read
SELECT id, body, version FROM documents WHERE id = $1;

-- Write; zero rows affected means a concurrent writer won.
UPDATE documents
SET body = $2, version = version + 1, updated_at = now()
WHERE id = $1 AND version = $3;
```

This is the right default for low-conflict resources, and it removes the need to hold locks.
Its cost is that you must handle the conflict: either retry, or return a conflict to the caller
so it can re-read and merge.

Where it does not fit: high-conflict rows (a hot counter), where every retry collides again.
There, use the conditional single-statement `UPDATE` or a pessimistic lock.

## Retry strategies

Serialization failures and deadlocks are expected in a correct concurrent system. Retrying is
the correct response; retrying badly is worse than not retrying.

```text
retryable:
  40001  serialization_failure
  40P01  deadlock_detected
  55P03  lock_not_available   (NOWAIT / lock_timeout paths)

not retryable (retrying changes nothing or makes it worse):
  23505  unique_violation
  23503  foreign_key_violation
  23514  check_violation
```

Retry rules:

1. Retry **the whole transaction**, not the failed statement. The transaction is already
   aborted; you must start over.
2. **Only retry if the transaction is idempotent.** If it already sent an email or called a
   payment API before failing, retrying duplicates the effect. Put side effects after the
   commit, or make them idempotent with a deduplication key.
3. Use **exponential backoff with jitter**. Synchronised retries recreate the contention.
4. Cap the number of attempts and then surface the failure. A silent infinite retry is an
   outage with extra steps.
5. Under `SERIALIZABLE`, a read-only transaction can also be aborted, so reads need retry
   handling too.

```python
import random
import time

from sqlalchemy.exc import DBAPIError

RETRYABLE = {"40001", "40P01"}   # serialization_failure, deadlock_detected


def sqlstate(exc: DBAPIError) -> str:
    return getattr(exc.orig, "pgcode", None) or ""


def run_in_transaction(session, work, attempts=5):
    """work(session) must be idempotent: it runs again from scratch on a retryable failure."""
    for attempt in range(attempts):
        try:
            with session.begin():
                return work(session)
        except DBAPIError as exc:
            if sqlstate(exc) not in RETRYABLE or attempt == attempts - 1:
                raise
            # Exponential backoff with jitter, capped.
            time.sleep(min(2 ** attempt, 5) * (0.5 + random.random()))
    raise AssertionError("unreachable")
```

On engines where error codes are surfaced as strings rather than SQLSTATE, match on the stable
code values above rather than on human-readable messages.

## Choosing a level per workload

| Workload | Level | Reason |
| --- | --- | --- |
| Web request with a few reads and one write | `READ COMMITTED` | Default. Enough when writes are single-statement conditional updates |
| Counter, balance, inventory decrement | `READ COMMITTED` plus conditional single-statement `UPDATE` | The arithmetic happens inside the statement, so there is no lost update |
| Queue consumption with many workers | `READ COMMITTED` plus `FOR UPDATE SKIP LOCKED` | Each worker takes a disjoint row |
| Report needing a consistent snapshot | `REPEATABLE READ` | One snapshot for the whole transaction |
| Long report or export over a large table | `READ COMMITTED` with a bounded key range, or a repeatable-read snapshot on a replica | Limits snapshot age on the primary |
| Cross-row invariant with no expressible constraint | `SERIALIZABLE` | Detects the dangerous structure; requires retry |
| Reporting or analytics | `SERIALIZABLE` or a snapshot replica | Keeps heavy reads off the primary |

Two operational rules:

- **Never hold `SERIALIZABLE` on the primary for a workload you can measure.** It converts
  contention into aborts and CPU. Move it to a replica if you can.
- **Set a `statement_timeout` on every transaction**, so no single transaction holds a snapshot
  or a lock indefinitely.

## Checklist

- Every write that depends on a prior read is either a single conditional statement, a locking
  read, or a version check. Never a bare read-then-write at `READ COMMITTED`.
- Cross-row invariants are expressed as constraints where possible.
- Lock ordering is documented and consistent per code path.
- No transaction spans an external call or a user think.
- Isolation level is set per transaction, not by changing session defaults.
- `40001` and `40P01` are caught and retried with jittered backoff, and only when the
  transaction is idempotent.
- `statement_timeout` and `idle_in_transaction_session_timeout` are set.
- No `idle in transaction` sessions; the vacuum horizon is clean.
- Long analytical reads run against a replica or a bounded key range.

## References

- Transaction isolation: https://www.postgresql.org/docs/current/transaction-iso.html
- Concurrency control: https://www.postgresql.org/docs/current/mvcc-intro.html
- Explicit locking and lock modes: https://www.postgresql.org/docs/current/explicit-locking.html
- Explicit transactions and savepoints: https://www.postgresql.org/docs/current/transactions.html
- `SELECT` and the locking clauses: https://www.postgresql.org/docs/current/sql-select.html
- Error codes (SQLSTATE) appendix: https://www.postgresql.org/docs/current/errcodes-appendix.html
- `pg_stat_activity`: https://www.postgresql.org/docs/current/monitoring-stats.html
- Setting parameters: https://www.postgresql.org/docs/current/sql-set.html
- Index-only scans and visibility: https://www.postgresql.org/docs/current/indexes-index-only-scans.html
- MySQL InnoDB transaction isolation model: https://dev.mysql.com/doc/refman/en/ (InnoDB
  Transaction Model chapter)
- Jepsen analyses of database isolation behaviour: https://jepsen.io/analyses
