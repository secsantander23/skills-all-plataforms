# Memory and Garbage Collection

## What is actually growing

RSS is not the same as live heap. Before chasing a leak, separate:

- **Live heap** — objects still reachable. This is what a heap snapshot measures.
- **RSS** — pages the process has touched, including freed-but-not-returned memory.
- **Off-heap** — native allocations from addons, buffers, and the allocator's arenas.

```js
const m = process.memoryUsage();
console.log({
  rssMiB: (m.rss / 1048576).toFixed(1),
  heapTotalMiB: (m.heapTotal / 1048576).toFixed(1),
  heapUsedMiB: (m.heapUsed / 1048576).toFixed(1),
  externalMiB: (m.external / 1048576).toFixed(1),
  arrayBuffersMiB: (m.arrayBuffers / 1048576).toFixed(1),
});
```

If RSS grows but live heap stays flat, suspect fragmentation, native buffers, or a
dependency caching something off-heap.

## Heap snapshot anatomy

A snapshot is a graph. The DevTools **Summary** view shows, per constructor, the shallow
size, retained size, and object count. The useful views are:

- **Summary** — which class of object grew between two snapshots.
- **Comparison** — the delta between snapshot A and snapshot B.
- **Retainers** — the path from a GC root to the object. This is the leak explanation.
- **Paths to GC roots** — do this for the object you care about.

Dominators view answers "if I free this, what else goes with it".

## Leak taxonomies

| Pattern | How it shows up in a snapshot | Typical fix |
| --- | --- | --- |
| Unbounded cache/map keyed by user input | Growing `Map`, `Array`, or plain object with many keys and mixed shapes | Bound the cache (LRU / TTL / size cap) |
| Event listener accumulation | Growing node count with identical event handlers | Remove listeners on teardown; use `AbortController` and `signal` |
| Detached DOM nodes | Node objects whose `Document` is detached | Release references on route change; remove the framework's DOM cache |
| Closures capturing context | Growing closure count retaining large arrays | Pass explicit arguments; avoid capturing whole request objects |
| Subscribed-but-unsubscribed clients | Growing callback/observer count | Tie lifecycle to connection teardown, not to process lifetime |
| Timers never cleared | Growing timers holding references | Clear on shutdown, or use `.unref()` for fire-and-forget timers |
| Unbounded string accumulation in a module-level buffer | One enormous string object | Bound it; log to stdout and let the collector aggregate |

## Forced GC and repeatability

```js
// Run with: node --expose-gc bench/leak.js
function gc() {
  if (typeof global.gc === 'function') {
    global.gc();
    global.gc();   // twice: the first may promote survivors into the old generation
  }
}

async function measure(label, work) {
  gc();
  const before = process.memoryUsage().heapUsed;
  await work();
  gc();
  const after = process.memoryUsage().heapUsed;
  console.log({
    label,
    retainedMiB: ((after - before) / 1048576).toFixed(2),
  });
}

await measure('single pass', async () => {
  for (let i = 0; i < 1000; i++) createRequestObject(i);
});
await measure('second pass', async () => {
  for (let i = 0; i < 1000; i++) createRequestObject(i);
});
```

If retained memory keeps climbing between identical passes, you have a retention leak. If
it plateaus, you have allocation churn, which is a GC-pressure problem rather than a leak.

## GC pressure, not leaks

Churn shows up as high allocation rate with a stable live set. Symptoms:

- GC time a visible share of CPU profile width.
- `p99` latency far above `p50`, with the tail coinciding with collections.
- High `heapTotal` with small `heapUsed`.

Reducers, in rough order of leverage:

1. Do not allocate what you can reuse: buffers, arrays, object pools for known-size items.
2. Remove intermediate representations: parsing then re-serializing, `map` chains that copy.
3. Keep allocation out of loops and out of per-request closures that escape.
4. Stream large payloads instead of materializing them.
5. Move computation off the request path (batch, background worker) rather than trying to
   make the collector faster.

Do not "tune" the collector as the first move. Changing heap limits trades latency for
larger RSS and can push an OOM kill forward. Only raise limits when you have measured that
live heap legitimately exceeds the current cap.

## Container memory limits

A process in a container sees the cgroup limit, not the host's memory. Under a limit, RSS
counts against the cgroup and exceeding it triggers the OOM killer, which by default kills
the largest process rather than the one that misbehaved. Practical rules:

- Leave headroom for native allocations, the allocator's unreturned pages, and sidecars.
- Make the V8 heap cap a fraction of the limit, set explicitly:
  `node --max-old-space-size=1536 server.js`.
- The exact relationship between RSS and the heap cap depends on the allocator. Measure
  your real RSS under a representative load rather than deriving it.

## Python memory

```bash
python -m tracemalloc                       # programmatic allocation tracking
python -X tracemalloc -m mymodule           # start it from the CLI
```

```python
import tracemalloc

tracemalloc.start(10)
do_work()
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:10]:
    print(stat)
```

`tracemalloc` counts Python allocations, not native ones. For native growth use a native
tooling approach. RSS alone is misleading because Python returns memory to the OS lazily;
`gc.collect()` plus repeated passes is the honest comparison.

## Verifying before relying on this

V8 heap limits, GC defaults, and the meaning of `external`/`arrayBuffers` have shifted
across Node major versions. Confirm against
<https://nodejs.org/api/process.html#processmemoryusage> and
<https://nodejs.org/api/cli.html> for your version.
