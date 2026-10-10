# Async, Backpressure, and Deadlines

## Event loop mechanics

A single-threaded event loop runs to completion per turn: it takes one callback, runs it to
completion, then takes the next. Anything synchronous inside that callback blocks every
other request handled by that thread.

```
poll phase      -> wait for I/O readiness, run I/O callbacks
check phase     -> setImmediate callbacks
close callbacks -> socket 'close' events
```

Between turns the loop advances timers. A long synchronous callback delays every timer,
every I/O callback, and every other request on that thread.

```js
// Detect a blocked loop with the built-in histogram (Node 11.10+).
import { monitorEventLoopDelay } from 'node:perf_hooks';

const h = monitorEventLoopDelay({ resolution: 10 });
h.enable();
const t = setInterval(() => {
  console.log({
    meanMs: +(h.mean / 1e6).toFixed(2),
    p50Ms: +(h.percentile(50) / 1e6).toFixed(2),
    p99Ms: +(h.percentile(99) / 1e6).toFixed(2),
    maxMs: +(h.max / 1e6).toFixed(2),
  });
  h.reset();
}, 5000);
t.unref();
```

Interpretation: mean close to zero with a large max means occasional blocking work.
Mean itself elevated means the loop is continuously busy, which is healthy for an
I/O-bound server and unhealthy for a mostly-idle one.

## Blocking operations to find and fix

| Operation | Blocking form | Non-blocking form |
| --- | --- | --- |
| File read | `fs.readFileSync` | `fs.promises.readFile` or `fs.createReadStream` |
| File write | `fs.writeFileSync` | `fs.promises.writeFile` |
| DNS lookup | `dns.lookupSync`-style helpers | `dns.promises.lookup` |
| Hashing a large buffer | `crypto.createHash(...).digest()` inline | `crypto.hash()` (async), or a worker thread |
| Compression | `zlib.gzipSync` | `zlib.gzip` (callback/promise) |
| `JSON.parse` on a huge string | inline | Move to a worker, or precompute at startup |
| Database client | Synchronous driver calls | Async driver |
| HTTP client | Synchronous request library | Async client |

`worker_threads` is the general escape hatch for CPU-bound work in Node:

```js
import { Worker } from 'node:worker_threads';

function runInWorker(fnSource, payload) {
  return new Promise((resolve, reject) => {
    const w = new Worker(fnSource, { workerData: payload });
    w.once('message', resolve);
    w.once('error', reject);
    w.once('exit', (code) => {
      if (code !== 0) reject(new Error(`worker exited ${code}`));
    });
  });
}
```

Workers cost memory and serialization per message. Send compact payloads (IDs, not
documents) and batch messages.

## Structured concurrency and cancellation

Unstructured concurrency is how orphaned work piles up: a request is cancelled but its
downstream calls keep running, consuming connections and tokens nobody will use.

```js
// Tie the whole call tree to the client's disconnect.
app.get('/report', async (req, res) => {
  res.on('close', () => {
    ac.abort();                       // AbortController from node:abort_controller or global
  });
  const ac = new AbortController();
  const data = await fetchReport({ signal: ac.signal });
  if (!ac.signal.aborted) res.json(data);
});
```

In Python, `asyncio.TaskGroup` propagates cancellation and failure to sibling tasks, which
is the closest equivalent to structured concurrency:

```python
import asyncio

async def gather_all(ids):
    async with asyncio.TaskGroup() as tg:
        tasks = {tg.create_task(fetch_one(i)): i for i in ids}
    return {i: t.result() for t, i in tasks.items()}
```

In Go, `context.Context` is the cancellation spine: pass it down, honor `ctx.Done()`, and
every library call that accepts a context will unwind with you.

```go
ctx, cancel := context.WithTimeout(parent, 2*time.Second)
defer cancel()
req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
```

## Deadlines

A deadline is a budget that travels with the request. Propagating it is what stops a slow
branch of the call tree from consuming unbounded time and resources.

```js
// Combine an inbound deadline with a local cap, then propagate the signal.
import { setTimeout as delay } from 'node:timers/promises';

function withDeadline(parentSignal, budgetMs) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(new Error('deadline exceeded')), budgetMs);
  const onAbort = () => ac.abort(parentSignal?.reason);
  if (parentSignal) {
    if (parentSignal.aborted) onAbort();
    else parentSignal.addEventListener('abort', onAbort, { once: true });
  }
  ac.signal.addEventListener('abort', () => {
    clearTimeout(timer);
    parentSignal?.removeEventListener('abort', onAbort);
  }, { once: true });
  return ac.signal;
}
```

Rules that pay off:

- Every outbound call receives a signal. A call with no signal is uncancellable work.
- The budget shrinks as the request goes deeper. If the caller allows 2s and one hop is
  already using 1.2s, the next hop should not be handed 2s.
- A deadline that is routinely hit is a capacity or dependency problem, not a timeout-tuning
  problem. Raising the deadline hides the symptom.

## Shedding

Load shedding means refusing work before the system fails. It is a feature, not an
admission of weakness.

```js
// Token-bucket admission control.
class SheddingLimiter {
  #tokens;
  #capacity;
  #refillPerMs;

  constructor({ capacity, refillPerSec }) {
    this.#capacity = capacity;
    this.#tokens = capacity;
    this.#refillPerMs = refillPerSec / 1000;
  }

  tryAcquire(now) {
    this.#tokens = Math.min(
      this.#capacity,
      this.#tokens + (now - (this.#last ?? now)) * this.#refillPerMs,
    );
    this.#last = now;
    if (this.#tokens < 1) return false;
    this.#tokens -= 1;
    return true;
  }
}
```

```js
// Shed with an honest response. Retrying a 503 without backoff makes it worse.
if (!limiter.tryAcquire(Date.now())) {
  res.writeHead(503, {
    'Content-Type': 'application/json',
    'Retry-After': '5',
  });
  res.end(JSON.stringify({ error: 'overloaded' }));
  return;
}
```

Priority tiers go further: reject interactive traffic before batch traffic when both
compete for the same saturated resource. Separate the two workloads into separate
deployments with separate quotas, and the fight disappears.

## Queue design

| Queue property | Choice | Consequence |
| --- | --- | --- |
| Bound | Hard max depth | Beyond it, shed or block; never grow without limit |
| Policy | FIFO / priority / fair-share | Priority queues starve FIFO queues without aging |
| Persistence | In-memory vs durable | Durable queues survive restarts and cost latency |
| Delivery | At-most-once vs at-least-once | At-least-once requires idempotent consumers |
| Overflow | Drop newest / drop oldest / spill to disk | Each loses different work; choose explicitly |
| Backpressure to producer | Block, reject, or sample | Rejecting is usually correct for request paths |

A durable queue plus at-least-once delivery is the right shape for work you cannot lose.
It also means you must handle duplicates, poison messages, and consumer lag. A queue is
not free reliability; it is a system you now operate.

## Retry semantics

```js
function isRetryable(err) {
  // Transport errors and explicit retry signals.
  return err.code === 'ECONNRESET' ||
         err.code === 'ETIMEDOUT' ||
         err.code === 'EAI_AGAIN' ||
         err.status === 429 ||
         (err.status >= 500 && err.status <= 599);
}
```

- Never retry non-idempotent operations without an idempotency key.
- Retry only on errors that a later attempt could plausibly fix.
- Exponential backoff with full jitter. Fixed backoff synchronizes retries.
- Cap attempts *and* cap total retries as a fraction of requests.
- Honour `Retry-After` when the server sends it.
- Never retry from inside a retry. Nested retries multiply: three layers of three retries
  is twenty-seven attempts against a service that is already failing.

## Cancellation and partial failure

```js
// Fail fast when independent sub-tasks cannot all succeed, and release their resources.
const results = await Promise.allSettled(tasks);
const failures = results
  .map((r, i) => (r.status === 'rejected' ? { i, reason: r.reason?.message } : null))
  .filter(Boolean);
if (failures.length > 0) {
  throw new AggregateError(failures, `${failures.length}/${results.length} subtasks failed`);
}
```

`Promise.allSettled` plus an explicit failure threshold is usually better than `Promise.all`
for fan-out: one failed element should not discard ninety-nine successful ones, and one
slow element should not delay the whole response past the deadline.

## Verifying before relying on this

Runtime scheduling internals and flag names change between major versions. Confirm
against the documentation for your version: <https://nodejs.org/api/perf_hooks.html>,
<https://docs.python.org/3/library/asyncio.html>, <https://go.dev/context>.
