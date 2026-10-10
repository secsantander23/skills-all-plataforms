# Isolation Level Behaviour Reference

## What the standard promises versus what engines deliver

The SQL standard defines isolation levels as minimum guarantees. Engines implement them with
different mechanisms and different edge cases. Always verify against your engine's own
documentation.

| Level | Standard minimum | Typical mechanism | Notable engine differences |
| --- | --- | --- | --- |
| Read Uncommitted | Dirty reads allowed | Reads the current row version | PostgreSQL treats it as Read Committed. Some engines do not offer it. |
| Read Committed | No dirty reads | New snapshot per statement, or per-row locking reads | Statement-level snapshots are what make phantoms and non-repeatable reads possible |
| Repeatable Read | No dirty or non-repeatable reads | Transaction-wide snapshot, or gap locks | PostgreSQL allows phantoms under snapshot isolation. MySQL InnoDB uses next-key locks and prevents a wider set of phantoms but blocks more. |
| Serializable | Full serializability | Two-phase locking (blocking) or SSI (abort-and-retry) | PostgreSQL uses SSI. MySQL InnoDB uses locking plus gap locks. |

## PostgreSQL transaction-level anomaly summary

Under the default `READ COMMITTED`, from a practical standpoint, all of these are possible:
dirty reads (no, those are prevented), non-repeatable reads, phantoms, serialization failures
(no, not at this level), and read/write conflicts resolved by locking rather than detection.

The practical list that matters:

- `READ COMMITTED`: statement-level snapshots. Non-repeatable reads and phantoms yes. Lost
  updates yes if your write is a separate statement.
- `REPEATABLE READ`: transaction snapshot. Phantoms yes for non-locking reads. Serialization
  failures possible.
- `SERIALIZABLE`: transaction snapshot plus dependency tracking. Serialization failures
  possible; retry required.

## SSI in one page

Serializable Snapshot Isolation does not lock rows in advance. Instead:

1. Each transaction reads the transaction snapshot.
2. Reads and writes create read-write dependency edges between concurrent transactions.
3. If there is a cycle of dependencies forming a dangerous structure, one transaction is
   aborted with `serialization_failure` (`40001`) at commit time or during execution.

Practical consequences:

- Failures happen **at commit**, not at the statement that caused the conflict. A transaction
  can do hours of work and be aborted at the end.
- Read-only transactions still track dependencies and can be aborted. This surprises people who
  assume reads are free.
- Tracking has a cost, so `SERIALIZABLE` is more CPU-hungry than `REPEATABLE READ`.
- Retrying is mandatory, and the retried transaction must be idempotent.
- You can reduce the abort rate by keeping transactions small and, where possible, by touching
  rows in a consistent order.

## Snapshot visibility rules worth internalising

Under MVCC, "the value I see" depends on which snapshot the statement used and whether the
committing transaction is visible in that snapshot.

```
T1: BEGIN; UPDATE accounts SET balance = 70 WHERE id = 1;   -- uncommitted
T2: SELECT balance FROM accounts WHERE id = 1;              -- sees the pre-update value
T1: COMMIT;
T2: SELECT balance FROM accounts WHERE id = 1;              -- new statement, new snapshot, sees 70
T2: SELECT balance FROM accounts WHERE id = 1;              -- Repeatable Read: still sees 100
```

This is why "the second SELECT in the same request returned a different value" is normal at
Read Committed and a bug at Repeatable Read.

## Blocking reads and their level interactions

| Query | Read Committed | Repeatable Read | Serializable |
| --- | --- | --- | --- |
| Plain `SELECT` | Statement snapshot, no blocking | Transaction snapshot, no blocking | Transaction snapshot, no blocking |
| `SELECT ... FOR UPDATE` | Row locks, re-checks the row after the lock is granted | Row locks, uses the transaction snapshot | Row locks plus predicate tracking |
| `SELECT ... FOR SHARE` | Row locks, shared | Row locks, shared | Row locks plus predicate tracking |

In PostgreSQL, at `REPEATABLE READ`, a locking read can raise a serialization error when the
row was modified by a transaction that committed after the snapshot was taken. That is
documented behaviour, not a bug. If you use `FOR UPDATE` at Repeatable Read, handle `40001`.

## Isolation levels for specific problems

**Report that must be internally consistent.** Use one transaction at `REPEATABLE READ` and
keep it short. Or use a snapshot replica.

**Report that scans a huge table on the primary.** Use bounded key ranges at `READ COMMITTED`,
or move it to a replica. A long transaction at any level holds back vacuum and creates bloat.

**Double-entry bookkeeping.** Single-statement conditional updates at `READ COMMITTED`, plus
a uniqueness constraint that makes double-spending impossible. Prefer constraints over
isolation.

**Approval workflow where two approvers must not both approve.** Either model it as a state
machine with a conditional update (`UPDATE ... SET state='approved' WHERE id=? AND
state='pending'`), or use `SERIALIZABLE` and retry. The conditional update is cheaper and
deterministic.

**Occupancy counter where overbooking must be impossible.** `UPDATE rooms SET available =
available - 1 WHERE id = ? AND available > 0` then check the affected row count. The check and
the decrement are one statement, so there is no window.

## Isolation in non-PostgreSQL engines

Read your engine's documentation for these specifics, because they are the usual source of
"this behaves differently in staging":

- **MySQL InnoDB**: default `REPEATABLE READ`, next-key locking on range scans, separate
  configuration for read-only transactions, and `NOWAIT`/`SKIP LOCKED` variants of locking
  reads.
- **SQL Server**: `READ COMMITTED` takes shared locks that are released as it reads, which
  produces non-repeatable reads and allows conversion deadlocks under load. `SNAPSHOT`
  isolation requires enabling it database-wide, which changes the storage requirement and
  enables row versioning. `UPDLOCK`, `HOLDLOCK`, `READPAST`, and `UPDLOCK, READPAST` are
  commonly used as locking hints.
- **Oracle**: `READ COMMITTED` by default with statement-level consistency; `SERIALIZABLE` uses
  serializable transactions with statement-level rollback.
- **SQLite**: a single-writer model. Writers block writers. Exact isolation and locking
  behaviour depends on the journal mode and version; check the current SQLite documentation.

## Verifying your assumption

PostgreSQL:

```sql
SHOW transaction_isolation;
SHOW default_transaction_isolation;

BEGIN;
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SHOW transaction_isolation;
COMMIT;
```

Run the same query twice inside the transaction against a table another session is modifying,
and observe whether the second read sees the new value. That is the fastest way to confirm the
level you actually have, including any change made by your ORM or connection pooler.
