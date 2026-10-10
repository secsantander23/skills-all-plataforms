---
name: Browser Runtime
description: Covers how the browser actually executes an application including HTML and CSS parsing, DOM construction, the critical rendering path, layout paint and composite, script loading with defer, async, and modules, reflow and repaint, the event loop and rendering opportunities, garbage collection, storage APIs for cookies, localStorage, sessionStorage, and IndexedDB, and service workers. Use when the user asks why a page is slow to appear, about jank or layout thrashing, about script loading strategies, storage limits, or how service workers intercept requests.
metadata:
  opencode/autoinvoke: true
---

# Browser Runtime

A browser is not a virtual machine running your code; it is a pipeline that has to turn bytes
into pixels, and it interleaves that work with your JavaScript. Most "the site is slow"
problems come from a specific stage in that pipeline, and most performance advice is really a
statement about which stage you are blocking.

The pipeline: **bytes → DOM → CSSOM → render tree → layout → paint → composite**. JavaScript
interrupts it at several points, and the browser only paints between tasks.

## When to use this

- Diagnosing a blank or slow first paint, and choosing between `defer`, `async`, and modules.
- Fixing jank, layout thrashing, or long tasks.
- Choosing storage: cookies, `localStorage`, `sessionStorage`, or IndexedDB.
- Adding a service worker and understanding its lifecycle and interception scope.
- Understanding why a mutation causes a full relayout.
- Deciding what belongs on the main thread and what belongs in a worker.

## Quick start

A page skeleton that avoids the three classic stalls:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />

    <!-- Only the styles needed for the first screen -->
    <link rel="preload" href="/assets/critical.css" as="style" />
    <link rel="stylesheet" href="/assets/critical.css" />
    <link rel="stylesheet" href="/assets/rest.css" media="print" onload="this.media='all'" />

    <!-- Start the font before the CSS needs it -->
    <link rel="preload" href="/assets/inter.woff2" as="font" type="font/woff2" crossorigin />

    <script type="module" src="/assets/app.js"></script>
  </head>
  <body>
    <header>...</header>
    <main>...</main>
    <script type="module" src="/assets/lazy.js"></script>
  </body>
</html>
```

`type="module"` is deferred by default, so neither script blocks HTML parsing.

## Parsing and the DOM

### HTML

The HTML parser is a streaming tokenizer plus a tree builder. It builds the DOM incrementally,
which is why content injected by a script in the middle of the document is reparsed
differently than markup in the initial file — "the parser goes around your appended content"
is a real, observable behaviour.

Key consequences:

- A classic `<script src>` without `defer` or `async` **blocks the parser**. Nothing after it is
  built until it downloads and executes. This is the single largest first-paint cost on
  script-heavy pages.
- The document streams, so the browser can start layout before the response finishes. If your
  server buffers the whole response before sending, you lose that.
- `document.write()` is a performance footgun and is disallowed for cross-origin scripts in some
  contexts; never use it.
- `document.readyState` moves `loading` → `interactive` → `complete`. `DOMContentLoaded` fires
  at `interactive`; `load` waits for subresources including images and fonts.

### CSS

- A stylesheet in `<head>` is render-blocking: the browser cannot paint without the CSSOM.
- `media` attributes on `<link rel="stylesheet">` make the sheet non-render-blocking.
  `media="print"` plus a switch to `all` on load is a common non-blocking pattern, though it
  costs a flash of unstyled content and uses an inline handler that a strict CSP will block
  (use a nonce, or do the swap from an external script).
- `@import` chains requests and delays discovery of the imported sheet. Prefer `<link>`.
- Inline critical CSS in `<style>` with a nonce removes one round trip.

## Script loading

| Attribute | Parser blocking | Execution order | Notes |
| --- | --- | --- | --- |
| `<script src>` | Yes | Immediately, in document order | The default that hurts |
| `<script src defer>` | No | In document order, after parsing, before `DOMContentLoaded` | The default choice for app code |
| `<script src async>` | No | As soon as it arrives, out of order | Analytics, widgets, anything order-independent |
| `<script type="module">` | No | Deferred, in document order | Strict mode, own scope, `import`/`export` |
| `<script type="module" async>` | No | As available | Independent modules |

Rules of thumb:

- Application code: `type="module"` or `defer`. Both preserve order.
- Third-party analytics, tag managers, chat widgets: `async`.
- `async` on code that depends on another script is a race that will pass locally and fail in
  production when the cache is cold.

### Modules

`<script type="module">` scripts:

- Are deferred automatically; the `defer` attribute is redundant on them.
- Are fetched with CORS semantics, so cross-origin modules need correct CORS headers.
- Run in strict mode with their own scope; top-level variables are not global.
- Are evaluated once per URL. Two imports of the same URL share one instance.
- Have their static `import` graph fetched and evaluated before the importing module runs, in
  depth-first post-order. Circular imports between modules execute with bindings that may not
  be initialized yet.

`import()` returns a promise and is evaluated at call time, which is the basis of code
splitting:

```javascript
document.querySelector('#open-report').addEventListener('click', async () => {
  const { openReport } = await import('./report-dialog.js');
  openReport();
});
```

### Preloading

```html
<link rel="preload" as="script" href="/assets/app.js" fetchpriority="high" />
<link rel="prefetch" href="/assets/next-page.js" />
<link rel="modulepreload" href="/assets/report.js" />
```

- `preload` fetches now at high priority; use it for resources that will definitely be used in
  this page.
- `prefetch` fetches at low priority for a likely *next* navigation. Loading the same URL as
  both preload and prefetch wastes bandwidth.
- `modulepreload` fetches a module and its dependency graph.
- `as` must match how the resource is consumed (`script`, `style`, `font`, `image`), and fonts
  require `crossorigin` even for same-origin fonts, because they are fetched in CORS mode.

## Layout, paint, composite

After the DOM and CSSOM exist, the browser builds a render tree and runs three stages:

1. **Layout (reflow)** — compute geometry: position and size for every box. Triggered by changes
   to geometry-affecting properties: `width`, `height`, `top`, `margin`, `font-size`, `display`,
   content changes, window resize.
2. **Paint** — fill in the pixels for each box: text, colors, borders, shadows, images.
   Triggered by changes to visual properties that do not affect geometry: `color`,
   `background-color`, `box-shadow`.
3. **Composite** — combine painted layers into the frame. Triggered by `transform` and
   `opacity`, which can often be handled on the compositor thread without layout or paint.

Choosing the right property matters:

```javascript
/* Triggers layout, then paint, then composite. */
element.style.left = `${x}px`;

/* Usually composite only: no layout, no paint. */
element.style.transform = `translateX(${x}px)`;
```

Animating `left`/`top`/`width` on every frame is a reliable way to produce jank. Animating
`transform` and `opacity` is not, and is what animations are designed around.

### Layout thrashing

Reading a layout property forces the browser to flush pending layout before returning.

```javascript
/* Thrashing: write, read, write, read — forces a synchronous layout per iteration. */
for (const item of items) {
  item.style.width = measureWidth(item) + 'px';   // write ... then read = forced reflow
}

/* Correct: read everything first, then write everything. */
const widths = items.map(measureWidth);            // all reads
items.forEach((item, i) => {
  item.style.width = widths[i] + 'px';             // all writes
});
```

Symptoms of thrashing: high `Layout` time in a performance recording, long tasks with no obvious
cause, a typing lag in an input with a keypress handler that measures then writes.

### Layers

Promoting an element to its own compositor layer costs memory but makes transforms and opacity
cheap. Use it sparingly:

```javascript
element.style.willChange = 'transform';   // set before the animation
```

`will-change` on too many elements is worse than none: it exhausts memory and can make
everything slower. Set it immediately before an animation and remove it after. Never apply it to
hundreds of elements globally.

## Event loop and rendering

JavaScript runs on one main thread (per browsing context). It processes:

- **Tasks** — from event handlers, `setTimeout`, `setInterval`, network callbacks,
  `MessageChannel`, and so on.
- **Microtasks** — promise callbacks and `queueMicrotask`. They run after the current task
  completes and before the browser gets a chance to render.
- **Rendering** — between tasks, if the browser decides it is time to render.

The consequences are precise and frequently surprising:

- A microtask that schedules more microtasks starves rendering indefinitely.
- `setTimeout(fn, 0)` does not run "immediately"; it runs after the current task and after
  rendering has an opportunity.
- `requestAnimationFrame` callbacks run just before the next paint. Use it for visual updates;
  use it instead of `setInterval` for animations.
- Long tasks delay rendering and, in newer engines, delay input processing. The Long Tasks API
  reports tasks over roughly 50 ms.

```javascript
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    console.log('long task', entry.duration, entry.attribution);
  }
});
observer.observe({ entryTypes: ['longtask'] });
```

`PerformanceObserver` with `entryTypes: ['longtask']` is real and supported in current major
browsers. Its entries carry `attribution` identifying the script.

Event Timing, which is what INP is computed from:

```javascript
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    // PerformanceEventTiming: startTime, processingStart, duration, interactionId
    console.log(entry.name, entry.duration, entry.processingStart - entry.startTime);
  }
});
observer.observe({ type: 'event', durationThreshold: 16, buffered: true });
```

`durationThreshold` has a minimum of 16 ms; the browser clamps lower values. Older `type:
'first-input'` observers are deprecated in favor of `type: 'event'`.

Break up long tasks rather than just moving them:

```javascript
async function processAll(items) {
  for (const item of items) {
    await processOne(item);
    await new Promise((resolve) => setTimeout(resolve, 0)); // yield to the event loop
  }
}
```

Yielding this way is a real technique but it is not free; prefer moving genuinely independent work
into a Web Worker.

### Workers

A Web Worker has no DOM access and its own event loop, so long computation off the main thread
does not block interaction:

```javascript
// worker.js
self.onmessage = ({ data }) => {
  self.postMessage(compute(data));
};

// main thread
const worker = new Worker('/workers/compute.js', { type: 'module' });
worker.onmessage = ({ data }) => render(data);
worker.postMessage(payload);
```

Use workers for parsing large payloads, image processing, crypto, and heavy computation. Do not
use them for DOM work; they cannot touch the DOM.

## Memory and garbage collection

JavaScript values are garbage collected when unreachable. The practical consequences:

- Detached DOM nodes and forgotten event listeners retain memory until the whole object graph
  is unreachable. In single-page applications, route teardown is where leaks live.
- Large arrays that are grown and discarded in a loop create load on the collector, which shows
  up as periodic pauses.
- Observing real memory needs the browser's memory tooling or the `performance.memory`
  property, which is a non-standard Chromium feature and is not available elsewhere. Do not build
  a production feature on it; use the DevTools Memory panel or a browser-specific heap snapshot.
- Heap size alone is not a leak. Look for heap size that grows and never returns to baseline
  after a repeatable user action.

## Storage APIs

| API | Sync? | Capacity | Survives close | Scope |
| --- | --- | --- | --- | --- |
| Cookie | Yes | Small; roughly 4 KB per cookie including attributes | If `Max-Age`/`Expires` set | Sent to matching origins on every request |
| `localStorage` | Yes | Small, string-only; roughly 5 MB per origin in practice | Yes | All tabs on the origin |
| `sessionStorage` | Yes | Same small budget | No (survives reload) | One top-level browsing context (tab) |
| IndexedDB | No | Large; browser-managed quota | Yes | Origin, structured data |
| Cache API | No | Large | Yes | Origin, request/response pairs |
| Cookies | Yes | ~4 KB per cookie (RFC 6265) | With expiry | Per domain and path rules |

### Cookies

```javascript
document.cookie = 'session=abc; Path=/; Secure; HttpOnly; SameSite=Lax';
```

`document.cookie` cannot see or set `HttpOnly` cookies — that is the point of the attribute.
Cookies are read from and written to disk, so they are small, synchronous, and subject to
`SameSite`, `Secure`, and third-party cookie restrictions that differ between browsers. Do not
put application state in them; put an opaque session identifier there.

### localStorage and sessionStorage

```javascript
localStorage.setItem('theme', 'dark');
localStorage.getItem('theme');
sessionStorage.setItem('draft', JSON.stringify(payload));
```

- Both are **synchronous**. A large read blocks the main thread; store identifiers, not blobs.
- Both are **string-only**. `localStorage.setItem('a', {})` stores `"[object Object]"`. Use
  `JSON.stringify` / `JSON.parse` explicitly.
- Access can **throw**: in sandboxed frames without `allow-same-origin`, and in partitioned or
  blocked third-party contexts. Wrap access in `try/catch` and fall back.
- `localStorage` is shared by every tab on the origin, so a `storage` event fires in other tabs
  when a value changes.
- `sessionStorage` is per tab, so it is the right place for per-tab state like wizard drafts.

### IndexedDB

IndexedDB is asynchronous, transactional, and stores structured-cloneable values with indexes.

```javascript
const request = indexedDB.open('acme-cache', 1);

request.onupgradeneeded = (event) => {
  const db = request.result;
  if (!db.objectStoreNames.contains('orders')) {
    const store = db.createObjectStore('orders', { keyPath: 'id' });
    store.createIndex('byCustomer', 'customerId');
  }
};

request.onsuccess = () => {
  const db = request.result;
  const tx = db.transaction('orders', 'readonly');
  const store = tx.objectStore('orders');
  const all = store.getAll();
  all.onsuccess = () => console.log(all.result);
};

request.onerror = () => console.error(request.error);
```

Rules that matter:

- Every operation lives inside a transaction. Reads and writes in separate transactions can
  interleave.
- `onupgradeneeded` is the only place schema changes happen; version the database.
- Structured clone means you cannot store functions, DOM nodes, or class instances with custom
  prototypes without going through structured clone semantics.
- Use it for anything that outgrows `localStorage`: offline data, large caches, documents, drafts.

## Service workers

A service worker is a worker with a network position: it can intercept requests for a scope.

```javascript
// main thread
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then((registration) => console.log(registration.scope));
  });
}
```

```javascript
// sw.js
const CACHE = 'acme-v1';
const PRECACHE = ['/', '/offline.html', '/assets/app.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.match(event.request).then((cached) => {
      return cached || fetch(event.request).then((response) => {
        if (response.ok && new URL(event.request.url).origin === self.location.origin) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      });
    })
  );
});
```

Behaviour that surprises people:

- **Only on secure contexts.** HTTPS, or `localhost` during development. A service worker will
  not register on plain HTTP.
- **It controls navigations within its scope**, including ones you never intended. This is the
  main source of "it works after a hard refresh" bugs.
- **Updates are lazy.** The browser checks for a new worker on navigation and, for the byte
  cache, periodically. `registration.update()` forces a check. Until a new worker is installed
  and activated, the old one serves requests.
- `self.skipWaiting()` activates immediately, which can mix old and new assets. Call it only
  when you are sure there is no incompatible state.
- `self.clients.claim()` takes control of pages that are already open, again risking a
  half-updated client.
- `event.respondWith` must be called synchronously or during a microtask, or the request falls
  through to the network.
- A navigation preload hint lets the worker start fetching while it boots:
  `self.addEventListener('fetch', ...)`, plus `registration.navigationPreload.enable()` on the
  main thread and `event.preloadResponse` in the worker.
- Cache-first with no invalidation is the number one source of "users are stuck on an old
  version". Version the cache name and clean up on `activate`.

## Checklist

- [ ] No classic render-blocking `<script>` without `defer` in `<head>`.
- [ ] Third-party scripts use `async`; app code uses `type="module"` or `defer`.
- [ ] Critical CSS is inlined or preloaded; the rest is non-render-blocking.
- [ ] Fonts are preloaded with `crossorigin`, or `font-display: swap` is set.
- [ ] Animations use `transform` and `opacity`, not geometry properties.
- [ ] No read-after-write layout thrashing in loops or animation callbacks.
- [ ] Long tasks identified with PerformanceObserver and broken up or moved to a worker.
- [ ] Input handlers do not perform synchronous heavy work.
- [ ] Storage choice matches the data: string and small for localStorage/sessionStorage,
      structured and large for IndexedDB.
- [ ] `localStorage` access is wrapped in `try/catch` for blocked contexts.
- [ ] If a service worker exists: HTTPS only, explicit versioned cache names, old caches
      deleted on `activate`, and a plan for updates.

## Common mistakes

**Reading a layout property after writing a style.** Symptom: layout time spikes in loops.
Root cause: forced synchronous reflow. Fix: batch reads, then batch writes.

**Animating `width`, `top`, or `height`.** Symptom: dropped frames even on modest content.
Root cause: layout and paint per frame. Fix: animate `transform`.

**`setInterval` for animation.** Symptom: visible stutter and drift. Fix:
`requestAnimationFrame`, which syncs to the browser's paint cadence.

**`localStorage.setItem` on a 2 MB string.** Symptom: a long pause on the main thread. Fix:
IndexedDB, which is asynchronous.

**Putting JSON in `localStorage` without `JSON.stringify`.** Symptom: `"[object Object]"`
stored and never parsed. Fix: serialize explicitly, and handle `JSON.parse` failure on read.

**`localStorage` used as a session store.** Symptom: a user logs out on one tab and stays
logged in on the others. Fix: a cookie or `sessionStorage` depending on the lifetime you need.

**A service worker caching everything forever.** Symptom: a released bug that no user can get
past. Fix: versioned caches, cleanup on `activate`, and a network-first strategy for HTML.

**Registering the service worker inside `load` and wondering why it misses the first
navigation.** `load` may already have fired relative to the initial request. Register earlier, or
accept that the first visit is not controlled.

**Assuming `will-change: transform` on everything makes it fast.** Symptom: memory pressure and
slower rendering. Fix: set it just before the animation.

**Long synchronous work in an input handler.** Symptom: INP spikes. Fix: do the minimum needed
to update the UI, and defer the rest.

## Anti-patterns

- **Everything on the main thread.** If a user action can take more than a frame, most of it
  belongs in a worker.
- **`innerHTML` in a hot loop.** Each parse is a DOM mutation; batch with a `DocumentFragment`
  or build once.
- **Layout-triggering properties in a `requestAnimationFrame` loop.** Same class of bug as
  thrashing, just animated.
- **Caching API responses in `localStorage` as JSON.** Synchronous, string-only, no size
  headroom. Use IndexedDB.
- **A service worker that hides network errors.** Returning a cached empty response for a failed
  API call turns a visible bug into an invisible one. Distinguish offline from error.
- **Polling with `setInterval` instead of listening to events or using long-lived connections.**
- **Blocking the parser to guarantee script order.** Use `defer` or modules; both preserve order
  without blocking.
- **Optimizing before measuring.** `performance.mark`, `PerformanceObserver`, and the
  performance panel answer which stage is slow. Guessing wastes weeks.

## References

- HTML Living Standard, script processing: https://html.spec.whatwg.org/multipage/scripting.html
- HTML Living Standard, Web workers: https://html.spec.whatwg.org/multipage/workers.html
- DOM Standard: https://dom.spec.whatwg.org/
- web.dev, optimize CSS delivery: https://web.dev/articles/optimize-css
- MDN, `defer`: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/script#defer
- MDN, JavaScript modules: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules
- MDN, `document.readyState`: https://developer.mozilla.org/en-US/docs/Web/API/Document/readyState
- MDN, the event loop: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Execution_model
- MDN, `requestAnimationFrame`: https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame
- MDN, `PerformanceObserver`: https://developer.mozilla.org/en-US/docs/Web/API/PerformanceObserver
- MDN, Long Tasks API: https://developer.mozilla.org/en-US/docs/Web/API/Performance_API/Long_tasks_API
- MDN, Event Timing API: https://developer.mozilla.org/en-US/docs/Web/API/Performance_API/Event_timing_API
- MDN, Web Storage API: https://developer.mozilla.org/en-US/docs/Web/API/Web_Storage_API
- MDN, IndexedDB API: https://developer.mozilla.org/en-US/docs/Web/API/IndexedDB_API
- MDN, HTTP cookies: https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies
- MDN, Service Worker API: https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API
- MDN, Cache API: https://developer.mozilla.org/en-US/docs/Web/API/Cache_API
- MDN, `will-change`: https://developer.mozilla.org/en-US/docs/Web/CSS/will-change
- web.dev, rendering performance fundamentals: https://web.dev/articles/rendering-performance
- web.dev, avoid layout thrashing: https://web.dev/articles/avoid-layout-thrashing
- RFC 6265, HTTP State Management Mechanism (cookie limits): https://www.rfc-editor.org/rfc/rfc6265