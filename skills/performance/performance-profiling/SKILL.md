---
name: performance-profiling
description: Systematic CPU and memory profiling for Node.js, Python, and browsers, covering samplers versus instrumentation, flame graph reading, heap snapshots, allocation hotspots, GC pressure, and wall-clock versus CPU time. Use when the user says something is slow or hangs, asks how to profile, mentions a flame graph, heap snapshot, --cpu-prof, py-spy, or the Chrome DevTools performance panel, or when chasing a performance regression.
metadata:
  opencode/autoinvoke: true
---

# Performance Profiling

Profiling is measurement, not guesswork. A profile answers exactly one question — where
did the time or the memory go — and it answers it for one specific workload. Every
productive profiling session is the same loop: write down a hypothesis, choose the metric
that would prove or disprove it, capture a profile under a controlled load, read it, change
one thing, and measure again.

## When to use this

- A service, script, or page is slower than its latency budget and nobody knows why.
- A regression appeared after a deploy and you need the specific commit or code path.
- Memory grows until the process is OOM-killed, or latency degrades as the heap grows.
- You are about to optimize based on intuition, or on "this function looks slow".
- You need evidence for a pull request that claims a speedup.
- A user reports "it freezes" and you need to distinguish CPU-bound from I/O-bound.

## Quick start

Build a reproducible, self-contained workload first. Profiling a live service is the last
step, not the first.

```js
// bench/workload.js - a workload you can run on demand, with no network or clock skew.
const { N } = require('./config');
const rows = Array.from({ length: N }, (_, i) => ({ id: i, name: `user-${i}`, tags: ['a', 'b'] }));

function normalize(record) {
  return { ...record, name: record.name.trim().toLowerCase(), tags: [...record.tags].sort() };
}

const started = process.hrtime.bigint();
const out = rows.map(normalize);
const elapsedNs = Number(process.hrtime.bigint() - started);
console.log(JSON.stringify({ n: out.length, ms: elapsedNs / 1e6 }));
```

```bash
# Baseline metric: run it repeatedly, record the distribution, not one sample.
node bench/workload.js

# CPU profile (Node.js, V8 sampling profiler). Writes a .cpuprofile on exit.
node --cpu-prof --cpu-prof-dir=./profiles bench/workload.js

# Coarser or finer sampling interval, in microseconds. Verify with `node --help`.
node --cpu-prof --cpu-prof-interval=200 --cpu-prof-dir=./profiles bench/workload.js

# Allocation profile -> .heapprofile, loadable in the Chrome DevTools Memory panel.
node --heap-prof --heap-prof-dir=./profiles bench/workload.js

# GC activity as a diagnostic signal.
node --trace-gc bench/workload.js

# Interactive: attach the inspector and record with DevTools or chrome://inspect.
node --inspect-brk=9229 bench/workload.js
```

Open `chrome://inspect/#devices` in Chrome, find the Node target, click *inspect*, then
use the **Performance** panel (CPU) or **Memory** panel (heap snapshot, allocation
sampling). To load a saved profile instead, use the *Load profile* button in the
Performance panel and pick the `.cpuprofile` file.

## The profiling loop

1. **State the symptom as a metric.** "Slow" is not a metric. `p99 = 1.9s, budget = 300ms`
   is. Prefer a percentile over a mean; means hide the tail that users actually feel.
2. **State one hypothesis.** "Time is spent in JSON serialization of the response body."
   One sentence, falsifiable.
3. **Choose the measurement that could refute it.** If the hypothesis is about CPU, take
   a CPU profile. If it is about waiting, take a trace or a wall-clock timeline, not a CPU
   profile — a CPU profile of an I/O-bound program is mostly empty and tells you nothing.
4. **Fix the load.** Same input data, same machine, same warmup, no other load on the box.
   A profile of a cold run measures the filesystem and the JIT, not your code.
5. **Capture.** Save the raw artifact to disk. You will compare against it later.
6. **Read top-down.** Find the widest bars first, drill into the one that matters.
7. **Change one thing.** Two changes at once means you cannot attribute the result.
8. **Re-measure the same metric.** Report the delta. If it is inside the noise, revert.

## CPU time versus wall-clock time

This distinction explains most confusing profiles.

- **CPU time** is time the process spends executing on a CPU.
- **Wall-clock time** is elapsed real time, including waiting on sockets, disks, locks,
  garbage collection pauses, and scheduler descheduling.
- **CPU utilization** is CPU time divided by wall-clock time times core count.

If wall-clock is high and CPU is low, profiling the CPU is the wrong move. Look at
waiting instead: use `async_hooks`/async local storage, a tracing SDK, or strace. A
classic symptom is a request handler showing almost no self time in a CPU profile while
the endpoint is slow — the handler is blocked on a socket, and the cost is in someone
else's service.

Confirm the split before you profile:

```bash
# POSIX: user + system CPU seconds for the process
ps -o pid,etime,time,pcpu,rss,cmd -p <PID>

# POSIX: load average and runnable/uninterruptible task counts
uptime
cat /proc/loadavg

# Windows PowerShell
Get-Process -Id <PID> | Select-Object Id,CPU,WorkingSet64,Threads.Count
```

## Samplers versus instrumentation

| Approach | How it works | Strengths | Weaknesses |
| --- | --- | --- | --- |
| Sampling profiler | Periodically interrupts and records the stack | Low, roughly constant overhead; no code changes; works on unmodified or third-party code | Statistical; misses short-lived functions; does not count allocations or exact call counts |
| Instrumented / deterministic profiler | Hooks every function entry and exit | Exact call counts and inclusive times; deterministic | Large overhead, often 10x or more; perturbs the workload; usually needs a supported language runtime |
| Continuous / statistical profiler | Always-on sampling into a ring buffer, aggregated over time | Finds intermittent regressions and rare stacks you would never catch in a timed run | Needs storage and aggregation infrastructure |
| Trace / timeline | Records ordered events across threads and I/O | Best tool for latency, concurrency, and cross-service problems | High volume; needs a viewer; more like observability than profiling |

Pick a sampler first. Reach for instrumentation only when you need exact call counts and
can afford the distortion.

## Reading a flame graph

A flame graph stacks samples by call stack. Width is time or samples; vertical position is
depth. Read it as a set of inverted call trees.

- **Wide plateaus** are where the time is. A single function occupying a large fraction of
  the graph width is your candidate.
- **Self time** is a frame's own width excluding children. Sorting by self time is the
  fastest way to find leaf work: sorting by inclusive/cumulative time surfaces callers.
- **Tall and thin** stacks usually mean recursion or deep abstraction, not necessarily cost.
- **Flat wide bottom** means the cost is spread across many different callers; look for a
  shared helper.
- **White gaps** in Firefox Profiler or Speedscope are idle or unsampled time. A graph that
  is mostly gap is an I/O-bound or blocked program.
- **Compare two profiles** with Speedscope's *Compare* mode. Overlapping frames are the
  ones that changed. Reading the absolute graph when what you want is the delta is a
  common mistake.

Generate one on Linux with `perf` and Brendan Gregg's FlameGraph tools:

```bash
# Record 30 seconds of samples for a running process, with frame pointers.
sudo perf record -F 99 -g -p "$(pgrep -f 'node bench/workload.js')" -- sleep 30

# Text form first: cheaper to read, and it confirms the stack depth is sane.
perf script > perf.script
less perf.script

# SVG flame graph (requires the FlameGraph repo on PATH or cloned).
git clone https://github.com/brendangregg/FlameGraph.git
perl FlameGraph/stackcollapse-perf.pl perf.script > perf.folded
perl FlameGraph/flamegraph.pl perf.folded > flame.svg
```

Aggregate a profile so you see totals rather than one instance:

```bash
perf script | perl FlameGraph/stackcollapse-perf.pl | perl FlameGraph/flamegraph.pl > flame.svg
```

Set `perf_event_paranoid` appropriately for sampling if the kernel refuses; on many
distributions it is a sysctl under `/proc/sys/kernel/perf_event_paranoid`.

In the browser, use the **Performance** panel: record, interact with the slow path, stop,
then work top-down through the flame chart and the bottom-up **Summary** view. Enable the
**Memory** instrument only when you are investigating memory, since it adds overhead.

## Memory profiling

### Heap snapshots

A heap snapshot is a full object graph at one instant. It answers "what is retaining
memory", not "who allocated fastest".

```bash
# Node.js: attach the inspector, then Memory panel -> Heap snapshot.
node --inspect bench/workload.js
```

```js
// Node.js: take a snapshot from inside the process.
const v8 = require('node:v8');
const file = v8.writeHeapSnapshot('before.heapsnapshot');
console.log(`wrote ${file}`);
```

Load snapshots into Chrome DevTools **Memory** panel and compare two snapshots with the
*Compare* view. The delta view shows *constructors* that were added between snapshots, and
the retainer path for any object shows exactly what is holding it alive. Read the retainer
path from the object outward — that is the only part that matters for a leak.

### Allocation hotspots

Allocation rate drives GC pressure and is a common cause of latency tails that look
unrelated to any hot function. Take an **allocation profile** (`.heapprofile`) in Node or
use the **Allocation instrumentation on timeline** option in the DevTools Memory panel.
Look for:

- Large short-lived objects. Allocating a byte array per row to build a string is worse
  than reusing a buffer.
- Allocation inside a hot loop.
- Closures capturing large environments, which retain everything in scope.
- Repeated string concatenation building one large string.

### GC pressure

Garbage collection time is collected time: work your program did not ask for. Symptoms are
a latency p99 that jumps while throughput looks fine, and CPU profiles whose GC frames
occupy a surprising share of the width.

```bash
node --trace-gc bench/workload.js          # one line per GC event
node --expose-gc bench/workload.js         # then call global.gc() to force collections
```

```js
// Measure allocation pressure from inside a process.
const before = process.memoryUsage().heapUsed;
doWork();
const after = process.memoryUsage().heapUsed;
console.log({ heapDeltaBytes: after - before });
```

Use `--expose-gc` and explicit `global.gc()` calls around phases of a benchmark to make
allocation comparisons fair. Reduce pressure by reusing buffers and objects, avoiding
closures in hot loops, and moving work off the request path, not by calling the collector
more often.

### Browser memory

In DevTools, **Memory** -> *Take heap snapshot* for leaks and *Record allocation
sampling* for churn. Compare snapshots taken after a forced GC with and without
interacting with the suspect feature. The *Detached DOM trees* and *Collection* nodes in
the summary are the usual offenders in a single-page application.

## Other runtimes

```bash
# Python: deterministic, high overhead. -s cumt sorts by cumulative time.
python -m cProfile -o profile.out -s cumt bench_workload.py

# Python: sampling profiler, no code changes, works on a running process.
py-spy top --pid 12345
py-spy record --pid 12345 --output flame.svg --duration 30

# Go: CPU profile via net/http/pprof, then go tool pprof.
go test -cpuprofile cpu.out ./...
go tool pprof -http=:8080 cpu.out

# Go: block and mutex profiles are enabled explicitly when the program is built.
go build -blockprofile block.out -mutexprofile mutex.out ./cmd/server
```

For Python and Go, sample during a realistic workload. `cProfile` output is dominated by
profiling overhead on short functions; `py-spy` gives you a picture closer to production.

## Making the profile honest

- **Warm up.** Discard JIT and first-call cost unless you specifically care about cold start.
- **Pin the machine.** Close the IDE. Fix the CPU frequency if you can. Never compare
  profiles taken on a laptop on battery against a server.
- **Profile the thing users run.** A microbenchmark of a leaf function tells you about the
  leaf, not the endpoint.
- **Keep the artifact.** Commit profiles only if the repo accepts them; otherwise store
  them somewhere durable with the commit SHA, input size, and machine spec in the filename.
- **Sample size.** Short runs produce noisy graphs. Longer is better up to the point where
  the workload diverges from what you care about.

## Checklist

- [ ] Symptom written down as a numeric metric with a budget.
- [ ] One falsifiable hypothesis recorded before profiling.
- [ ] CPU versus wall-clock split checked so you picked the right tool.
- [ ] Workload is reproducible and pinned, with warmup discarded.
- [ ] Raw profile artifacts saved for later comparison.
- [ ] Graph read top-down for wide plateaus, then bottom-up for self time.
- [ ] Allocation profile and GC trace taken if the graph looks allocation-heavy.
- [ ] Heap snapshots compared, and leak candidates confirmed via retainer paths.
- [ ] Exactly one change made, and the same metric re-measured.
- [ ] Result reported as a delta against a recorded baseline.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| "The profile shows nothing hot" | Program is I/O bound; a CPU profile of idle time is empty | Confirm with CPU utilization, then use a trace or tracing SDK instead |
| "It got slower after the change" | Profiling overhead changed, or the machine was busier | Re-profile both sides under identical conditions before believing any delta |
| Graph is mostly idle gaps | Sampler cannot see native/IO frames, or the work happens off the main thread | Profile worker threads too, or use a timeline profiler |
| "Memory grows forever" | Retained, not garbage. Inspecting the wrong thing | Compare heap snapshots and read retainer paths, do not watch RSS alone |
| Optimize produced no change | Already off the critical path, or dominated by a downstream call | Confirm the frame is actually wide in the profile before spending effort |
| Profile looks great in staging | Different data volume, different hardware, cold caches | Reproduce production-like input size and check the box's core count and disk |
| Flame graph differs run to run | Sampling noise on a short workload | Lengthen the run; keep the workload constant |

## Anti-patterns

- **Guessing first.** Rewriting code because a function "looks expensive" is unfalsifiable
  work. Measure or explicitly label the change as a hypothesis to test.
- **Profiling production under incident load without care.** Sampling profiles are cheap
  but not free, and a debugger attached to a struggling process can tip it over. Prefer
  a rolling continuous profiler you already have, or reproduce the load elsewhere.
- **Profiling the build, not the artifact.** Measuring your test suite's own overhead tells
  you about the runner.
- **Breaking the workload to make a number look better.** Special-case fast paths for the
  benchmark input are regressions, not optimizations.
- **Treating `--cpu-prof` output as exact call counts.** It is a sampler. Do not derive
  algorithmic complexity claims from one run of it.
- **Optimizing allocation before finding the real cost.** Allocation matters when GC time
  or heap growth is actually visible in the profile. Otherwise it is churn.
- **Fixing the symptom without a baseline.** "It's fast now" is unfalsifiable. "p99 went
  from 1.9s to 0.4s under the same workload on the same machine" is a result.

## References

- Node.js documentation, Diagnostics, CPU profiling and heap profiling
  - https://nodejs.org/en/learn/diagnostics/measuring-performance
  - https://nodejs.org/en/learn/diagnostics/memory
- V8 documentation, CPU profiler
  - https://v8.dev/docs/cpu-profiler
- Chrome DevTools Performance panel
  - https://developer.chrome.com/docs/devtools/performance
- Chrome DevTools Memory panel
  - https://developer.chrome.com/docs/devtools/memory-problems
- Brendan Gregg, FlameGraph
  - https://github.com/brendangregg/FlameGraph
- Brendan Gregg, The Flame Graph article
  - https://www.brendangregg.com/flamegraphs.html
- Linux `perf` user guide
  - https://perfwiki.github.io/main/
- Firefox Profiler
  - https://profiler.firefox.com/
- Speedscope, a profile comparison viewer
  - https://github.com/jlfwong/speedscope
- py-spy, a sampling profiler for Python
  - https://github.com/benfred/py-spy
- Go pprof documentation
  - https://pkg.go.dev/net/http/pprof
- Google SRE book, "Monitoring Distributed Systems"
  - https://sre.google/sre-book/monitoring-distributed-systems/

## Deeper detail

- `references/node-and-v8.md` — inspector flags, profile formats, DevTools workflow,
  allocation profiling, and V8-specific pitfalls.
- `references/flame-graphs-and-perf.md` — `perf` collection, flame graph generation,
  folding, differential profiling, and reading patterns.
- `references/memory-and-gc.md` — heap snapshot anatomy, retainer analysis, leak
  taxonomies, GC tuning tradeoffs, and container memory limits.
