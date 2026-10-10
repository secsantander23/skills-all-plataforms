# Promises and Concurrency

## Promises settle once

```js
let resolveFn;
const p = new Promise((resolve) => { resolveFn = resolve; });
resolveFn("first");
resolveFn("second");
p.then(console.log); // "first" only
```

Every `.then` registered after settlement still runs, on a microtask. This is why a
promise returned from an already-settled promise still takes a tick:

```js
console.log("1");
Promise.resolve().then(() => console.log("3"));
console.log("2");
// 1, 2, 3
```

## Thenable adoption

Returning a thenable from `resolve` (or from an `async` function) unwraps it. Two ways to
accidentally lose a rejection:

```js
// Wrong: returns a NEW promise that nobody handles.
function bad() {
  return new Promise((resolve) => resolve(fetchThing()));
}

// Right: `return fetchThing()` directly from an async function.
async function good() {
  return fetchThing();
}
```

## Error handling discipline

```js
// Fail fast, clean up, rethrow with context.
async function transfer(from, to, amount) {
  if (amount <= 0) throw new RangeError("amount must be positive");
  const debit = await debitAccount(from, amount);
  try {
    return await creditAccount(to, amount);
  } catch (err) {
    await creditAccount(from, amount); // compensate; if this throws, you have a real incident
    throw new Error(`credit failed for ${from}->${to}: ${err.message}`, { cause: err });
  }
}
```

`Error`'s `cause` option attaches the original error without stringifying it away. It is
supported by all current engines; check your minimum runtime if you support older ones.

- Handle rejection at the layer that can *decide* something: the caller, or a boundary that
  reports and continues.
- Never `.catch(() => {})` silently. Log, count, or rethrow.
- `process.on("unhandledRejection", ...)` in Node, and `window.addEventListener("unhandledrejection", ...)`
  in a browser, are the last-resort detectors. Add them in tests, not in production hot
  paths.

## Cancellation

Promises cannot be cancelled. Cancel the *operation* instead:

```js
async function search(query, { signal } = {}) {
  const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`, { signal });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}

const controller = new AbortController();
const pending = search("abc", { signal: controller.signal });
controller.abort();
await pending; // rejects with an AbortError
```

Handle aborts distinctly from real errors, or every cancellation becomes an alert:

```js
try {
  const data = await search(q, { signal });
  render(data);
} catch (err) {
  if (err?.name === "AbortError") return; // expected, not an incident
  report(err);
}
```

## Retry with backoff and jitter

```js
async function withRetry(fn, { attempts = 3, baseDelayMs = 200, shouldRetry } = {}) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastError = err;
      if (shouldRetry && !shouldRetry(err)) throw err;
      if (attempt === attempts - 1) break;
      // Exponential backoff with full jitter.
      const ceiling = baseDelayMs * 2 ** attempt;
      await new Promise((r) => setTimeout(r, Math.random() * ceiling));
    }
  }
  throw lastError;
}

await withRetry(() => fetchRow(id), {
  shouldRetry: (err) => err instanceof HttpError && err.status >= 500,
});
```

Retrying non-idempotent operations duplicates side effects. Only retry reads, or make the
server operation idempotent with a request key. Jitter matters: without it, every client
that failed together retries together and re-creates the overload.

## Serial task queue

Some workloads must not overlap, e.g. writing ordered records to a file.

```js
function createQueue() {
  let tail = Promise.resolve();
  return function enqueue(task) {
    const result = tail.then(task);
    // Keep the chain alive even when a task rejects.
    tail = result.catch(() => undefined);
    return result;
  };
}

const write = createQueue();
await Promise.all(rows.map((r) => write(() => append(r))));
```

## Pipeline with bounded concurrency

Compose the bounded-concurrency mapper with retry for a production fetch layer:

```js
async function loadAll(ids, { concurrency = 8, attempts = 3 } = {}) {
  const results = await mapWithConcurrency(ids, concurrency, (id) =>
    withRetry(() => search(String(id)), {
      attempts,
      shouldRetry: (err) => err instanceof HttpError && err.status >= 500,
    }),
  );
  return results;
}
```

Order is preserved because the mapper writes into a pre-sized array by index, which is
usually what callers want and is harder to guarantee with `Promise.all` plus `push`.