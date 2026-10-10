# Node.js and V8 Profiling

## Inspector flags worth knowing

| Flag | Effect |
| --- | --- |
| `--inspect[=host:port]` | Open the inspector on the given address |
| `--inspect-brk[=host:port]` | Same, but break before user code runs |
| `--cpu-prof` | Write a V8 CPU sampling profile on exit |
| `--cpu-prof-dir=DIR` | Directory for `--cpu-prof` output |
| `--cpu-prof-name=NAME` | File name for `--cpu-prof` output |
| `--cpu-prof-interval=N` | Sampling interval in microseconds |
| `--heap-prof` | Write a sampling heap (allocation) profile on exit |
| `--heap-prof-dir=DIR`, `--heap-prof-name=NAME` | Output location for `--heap-prof` |
| `--heap-prof-interval=N` | Average bytes between allocation samples |
| `--trace-gc` | Log GC events to stderr |
| `--trace-gc-verbose` | More detail on GC decisions |
| `--prof` | Legacy V8 tick profiler; writes `isolate-*-v8.log` |
| `--prof-process` | Post-process a `--prof` log into a readable summary |
| `--expose-gc` | Install `global.gc()` |
| `--max-old-space-size=MB` | Cap the old generation |

Confirm the full list and defaults for your build with `node --help` and
`https://nodejs.org/api/cli.html`.

## Profile formats

| Extension | Producer | Opens in |
| --- | --- | --- |
| `.cpuprofile` | `--cpu-prof` | Chrome DevTools Performance, Speedscope, Firefox Profiler |
| `.heapprofile` | `--heap-prof` | Chrome DevTools Memory panel |
| `isolate-*-v8.log` | `--prof` | `--prof-process`, or the V8 logging tools |
| `.heapsnapshot` | `v8.writeHeapSnapshot()`, inspector | Chrome DevTools Memory panel, `memlab` |

`.cpuprofile` is JSON. A quick check without any tooling:

```bash
node -e '
const fs = require("node:fs");
const p = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const byId = new Map(p.nodes.map(n => [n.id, n]));
const self = new Map();
for (const id of p.samples) self.set(id, (self.get(id) || 0) + 1);
const rows = [...self.entries()]
  .map(([id, count]) => ({ fn: byId.get(id).callFrame.functionName || "(anonymous)",
                           url: byId.get(id).callFrame.url, count }))
  .sort((a, b) => b.count - a.count)
  .slice(0, 15);
const total = p.samples.length;
for (const r of rows) {
  console.log((100 * r.count / total).toFixed(1).padStart(5) + "%  " + r.fn + "  " + r.url);
}
' profiles/isolate.cpuprofile
```

This gives you the self-time ranking in seconds of work, which is often enough to
confirm or kill a hypothesis without opening a GUI.

## Loading a saved profile in Chrome DevTools

1. Open DevTools on any page.
2. Go to the **Performance** panel.
3. Click *Load profile* (the folder icon) and choose the `.cpuprofile`.
4. Switch the flame chart between *Total Time* (inclusive) and *Bottom Up* (self time).
5. Use the *Speedscope*-style comparison: record a second profile, load it, and use the
   overlay to see which stacks grew.

Node heap snapshots: **Memory** panel -> *Load profile* -> choose the `.heapsnapshot`.
Take the *before* snapshot, run the suspect operation, take the *after* snapshot, then
select *Snapshot A* and *Snapshot B* in the top-left dropdown to get the delta view.

## Allocation profiling in Node

```bash
node --heap-prof --heap-prof-dir=./profiles --heap-prof-interval=8192 bench/workload.js
```

`--heap-prof-interval` is the average number of bytes allocated between samples. Smaller
values resolve small allocators at higher cost. Start at the default and only lower it if
the hot allocations are missed.

In the DevTools Memory panel, the allocation profile view shows a flame chart of live
allocation sites over time. Wide bands are code paths allocating continuously. The fix is
usually to hoist the allocation out of the loop, reuse a buffer, or avoid the intermediate
representation entirely.

## Event-loop and latency, not CPU

If the endpoint is slow but CPU is idle, the CPU profile is the wrong tool. Look at:

- **Event loop delay and utilization** — `perf_hooks.monitorEventLoopDelay()` gives a
  histogram of delay between timer ticks; it is the cleanest way to prove the loop is
  blocked.

```js
const { monitorEventLoopDelay } = require('node:perf_hooks');
const h = monitorEventLoopDelay({ resolution: 10 });
h.enable();
setInterval(() => {
  console.log({
    meanNs: h.mean,
    p99Ns: h.percentile(99),
    maxNs: h.max,
  });
  h.reset();
}, 1000).unref();
```

- **Async stacks** — enable `async-stack-traces` at startup in the inspector sessions
  view, or use `AsyncLocalStorage` to carry context. Node's `Error.prepareStackTrace`
  does not cover async boundaries by default.

```js
const { AsyncLocalStorage } = require('node:async_hooks');
const storage = new AsyncLocalStorage();
storage.run({ requestId: 'abc' }, async () => {
  await doWork();       // context survives the await
  console.log(storage.getStore());
});
```

- **Worker threads** — a `.cpuprofile` records all threads, but the flame chart is easier
  to read if you know which worker you are looking at. Name workers and filter by URL.

## V8-specific behavior that skews profiles

- **Inlining.** Small hot functions get inlined and disappear as separate frames. What you
  see may be attributed to the caller. This is normal; it is why self-time attribution can
  look surprising in interpreted or baseline-tier code.
- **Deoptimization.** If a function keeps leaving the optimizing tier, it can be much
  slower than its source suggests. Look for deopt events in V8 flags output rather than
  guessing.
- **Lazy compilation.** Functions not yet called have no code, so a cold profile shows
  compile-time work. Warm up first.
- **Boundaries.** Crossing the JS/C++ boundary (JSON, `Buffer`, `crypto`, native addons,
  DOM) may attribute cost to the boundary frame. `--cpu-prof` will show native frames;
  the inspector timeline view is better at separating them.
- **Multiple isolates.** Worker threads and cluster workers are separate isolates with
  separate heap snapshots. Profile the one that is actually hot.

## Measuring a latency budget from inside Node

```js
const { performance } = require('node:perf_hooks');

async function timed(name, fn) {
  const t0 = performance.now();
  try {
    return await fn();
  } finally {
    const dt = performance.now() - t0;
    console.log(JSON.stringify({ event: 'handler', name, ms: Number(dt.toFixed(3)) }));
  }
}
```

Per-call timers are fine for measurement instrumentation; never leave a synchronous
timer on a hot path without checking its cost, and prefer a histogram metric aggregated
by a metrics library rather than emitting a log line per call. See the
`metrics-monitoring` skill.

## Verifying before relying on this

The Node CLI surface changes between major releases. Confirm flags and defaults for
your version with `node --help` and <https://nodejs.org/api/cli.html>.
