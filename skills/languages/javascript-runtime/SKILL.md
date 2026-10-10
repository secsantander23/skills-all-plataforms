---
name: JavaScript Runtime Semantics
description: Covers the event loop and microtask ordering, closures and scope, prototypes versus classes, this binding, promises and async/await, iterators and generators, destructuring, immutability patterns, and ESM versus CommonJS module systems including top-level await. Use when the user asks about async ordering, why a value is stale or undefined, for-loop bugs, event loop stalls, module import errors, or when writing generators, custom iterables, or closures.
metadata:
  opencode/autoinvoke: true
---

# JavaScript Runtime Semantics

JavaScript is single-threaded with a task queue and a microtask queue. Nearly every
"impossible" bug you will debug — a stale value in a callback, a loop variable that is
always the last item, a promise that never resolves, an import that is undefined at
startup — comes from one of four things: evaluation order, scope, `this` binding, or
timing. This skill is organised around those four.

## When to use this

- Output of `console.log` in async code is not in source order.
- The user asks "why is my value stale / undefined / the last one" in a callback, a loop,
  or a promise chain.
- The user is writing a `for` loop and hitting a closure-capture bug.
- The user needs a debounce, throttle, worker pool, batching, or a serial task queue.
- The user is choosing between `require` and `import`, or hitting `ERR_REQUIRE_ESM` /
  "does not provide an export named".
- The user is writing a generator, a custom iterable, or destructuring-heavy code.
- The user is deciding whether to `await` in a loop or to `Promise.all`.

## Quick start

The ordering model in one function:

```js
console.log("1 sync");

setTimeout(() => console.log("5 macrotask"), 0);

Promise.resolve().then(() => console.log("4 microtask"));

queueMicrotask(() => console.log("3 microtask (queued later, same drain)"));

console.log("2 sync");

// Output: 1 sync, 2 sync, 3 microtask, 4 microtask, 5 macrotask
```

Rule: after the current task and its synchronous code finish, the engine drains the
*entire* microtask queue before taking the next macrotask. A microtask that queues another
microtask runs in the same drain, which is how you can starve the event loop forever with a
self-rescheduling `queueMicrotask`.

## The event loop

**Macrotasks** (one per turn of the loop): `setTimeout`, `setInterval`, I/O callbacks,
`MessageChannel`/`postMessage` ports, UI events, and the initial script evaluation itself.

**Microtasks**: promise reaction jobs (`.then`, `.catch`, `.finally`, `await`
resumption), `queueMicrotask`, and `MutationObserver` callbacks.

Order inside one turn:

1. Run the current macrotask to completion.
2. Drain the microtask queue until empty (including microtasks enqueued by microtasks).
3. Render / idle callbacks if the host provides them.
4. Take the next macrotask.

Consequences you can rely on:

- `await` resumes in a microtask; `setTimeout(fn, 0)` runs in the next macrotask. Code
  after `await` always runs after all already-queued microtasks up to that point, and
  before any timer callback.
- A long synchronous loop blocks everything, including timers and rendering.
- An `await` inside a `for` loop yields between iterations, so a timer can interleave.

```js
async function demo() {
  console.log("a");
  await null;
  console.log("b");
}
demo();
console.log("c");
// a, c, b
```

## Closures and scope

A closure is a function plus the environment it captured. Block-scoped `let`/`const` are
captured per iteration, which is the entire reason `let` in a `for` loop is not a bug.

```js
// Broken: one shared binding, all three log 3.
for (var i = 0; i < 3; i++) {
  setTimeout(() => console.log(i), 0);
}

// Correct: per-iteration binding.
for (let i = 0; i < 3; i++) {
  setTimeout(() => console.log(i), 0); // 0, 1, 2
}

// Also correct: an extra function parameter.
for (var i = 0; i < 3; i++) {
  const capture = i;
  setTimeout(() => console.log(capture), 0);
}
```

The shared-binding trap applies to `var` in loops and to any single `let` reassigned inside
a loop:

```js
let total = 0;
for (const item of items) {
  setTimeout(() => { total += item.price; }, 0);
}
// Every callback sees the same `total` binding: results interleave unpredictably.
```

**Temporal dead zone**: accessing a `let`/`const` before its declaration in the same scope
throws `ReferenceError`, it is not `undefined`. Function declarations are hoisted and
initialised; `let`/`const`/`class` declarations are hoisted but uninitialised.

```js
console.log(typeof x); // "undefined" — x not in TDZ yet, typeof is safe
console.log(x);       // ReferenceError: Cannot access 'x' before initialization
let x = 1;
```

Only `typeof` on a TDZ binding is special-cased. `typeof` of a *declared but unassigned*
`let` in TDZ still throws, which surprises people migrating from `var`.

## Prototypes, classes, and `this`

Classes are syntax over prototypes. `class Foo` adds `Foo.prototype.method`; a
constructor's prototype chain determines `instanceof` and property lookup.

```js
class Shape {
  constructor(id) { this.id = id; }          // implicit? no — class bodies are strict mode
  describe() { return `shape ${this.id}`; }
}
class Circle extends Shape {
  constructor(id, r) { super(id); this.r = r; }
  describe() { return `circle ${super.describe()} r=${this.r}`; }
}

const c = new Circle("c1", 5);
c instanceof Circle;   // true
c instanceof Shape;    // true
Object.getPrototypeOf(Circle) === Shape; // true
```

`this` in a method is not lexical. It is determined by *how the function is called*:

| Call form | `this` |
| --- | --- |
| `obj.m()` | `obj` |
| `m()` (bare, sloppy mode) | global object; `undefined` in strict mode |
| `const f = obj.m; f()` | `undefined` (classes and modules are strict) |
| `new C()` | the new instance |
| `m.call(x)` / `m.apply(x)` / `m.bind(x)` | `x` |

This is the classic React-class failure: passing `obj.handler` as a callback loses `this`
because `callbacks.map(obj.handler)` calls it bare. Fix by binding, wrapping in an arrow,
or using a class field with an arrow function.

```js
class Counter {
  #n = 0;                      // private field, not an underscore convention
  increment = () => { this.#n += 1; };  // arrow class field captures instance `this`
}
```

`#private` is enforced by the engine, not by convention. `_name` is a convention and is
still publicly accessible. Use `#` for real encapsulation.

## Promises and async/await

A promise settles once, forever. Callbacks registered after settlement still run, on a
microtask.

```js
async function loadUser(id) {
  const res = await fetch(`/api/users/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();            // implicit await on the returned promise
}

loadUser("7").then(u => console.log(u.name));
```

Key rules:

- `async` functions always return a promise; a bare `return value` is resolved for you.
- `throw` inside `async` becomes a rejection.
- A rejected promise with no handler produces an "unhandled rejection" report; attach a
  handler at the point you create the promise or immediately after.
- Sequential awaits in a loop are a deliberate choice; `Promise.all` for concurrency:

```js
const ids = [1, 2, 3];
const users = await Promise.all(ids.map(id => loadUser(id)));   // parallel
const pages = [];
for (const id of ids) pages.push(await loadUser(id));            // serial, stop on error

// Parallel but tolerant of individual failures:
const settled = await Promise.allSettled(ids.map(id => loadUser(id)));
const ok = settled.filter(r => r.status === "fulfilled").map(r => r.value);
const failed = settled.filter(r => r.status === "rejected").map(r => r.reason);
```

`Promise.all` rejects on the *first* failure, but the other operations keep running. Use
`allSettled` when you need every result regardless of failures.

**Timeout and cancellation** are not built in. `AbortController` is the standard mechanism:

```js
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(new Error("timeout")), 5000);
try {
  const res = await fetch("/api/report", { signal: controller.signal });
  return await res.json();
} finally {
  clearTimeout(timer);   // always clear, or the timer keeps the process alive
}
```

## Iterators and generators

Iterables implement `Symbol.iterator` returning `{ value, done }`. Generators are the
concise way to write one, and `for...of`, spread, and destructuring all consume them.

```js
function* chunk(iterable, size) {
  let buf = [];
  for (const item of iterable) {
    buf.push(item);
    if (buf.length === size) {
      yield buf;
      buf = [];
    }
  }
  if (buf.length > 0) yield buf;
}

for (const batch of chunk(range(0, 7), 3)) {
  console.log(batch); // [0,1,2] [3,4,5] [6]
}

const gen = chunk([1, 2, 3], 2);
gen.next(); // { value: [1, 2], done: false }
gen.return?.(); // early exit; a generator's return() runs finally blocks
```

A generator that yields promises is a common source of confusion: `for (const x of gen)`
gives you the promises, not the values. Either `yield await promise` inside an async
generator, or collect the values yourself.

```js
async function* fetchAll(ids) {
  for (const id of ids) {
    yield await loadUser(id);   // async generator: yields resolved values
  }
}
for await (const user of fetchAll(ids)) console.log(user.name);
```

Lazy evaluation is the point. Build pipelines of generators; nothing runs until iterated.

## Destructuring, spread, and immutability patterns

```js
const { a, b: renamed, ...rest } = config;         // rest excludes a and b
const [first, , third = "default", ...tail] = list; // holes skip; tail collects the rest
const { x: { y: { z } } } = deep;                   // nested, no intermediate vars

function draw({ ctx, shape = "circle" } = {}) {}    // parameter destructuring + defaults
draw();                                             // {} -> no throw, uses defaults
draw({ shape: "square" });                          // ctx is undefined here; validate it

const copy = { ...original, a: 99 };                // shallow: nested objects are shared
const deep = structuredClone(original);             // structured deep copy where available
```

Rules that matter:

- Rest/spread is a **shallow** copy. Nested objects and arrays are shared references, so a
  "copy" can be mutated through the original.
- Default values apply only to `undefined`, not to `null` or other falsy values.
- Object spread drops the prototype; spreading a class instance yields a plain object.
- `structuredClone` does not clone functions, DOM nodes, or symbols. For class instances,
  use a proper `clone()` method.

Practical immutability rules:

- Never mutate a parameter; copy first (`const copy = [...arr]`).
- Freeze at module boundaries (`Object.freeze(config)`) so accidental writes throw in
  strict mode instead of silently corrupting shared state.
- Return new objects from reducers; do not rely on frameworks to copy for you.
- Treat external JSON as untrusted and clone before handing it to code that may mutate it.

## Modules: ESM vs CommonJS

| | ESM (`import`/`export`) | CommonJS (`require`/`module.exports`) |
| --- | --- | --- |
| Loading | static, hoisted, resolved before evaluation | dynamic, evaluated on first `require` |
| Live bindings | yes, imports reflect later reassignment | snapshot at `require` time |
| `__dirname` / `__filename` | not defined | defined |
| Top-level `this` | `undefined` | `module.exports` |
| `require` inside ESM | not available | yes |
| Async | top-level `await` supported | no top-level `await` |

```js
// ESM: named exports must exist at link time or the import fails immediately.
import { readFile } from "node:fs/promises";
import defaultExport, { named as alias } from "./mod.js";
import * as ns from "./mod.js";          // namespace object

export { readFile };
export default handler;
export * from "./re-export.js";         // re-export; use `export type` for types
```

Live bindings are the difference people trip on:

```js
// counter.mjs
export let count = 0;
export function inc() { count++; }

// main.mjs
import { count, inc } from "./counter.mjs";
console.log(count); // 0
inc();
console.log(count); // 1 — the binding is live, not a copied value
```

Node specifics:

- In a package with `"type": "module"` or a `.mjs` file, ESM rules apply. `.cjs` is always
  CommonJS.
- Mixing them throws `ERR_REQUIRE_ESM` in older setups; current Node supports
  `require()` of ESM only under conditions you should verify for your version. Use dynamic
  `import()` from CommonJS as the portable bridge.
- Node resolves ESM specifiers strictly: relative imports need the file extension
  (`./mod.js`). `node:` prefixes for built-ins are unambiguous and preferred.
- Top-level `await` blocks the module's evaluation, which blocks everything that imports
  it. Keep it short and never put a top-level `await` on a network call in a module that
  a server loads at startup.

```js
// CommonJS side: async bridge
async function main() {
  const mod = await import("./esm-only.js");
  mod.run();
}
```

Circular imports work in ESM because of hoisting and live bindings, but the values may be
in the temporal dead zone at evaluation time (`Cannot access 'X' before initialization`).
Treat a circular dependency as a design problem and extract the shared piece.

## Checklist

- [ ] No `var` in new code; `const` by default, `let` only on reassignment.
- [ ] Closures in loops capture a per-iteration value.
- [ ] Callbacks that need `this` are bound, arrow class fields, or arrow wrappers.
- [ ] Every promise has a rejection path handled somewhere.
- [ ] Timers and intervals are cleared; long operations accept an `AbortSignal`.
- [ ] Independent awaits are concurrent (`Promise.all` / `allSettled`), not sequential.
- [ ] Heavy synchronous work is chunked or moved to a worker so the loop stays responsive.
- [ ] Module system is consistent within a package; ESM imports carry extensions.
- [ ] No microtask recursion that starves the loop.
- [ ] Inputs from outside are copied or frozen before being shared.

## Common mistakes

**Expected serial order, got interleaved.** A `setTimeout` you scheduled "first" can run
after an `await` that happened later in source order. Compare the *queues*, not the lines.

**`for (var i...)` with a closure: every callback sees the final value.** Use `let`, or
capture into a `const` inside the body.

**`obj.method` passed as a callback loses `this`.** `map(obj.method)` calls it bare. Use
`x => obj.method(x)`, `obj.method.bind(obj)`, or an arrow class field.

**"Cannot access 'X' before initialization" in a circular import.** One module reads the
binding before the other has evaluated. Break the cycle.

**A promise rejection vanishes.** No `.catch` anywhere. In Node, unhandled rejections are
reported and can terminate the process; attach handlers at creation.

**Timer keeps the process alive.** An uncleared `setInterval` or a `setTimeout` never
cleared. Use `timer.unref?.()` where a long-lived timer must not block exit, and clear in
`finally`.

**Mutating an object you did not own.** Spreading is shallow, so `const copy = {...orig}`
shares nested state. Deep-copy or use an immutable update path.

**`for...in` used to iterate an array.** It walks inherited enumerable string keys, so the
order and the key types are both surprising. Use `for...of` for values,
`Object.keys`/`Object.entries` for own keys.

**A heavy loop freezes the UI.** It is one macrotask. Yield periodically or move to a
Worker.

**`await` inside `forEach`.** `forEach` ignores the returned promise, so the awaits are
unsequenced relative to the caller. Use `for...of` for sequential, `Promise.all` over
`map` for parallel.

## Anti-patterns

- **Faking async with `setTimeout(fn, 0)` instead of queueing a microtask.** Use
  `queueMicrotask` or a promise continuation.
- **Mixing `import` and `require` inside one file.** Pick the module system the package
  declares.
- **A module-level cache object mutated from many call sites.** Make ownership explicit;
  pass state or return new values.
- **Chaining `.then` five deep to do what one `async`/`await` function reads clearly as.**
- **Swallowing errors with `.catch(() => {})`.** At minimum log; usually fix the cause.
- **`setInterval` for polling when you can push or await a completion signal.**
- **Deeply nested callbacks** where `async`/`await` or a generator would flatten control
  flow.
- **`arguments.callee`, `with`, or implicit globals** — all illegal in strict mode, which
  every module and class body runs in.
- **Treating `== null` as a style preference when you only mean one of them.** `x == null`
  is deliberately both; use `x === null` when only null should match.

## References

- MDN, the canonical language reference
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Event_loop
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Execution_model
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Closures
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Function/bind
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/this
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Iterators_and_generators
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Destructuring_assignment
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/structuredClone
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/async_function
  - https://developer.mozilla.org/en-US/docs/Web/API/AbortController
  - https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Using_promises
- Node.js modules
  - https://nodejs.org/api/esm.html
  - https://nodejs.org/api/modules.html
- HTML spec on the microtask checkpoint
  - https://html.spec.whatwg.org/multipage/webappapis.html#event-loops

For deeper reading:

- `references/event-loop-and-scheduling.md` — drain-the-queue semantics, starvation,
  chunking, timers vs immediates, worker offload.
- `references/promises-and-concurrency.md` — task queues, cancellation, retry with
  backoff, bounded concurrency, batching.
- `references/this-and-prototypes.md` — the full `this` table, prototype chain mechanics,
  private fields, class vs function tradeoffs.