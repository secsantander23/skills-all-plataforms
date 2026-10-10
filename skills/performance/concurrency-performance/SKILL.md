---
name: concurrency-performance
description: Concurrency and throughput tuning covering threads versus processes versus async, connection pools, bounded concurrency and backpressure, lock contention, batching, async versus blocking I/O, and head-of-line blocking. Use when the user reports low throughput, connection pool exhaustion, thread pool starvation, lock contention, a growing queue or 429 problem, or asks how to make an I/O-bound workload faster.
metadata:
  opencode/autoinvoke: true
---

# Concurrency Performance

Concurrency is a resource-allocation problem. Every concurrent unit — a thread, a process,
a connection, a queue slot, a lock — consumes a bounded resource, and exceeding that bound
converts throughput into latency, latency into timeouts, and timeouts into retries, which
converts a mild overload into a collapse. Most "the service is slow" incidents are really
"the concurrency limit is in the wrong place, or unset".

## When to use this

- Throughput plateaus while latency climbs, or latency climbs while CPU is idle.
- Connection pool exhaustion, "too many connections", or database max connections errors.
- A thread pool is saturated, or an async runtime reports pending tasks.
- A queue grows without bound and workers cannot keep up.
- You want to raise throughput and need to know whether to add threads, processes, or
  concurrency inside one thread.
- You see lock contention, high context-switch counts, or high system time.
- A slow dependency makes your whole service slow even though your own work is trivial.

## Quick start

Bound your concurrency, measure the queue, and refuse work early:

```js
// bounded-concurrency.js
import pLimit from 'p-limit';

const limit = pLimit(Number(process.env.OUTBOUND_CONCURRENCY ?? 20));

export async function fetchAll(ids, { signal } = {}) {
  // Bounded fan-out: at most `limit` in flight, regardless of how many ids there are.
  return Promise.all(ids.map((id) => limit(() => client.get(`/items/${id}`, { signal }))));
}
```

```js
// Measure the wait, not just the work. A semaphore that always has waiters is your
// bottleneck's location.
import { monitorEventLoopDelay } from 'node:perf_hooks';
const h = monitorEventLoopDelay({ resolution: 10 });
h.enable();
setInterval(() => {
  console.log({
    loopP99Ms: Number((h.percentile(99) / 1e6).toFixed(2)),
    loopMaxMs: Number((h.max / 1e6).toFixed(2)),
  });
  h.reset();
}, 1000).unref();
```

```bash
# Where is the process spending time and how much of it is kernel time?
ps -o pid,etime,time,pcpu,rss,nlwp,cmd -p <PID>     # POSIX
cat /proc/<PID>/status | grep -E 'Threads|voluntary_ctxt'  # POSIX
```

```powershell
Get-Process -Id <PID> | Select-Object Id,CPU,Threads.Count,HandleCount,WorkingSet64
```

## Threads, processes, or async

| Model | Parallelism limited by | Good when | Cost |
| --- | --- | --- | --- |
| Threads in one process | Language runtime, GIL if present, shared-state races | Work is I/O bound or the runtime releases the GIL; shared memory needed | Context switch cost, lock complexity, one crash kills all |
| Processes | Cores and memory | Work is CPU bound, or you need fault isolation | Inter-process data cost, serialization, higher per-process memory |
| Async/await, single thread | Event loop throughput, one call stack at a time per thread | Work is dominated by waiting on many I/O operations | A single blocking call stalls everything |
| Async plus a thread or process pool | Pool size plus event loop | Mixed: many waits plus some CPU work | Two models to reason about at once |
| Multiple async threads | Per-thread event loop plus runtime constraints | Very high connection counts with a runtime that scales threads | Memory per thread; some runtimes thread-pool all of it behind the scenes |

Decision rule, in order: if the work is CPU bound, add parallel execution *units*
(processes or native threads) — concurrency inside one thread cannot help. If the work is
I/O bound, prefer async on a small number of threads over many OS threads, because thread
stacks and context switches cost more than the work you are waiting for. If the runtime
has a global interpreter lock and your work is pure Python CPU work, no amount of
threading helps; use processes.

```python
# CPU-bound: processes.
from concurrent.futures import ProcessPoolExecutor
with ProcessPoolExecutor() as pool:
    results = list(pool.map(parse_record, records))

# I/O-bound: threads (Python releases the GIL around socket and file I/O).
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=32) as pool:
    results = list(pool.map(fetch_url, urls))
```

## Blocking I/O inside async code

This is the single most common cause of "async is slow". A blocking call on the event loop
thread stops every other coroutine, including unrelated requests.

```js
// Wrong: fs.readFileSync and the synchronous JSON parse both stall the loop.
import fs from 'node:fs';
app.get('/config', (req, res) => {
  const raw = fs.readFileSync('./config.json', 'utf8');
  res.json(JSON.parse(raw));
});

// Right: await the async API.
app.get('/config', async (req, res) => {
  const raw = await fs.promises.readFile('./config.json', 'utf8');
  res.json(JSON.parse(raw));
});
```

```js
// Wrong: unbounded map over a large list.
const results = await Promise.all(ids.map((id) => heavyWork(id)));

// Right: bounded, so memory and downstream pressure stay flat.
const results = await Promise.all(ids.map((id) => limit(() => heavyWork(id))));
```

In Python, blocking calls in an asyncio program (`time.sleep`, `requests.get`, synchronous
file I/O) stall the loop. Use `asyncio.sleep`, an async HTTP client, and
`asyncio.to_thread` for genuinely blocking work that has no async equivalent:

```python
import asyncio

async def handler(record):
    # Offload the blocking parse; do not call it directly on the loop.
    return await asyncio.to_thread(parse_record, record)

results = await asyncio.gather(*(handler(r) for r in records))
```

Confirm you have fixed it by watching the event-loop delay histogram, not by intuition.
A loop whose p99 delay is a small multiple of its p50 is healthy; a loop whose max delay
spikes to hundreds of milliseconds is being blocked.

## Connection pools

A connection pool amortizes the cost of connection establishment (TLS handshake, DNS,
authentication) and caps how many connections a downstream can be asked to hold. Pools are
the most common throughput limit in a service that talks to a database.

Pool sizing: start with something modest and increase until latency regresses, because
past a point more connections mean more lock contention inside the database, not more
throughput. The classic symptom is throughput that improves slightly, then plateaus, then
falls while the database's CPU and lock-wait metrics climb.

| Pool parameter | Effect of raising it | Effect of lowering it |
| --- | --- | --- |
| Max size | More in-flight queries, more downstream contention | Fewer connections, more queueing in your app |
| Min idle size | Fewer connection-establishment stalls at low load | More idle resources held |
| Idle timeout | Reuses connections longer | Returns resources sooner, pays handshake cost |
| Max lifetime | Long-lived connections never reaped | More handshake churn, avoids upstream idle-closing |
| Acquire timeout | Longer waits, clearer errors under load | Fails fast, surfaces the bottleneck sooner |

```js
// node-postgres pool sizing. Verify option names against your driver's docs.
import pg from 'pg';
const pool = new pg.Pool({
  host: process.env.PGHOST,
  database: process.env.PGDATABASE,
  user: process.env.PGUSER,
  max: Number(process.env.PGPOOL_MAX ?? 10),
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
  pool.on('error', (err) => console.error('idle client error', err.message)),
});
await pool.query('SELECT 1');
```

Rules that hold across stacks:

- One pool per process (or per worker), sized so that *processes × pool size* stays under
  the database's connection limit, with headroom for administration and replication.
- Always return or close the connection. Leaked connections show up as steady growth in the
  pool's checked-out count.
- Set an acquire timeout. Without one, a pool-exhaustion event becomes an unbounded hang
  instead of an error you can observe.
- Time out the query too, and cancel it on the client side when the caller gives up.

```sql
-- PostgreSQL: who is connected and what they are doing.
SELECT state, count(*) FROM pg_stat_activity GROUP BY state;
SELECT pid, now() - query_start AS runtime, wait_event_type, wait_event, left(query, 80)
  FROM pg_stat_activity
 WHERE state <> 'idle' AND now() - query_start > interval '1 second'
 ORDER BY runtime DESC;
```

`wait_event_type` values such as `Lock`, `IO`, `LWLock` tell you where the database is
blocked. This is a much more direct signal than guessing from your application's latency.

## Backpressure and bounded concurrency

Unbounded concurrency converts a slow dependency into an outage: every incoming request
opens another connection, the downstream queue fills, timeouts fire, retries multiply
load, and the system never recovers on its own.

A bounded system has, in order of preference:

1. **A queue with a depth limit.** Accept work until full, then shed.
2. **A concurrency limit.** A semaphore around the actual work.
3. **A deadline propagated from the caller.** Stop doing work nobody will consume.

```js
// A work queue with backpressure: bounded depth, and a refusal when full.
class WorkQueue {
  #limit;
  #queue = [];
  #waiters = [];
  #running = 0;

  constructor(limit) {
    this.#limit = limit;
  }

  push(task) {
    if (this.#queue.length >= this.#limit) return false;   // shed
    this.#queue.push(task);
    this.##drain();
    return true;
  }

  #drain() {
    while (this.#running < this.#limit && this.#queue.length > 0) {
      const task = this.#queue.shift();
      this.#running++;
      Promise.resolve()
        .then(task)
        .catch(() => {})
        .finally(() => { this.#running--; this.#drain(); });
    }
  }
}
```

Overload signals, from the outside in, in the order you should act on them:

| Signal | Meaning | Response |
| --- | --- | --- |
| Queue depth rising, latency flat | Absorption working so far | Watch it; it is early warning |
| Queue depth rising, latency rising | Absorption exhausted | Shed load; scale capacity |
| Timeouts and errors rising | Saturation is now visible to users | Shed, then reduce work per request |
| Retries visible in traffic | Amplification loop | Cap retries, add jitter, cut load |
| Crash or restart | Failed to shed | Add the bound; it was missing |

```js
// Bounded retry with jitter. Retries without a cap and jitter cause thundering herds.
async function withRetry(fn, { attempts = 3, baseMs = 50, signal } = {}) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn({ signal });
    } catch (err) {
      if (attempt >= attempts || !isRetryable(err)) throw err;
      const backoff = baseMs * 2 ** (attempt - 1);
      const jitter = Math.random() * backoff;
      await sleep(backoff + jitter, { signal });
    }
  }
}
```

Retry budgets work better than attempt caps: cap total retries as a fraction of requests so
a dependency that is fully down cannot receive more retry load than it was getting
originally.

## Head-of-line blocking

Head-of-line blocking is a unit of work waiting behind work it does not depend on. It hides
in several places:

- **Single-connection HTTP/1.1 multiplexing.** Requests on one connection are serialized
  unless the origin streams responses out of order. HTTP/2 and HTTP/3 multiplex over one
  connection and largely remove this.
- **A single database connection per process** when concurrency would otherwise help.
- **A worker pool where one slow task occupies a slot** that dozens of fast tasks wait for.
  This is why per-task timeouts matter: without them, a single hung task removes capacity.
- **A single-threaded queue drained serially** while workers exist downstream.
- **Head-of-line in a batching layer.** A batch that is waiting to fill blocks items that
  could already have been processed.

Mitigations: shard the work across independent queues; enforce per-task deadlines so a
slot is never held indefinitely; use bounded concurrency so slow work cannot consume the
whole pool; split a batch's timeout so a stuck item cannot hold the batch.

## Lock contention

Contention shows up as time in kernel or scheduler, high context-switch counts, and
throughput that does not improve when you add cores.

```bash
# POSIX: context switches and per-thread state.
cat /proc/<PID>/status | grep -E 'Threads|voluntary_ctxt_switches|nonvoluntary_ctxt_switches'

# Sampling a running process; expand to the futex/lock layer if available.
perf top -p <PID>
perf record -e context-switches -g -p <PID> -- sleep 10
perf report --stdio
```

```sql
-- PostgreSQL: lock waits.
SELECT locktype, relation::regclass, mode, granted, count(*)
  FROM pg_locks
 GROUP BY 1, 2, 3, 4
 ORDER BY count(*) DESC;

SELECT pid, wait_event_type, wait_event, state, left(query, 100)
  FROM pg_stat_activity
 WHERE wait_event_type = 'Lock';
```

Reductions, roughly in order of impact:

1. **Shrink the critical section.** Move I/O and logging out of the lock.
2. **Replace a shared lock with a partitioned one.** Shard by key so unrelated work never
   contends.
3. **Prefer single-writer structures** over many-writer ones. Append-only logs and
   single-producer queues avoid most lock traffic.
4. **Use a better algorithm** before adding a cache of the lock. Read-write locks,
   copy-on-write snapshots, or immutable data eliminate contention rather than serializing
   it.
5. **Use atomics or thread-local state** for counters where a lock is overkill.

A `synchronized` block that wraps a network call is the archetypal mistake: the lock is
held for the slowest operation in the system.

## Batching

Batching amortizes per-operation overhead — round trips, syscalls, index lookups, log
writes. It is usually the cheapest throughput win available.

```sql
-- One multi-row insert instead of N inserts. PostgreSQL and MySQL both support this.
INSERT INTO events (id, kind, payload) VALUES
  ($1, $2, $3), ($4, $5, $6), ($7, $8, $9);
```

```js
// Batch database writes behind a flush interval or a size trigger.
class BatchWriter {
  #rows = [];
  #timer = null;

  constructor({ flushSize = 500, flushMs = 100, write }) {
    this.#flushSize = flushSize;
    this.#write = write;
  }

  add(row) {
    this.#rows.push(row);
    if (this.#rows.length >= this.#flushSize) return this.#flush();
    this.#timer ??= setTimeout(() => this.#flush(), this.#flushMs).unref();
    return Promise.resolve();
  }

  async #flush() {
    clearTimeout(this.#timer);
    this.#timer = null;
    const batch = this.#rows.splice(0, this.#rows.length);
    if (batch.length) await this.#write(batch);
  }
}
```

Batch tradeoffs to know before you ship one:

- **Latency.** Batching adds wait time for the oldest item in the batch. Flush on size
  *or* on time, whichever comes first.
- **Partial failure.** One bad row fails the batch. Use a multi-row insert that reports
  per-row results, or size batches so retrying is cheap.
- **Lock duration.** A large batch holds a longer lock. Large writes can block more than
  many small ones.
- **Memory.** Unbounded batches are a memory leak. Cap size and flush interval.
- **Tail.** The last few items before a flush wait. A long flush interval is a latency
  bug even though throughput looks great.

## Checklist

- [ ] Determined whether the workload is CPU bound or I/O bound before choosing a model.
- [ ] All fan-out is bounded by a semaphore or a concurrency limit.
- [ ] No blocking calls on the event loop; verified with an event-loop delay histogram.
- [ ] Every pool has a max size, an acquire timeout, and a query timeout.
- [ ] `processes × pool size` is under the downstream connection limit, with headroom.
- [ ] Connections are returned in a `finally` block on every path.
- [ ] Queue depth is bounded and shedding is implemented and tested.
- [ ] Retries are capped, jittered, and restricted to retryable errors.
- [ ] Per-task deadlines exist so one hung task cannot hold a worker slot.
- [ ] Context-switch and lock-wait metrics are monitored, not guessed at.
- [ ] Batching is bounded by size and time, with partial-failure handling.
- [ ] Throughput and latency are measured together; neither is optimized alone.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Throughput flat, latency rising as load rises | Unbounded concurrency, downstream saturating | Bound concurrency, shed load, propagate deadlines |
| `FATAL: sorry, too many clients already` | Pool total exceeds `max_connections` | Reduce `processes × pool size`; add a pooler such as PgBouncer |
| Intermittent multi-second stalls | One slow dependency blocking a shared thread or connection pool | Per-task timeouts; shard pools per dependency |
| CPU idle but queue deep | Waiting on I/O or a lock, not on CPU | Look at wait events and pool wait, not CPU |
| Everything slow after adding workers | More contention inside the shared resource | Partition the resource instead of adding more clients |
| Latency spikes every N seconds | Batching flush interval, or a periodic background job | Move work off the hot path or stagger it |
| Errors only under load, with many 429s or 503s | Retry storm amplifying an overload | Cap retries, add jitter, shed earlier |
| Deadlock under concurrency | Inconsistent lock ordering | Global lock order; prefer lock-free structures |
| Hangs that never time out | No acquire timeout on the pool | Set one and treat exhaustion as an error |

## Anti-patterns

- **`Promise.all` over an unbounded collection.** Creates a task per element, which is a
  denial-of-service vector against yourself.
- **Adding cores to fix a lock problem.** More threads on a contended lock is strictly
  worse.
- **A connection pool sized "as large as possible".** Large pools move the bottleneck into
  the database, where it is harder to see and harder to shed.
- **Retrying everything.** Non-idempotent operations retried create duplicate work; 4xx
  retried just adds latency.
- **No jitter.** Every client that failed at the same instant retries at the same instant.
- **A thread per request** in a runtime where the thread stack and context switch cost more
  than the request.
- **Buffering without a bound.** An unbounded queue is not backpressure; it is a delayed
  outage.
- **Holding a lock across an await or an I/O call.** It converts a fast critical section
  into the slowest operation in the system.
- **Measuring only mean throughput.** A system with rising mean and collapsing p99 is
  failing, not improving.

## References

- Node.js documentation, the event loop and diagnostics
  - <https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop>
  - <https://nodejs.org/api/perf_hooks.html>
- `p-limit`, promise concurrency limiting
  - <https://github.com/sindresorhus/p-limit>
- PostgreSQL documentation, connection management and `pg_stat_activity`
  - <https://www.postgresql.org/docs/current/runtime-config-connection.html>
  - <https://www.postgresql.org/docs/current/monitoring-stats.html>
- PgBouncer
  - <https://www.pgbouncer.org/>
- Python `asyncio` documentation
  - <https://docs.python.org/3/library/asyncio.html>
- Python `concurrent.futures`
  - <https://docs.python.org/3/library/concurrent.futures.html>
- Go memory model and concurrency
  - <https://go.dev/ref/mem>
- Java `java.util.concurrent`
  - <https://docs.oracle.com/javase/8/docs/api/java/util/concurrent/package-summary.html>
- HTTP/2 multiplexing (RFC 9113)
  - <https://www.rfc-editor.org/rfc/rfc9113.html>
- Google SRE book, "Handling Overload"
  - <https://sre.google/sre-book/handling-overload/>
- Little's Law applied to queueing
  - <https://sre.google/sre-book/littles-law-for-performance-testing/>

## Deeper detail

- `references/connection-pools.md` — pool sizing math, per-stack configuration, pooler
  deployment shapes, timeouts, and leak diagnosis.
- `references/async-and-backpressure.md` — event loop mechanics, structured concurrency,
  cancellation, deadlines, and queue-and-shedding designs.
