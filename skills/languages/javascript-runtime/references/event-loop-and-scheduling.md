# Event Loop and Scheduling

## One turn of the loop

```text
take one task from the macrotask queue
  -> run it to completion (all synchronous code, including nested awaits' setup)
drain the microtask queue completely
  (repeat until empty; microtasks can enqueue microtasks)
host rendering / idle callbacks
take the next task
```

`setTimeout(fn, 0)` and `setTimeout(fn, 1)` are not the same guarantee. Clamping and
coarse timer resolution mean the host may round both to the same delay; a 0 ms timer is
"as soon as possible after the current task and microtask drain", never "immediately".

## Starvation

```js
// Never yields. The page freezes forever.
function spin() {
  queueMicrotask(spin);
}
spin();
```

```js
// Same hazard with promises.
async function spin() {
  await Promise.resolve();
  return spin();
}
spin();
```

Fixes: yield to the task queue (`setTimeout`) periodically, yield to the host with
`requestAnimationFrame` in a browser, or move the work to a Worker.

## Chunking large synchronous work

```js
function processInChunks(items, handler) {
  const CHUNK = 1000;
  let index = 0;

  function step() {
    const end = Math.min(index + CHUNK, items.length);
    for (; index < end; index++) handler(items[index]);
    if (index < items.length) queueMicrotask(step);
  }

  step();
}
```

Yielding with `queueMicrotask` keeps everything in one macrotask, so timers still wait.
Yielding with `setTimeout` lets timers and rendering interleave. Choose deliberately:

| Yield to | Use when |
| --- | --- |
| `queueMicrotask` | you want fairness against other microtasks only |
| `setTimeout(fn, 0)` | you want timers and input to run between chunks |
| `requestAnimationFrame` | you are updating what the user sees |
| `scheduler.postTask` (host-provided) | you want a priority hint, where available |

Check availability of `scheduler.postTask` and `requestIdleCallback` in your target
environments before relying on them; both are host APIs, not core language features.

## Debounce and throttle, correctly

```js
function debounce(fn, delayMs) {
  let timer;
  function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delayMs);
  }
  debounced.cancel = () => clearTimeout(timer);
  return debounced;
}

function throttle(fn, intervalMs) {
  let last = 0;
  let pendingTimer = null;
  let pendingArgs = null;

  return function throttled(...args) {
    const now = Date.now();
    const remaining = intervalMs - (now - last);

    if (remaining <= 0) {
      if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
      last = now;
      fn.apply(this, args);
      return;
    }

    // Keep the most recent call for the trailing edge.
    pendingArgs = args;
    if (pendingTimer === null) {
      pendingTimer = setTimeout(() => {
        last = Date.now();
        pendingTimer = null;
        if (pendingArgs) fn.apply(this, pendingArgs);
      }, remaining);
    }
  };
}
```

Two real bugs to avoid: (1) clearing the timeout in a debounce but never exposing a way to
cancel it, so a component unmount still fires; (2) a throttle that drops the trailing
call entirely, which silently loses the user's last input.

In a browser, consider the native `AbortController` pattern for search inputs: abort the
previous fetch on the next keystroke. That avoids the "out-of-order response overwrites
newer data" race entirely.

## Bounded concurrency

```js
async function mapWithConcurrency(items, limit, worker) {
  if (limit < 1) throw new RangeError("limit must be >= 1");

  const results = new Array(items.length);
  let next = 0;

  async function run() {
    for (;;) {
      const index = next++;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
  return results;
}

const ids = Array.from({ length: 500 }, (_, i) => i);
const rows = await mapWithConcurrency(ids, 8, (id) => fetchRow(id));
```

`Promise.all` over 500 requests opens 500 sockets. Rate limits, memory, and server
politeness all argue for a bound. This pattern is the workhorse of batching utilities.

## Timers in Node vs the browser

- In Node, `setTimeout` returns a `Timeout` object with `unref()` and `ref()`. Calling
  `unref()` lets the process exit without waiting for the timer.
- In the browser, timer identifiers are numbers and there is no `unref`.
- In both, an active timer keeps the event loop (Node) or the page's task queue alive.
  Always clear intervals when their purpose ends.

```js
// Node only
const heartbeat = setInterval(pulse, 30_000);
heartbeat.unref?.();  // optional-call so the same code runs in a browser
```

## Measuring where time went

In a browser, the Performance API is the reliable tool for long-task attribution. In
Node, `process.hrtime.bigint()` for wall-clock deltas and
`performance.now()` (available in modern Node) for monotonic measurements. Never measure
with `Date.now()` for durations: it is wall-clock and can jump.

```js
const t0 = performance.now();
await heavyWork();
console.log(`${(performance.now() - t0).toFixed(1)}ms`);
```