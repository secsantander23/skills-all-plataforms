# Locking, Deadlocks and Retry Reference

## Lock modes in PostgreSQL

Row-level locks are stored on the tuple, not in a separate table, and are held to the end of
the transaction.

| Lock | Held by | Conflicts with | Typical use |
| --- | --- | --- | --- |
| `FOR KEY SHARE` | `SELECT ... FOR KEY SHARE` | `FOR UPDATE` | Reference a row to be deleted later |
| `FOR SHARE` | `SELECT ... FOR SHARE` | `FOR UPDATE`, `FOR NO KEY UPDATE` | Read and hold against writers |
| `FOR NO KEY UPDATE` | `SELECT ... FOR NO KEY UPDATE` | `FOR UPDATE`, `FOR NO KEY UPDATE`, `FOR SHARE` | Claim a row without changing its key |
| `FOR UPDATE` | `SELECT ... FOR UPDATE` | All row locks | Exclusive claim on a row |

Modifiers:

- `NOWAIT`: raise `55P03` immediately instead of waiting.
- `SKIP LOCKED`: skip rows that are already locked.

## The work-queue pattern

```sql
-- Multiple workers pull disjoint rows. No worker ever waits for another.
WITH claimed AS (
    SELECT id
    FROM jobs
    WHERE status = 'ready'
      AND run_after <= now()
    ORDER BY run_after
    LIMIT 10
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs
SET status = 'running', attempts = attempts + 1
FROM claimed
WHERE jobs.id = claimed.id
RETURNING jobs.id, jobs.payload;
```

Why the CTE matters: `FOR UPDATE SKIP LOCKED` is evaluated and the rows locked in the CTE, then
the `UPDATE` uses that locked set. Doing `UPDATE ... WHERE ... LIMIT` without the locking CTE
does not give you the same guarantee.

Post-processing the claimed rows must happen in the **same transaction** as the claim. If you
return the ids and process them in another transaction, two workers can claim the same job.

## Consistent lock ordering

The single most effective deadlock prevention technique.

```python
def transfer(conn, from_id: int, to_id: int, cents: int) -> None:
    # Lock in ascending id order, always. Deterministic order means no cycle.
    lo, hi = sorted((from_id, to_id))
    with conn.transaction():
        for account_id in (lo, hi):
            row = conn.execute(
                "SELECT balance_cents FROM accounts WHERE id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown account {account_id}")
        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents - %s WHERE id = %s",
            (cents, from_id),
        )
        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents + %s WHERE id = %s",
            (cents, to_id),
        )
```

If the ordering has to come from a query, sort in the application before issuing locking
statements:

```sql
SELECT id FROM accounts WHERE id = ANY(%s) ORDER BY id FOR UPDATE;
```

## Deadlock detection and diagnosis

PostgreSQL logs a deadlock report naming the two processes, the conflicting statements, and the
rows involved. Find it in the server log.

```sql
-- Live lock waits right now.
SELECT pid, state, wait_event_type, wait_event,
       now() - query_start AS duration,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE wait_event_type = 'Lock'
ORDER BY query_start;
```

`deadlock_timeout` controls how long the engine waits before running deadlock detection. Lower
values detect sooner at a small CPU cost. `log_lock_waits` logs statements that wait for locks.

## Error codes for control flow

| SQLSTATE | Name | Retry? |
| --- | --- | --- |
| `40001` | `serialization_failure` | Yes |
| `40P01` | `deadlock_detected` | Yes |
| `55P03` | `lock_not_available` (NOWAIT, or a lock timeout reported this way) | Yes |
| `57014` | `query_canceled` (statement timeout, or explicit cancel) | Depends: usually surface it |
| `23505` | `unique_violation` | No: the data conflicts |
| `23503` | `foreign_key_violation` | No |
| `23514` | `check_violation` | No |
| `40003` | `statement_completion_unknown` (ambiguous commit) | Requires an idempotency check, not a blind retry |

`statement_completion_unknown` is the dangerous one: the commit may or may not have happened.
A blind retry can duplicate a payment. Handle it with an idempotency key stored in the same
transaction, and look the operation up by that key before retrying.

## Idempotency keys

```sql
CREATE TABLE payment_operations (
    idempotency_key text PRIMARY KEY,
    account_id      bigint NOT NULL REFERENCES accounts (id),
    amount_cents    bigint NOT NULL,
    provider_ref    text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- The key is the primary key, so a concurrent duplicate fails cleanly.
INSERT INTO payment_operations (idempotency_key, account_id, amount_cents)
VALUES ($1, $2, $3)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key;

-- Zero rows returned means this request already ran. Return the stored result.
```

Because the key insert is inside the same transaction as the business write, either both happen
or neither does. That is what makes a retry safe.

## Backoff and jitter

Synchronised retries are the classic mistake: if 1000 clients collide, all 1000 retry after the
same delay and collide again.

```python
import random

def backoff_delay(attempt: int, base: float = 0.05, cap: float = 2.0) -> float:
    return min(cap, base * (2 ** attempt)) * (0.5 + random.random())
```

Guidelines:

- Cap the delay so a request does not exceed its caller's timeout budget.
- Cap the number of attempts. Three to five is typical.
- Under heavy contention, more workers is the wrong answer. Reduce contention instead:
  narrower transactions, consistent ordering, or sharding the contended key.

## Advisory locks

Application-level mutual exclusion keyed by a value rather than a row.

```sql
-- Session-level: released explicitly or when the session ends.
SELECT pg_advisory_lock(4242);
SELECT pg_try_advisory_lock(4242);      -- returns false if already held
SELECT pg_advisory_unlock(4242);

-- Transaction-level: released automatically at commit or rollback. Prefer this.
SELECT pg_advisory_xact_lock(4242);
SELECT pg_try_advisory_xact_lock(4242);
```

Advisory locks are not respected by anything that does not take them, so they only coordinate
cooperating code. They are a good fit for "one job per tenant at a time" and a bad fit for
correctness invariants.

## Handling 40001 correctly in an ORM

Most frameworks offer a "retry on serialization failure" hook. Three things to verify before
trusting it:

1. **Does it restart the transaction, not the statement?** The transaction is aborted after a
   serialization failure; retrying a statement alone fails again.
2. **Is the work idempotent?** Side effects inside the retried body get duplicated. Move email,
   webhooks, and payment calls after the commit, or guard them with an idempotency key.
3. **Is the retry bounded?** Infinite retries convert a hard failure into a hung request.
