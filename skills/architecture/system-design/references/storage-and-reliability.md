# Storage Selection and Reliability Detail

## Database selection by access pattern

Write the query first, then pick.

| Access pattern | Storage | Watch for |
|---|---|---|
| Multi-row transaction over related data | Relational | Lock contention, hot rows |
| Read one record by key, no joins | Key-value | No secondary queries; think about how you list |
| Filter, sort, paginate a wide entity | Wide-column or document | Consistency model, index build time |
| Full text, relevance ranking, faceting | Search engine | Eventual; refresh lag; reindex cost |
| Aggregate over long time windows | Columnar or warehouse | Query concurrency limits |
| Append-only, replayable history | Log | Retention and compaction |
| Derived, read-heavy, sub-second | In-memory cache | Invalidation is your problem |

Anti-pattern: using a key-value store as the primary system of record for data that needs joins,
uniqueness across fields, or multi-record transactions, then rebuilding those guarantees in
application code.

## NoSQL does not remove constraints

Moving to a document store or a wide-column store changes where constraints live, not whether
they exist. Plan for:

- Uniqueness across non-key fields: an application check plus a lock, or a dedicated index.
- Referential integrity: application-enforced, with reconciliation to catch divergence.
- Transactions across aggregates: compensation, or a narrower aggregate boundary.

Write down which guarantees moved from the database to your code. That list is your new failure
surface.

## Indexing rules

- Every index has a write cost and a storage cost. Justify each.
- Composite index column order must match how the query filters and sorts.
- An index that no query uses is pure overhead.
- Uniqueness indexes are the cheapest way to enforce an invariant; use them instead of
  application checks where possible.
- Before adding an index to fix a slow query, read the query plan. Symptom-driven indexes
  accumulate.

## Replication and failover

| Mechanism | Provides | Adds |
|---|---|---|
| Read replicas | Read capacity, analytics isolation | Replication lag |
| Synchronous replication | Stronger durability | Write latency, availability coupling |
| Automatic failover | Recovery from instance loss | Failover time, split-brain risk |
| Multi-zone | Zone failure tolerance | Cost |
| Multi-region | Regional failure tolerance | Latency, consistency complexity |

For each, state the failover mechanism and its measured or expected time. "Multi-AZ" with no
failover story is one zone plus a bill.

## Backups

A backup that has never been restored is a hypothesis.

| Property | Question to answer |
|---|---|
| Frequency | What is the maximum data loss? RPO |
| Retention | How far back can you go? |
| Restore time | What is the recovery time objective? RTO |
| Isolation | Are backups in a separate account or failure domain? |
| Immutability | Can a compromised production credential delete them? |
| Verification | When did a restore last succeed, and how long did it take? |

Test restores on a schedule. Record the result. That record is the only evidence that exists.

## Error budgets

```
error budget = (1 - SLO) * measurement window
```

Use it to decide release pace:

| Budget consumed | Posture |
|---|---|
| Under half | Full release cadence |
| Between half and full | Feature freeze on the unreliable path, reliability work |
| Exhausted | Reliability only, new releases need sign-off |

An SLO without an error budget is a wish. With one, it changes behavior.

## Dependency failure matrix

For every outbound dependency:

| Dependency | Timeout | Retries | Degraded behavior | Circuit breaker |
|---|---|---|---|---|
| Payments | 2s | 0, idempotency key required | Fail the checkout, tell the user | Yes |
| Recommendations | 200ms | 1 | Hide the section | Yes |
| Search | 300ms | 1 | Fall back to a recent index or empty state | Yes |
| Analytics | fire and forget | bounded | Drop the event, queue it | No |

Set timeouts everywhere. A missing timeout is an unbounded thread or connection, and it is the
single most common cause of cascading failure. Use an overall deadline and derive per-call
deadlines from it.

## Circuit breaker shape

```python
class CircuitBreaker:
    """Closed -> Open on consecutive failures. Open -> HalfOpen after a cooldown."""

    def __init__(self, failure_threshold: int, cooldown_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def allow(self, now: float) -> bool:
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown_seconds:
            self.opened_at = None
            self.consecutive_failures = 0
            return True  # half-open: allow one probe
        return False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = now
```

Wrap every outbound call with: timeout, breaker, bounded retry with jitter, and a metric.
Retries without a breaker amplify an outage instead of absorbing it.