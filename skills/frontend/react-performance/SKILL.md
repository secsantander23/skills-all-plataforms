---
name: React Performance
description: Covers profiling with React DevTools and the Profiler API, memo and useMemo tradeoffs, list virtualization, React.lazy code splitting, bundle analysis, render waterfall avoidance, and image loading attributes. Use when the user reports a slow React app, a long list that stutters, a large bundle, slow initial load, or unnecessary re-renders, and when deciding whether to memoize or optimize.
metadata:
  opencode/autoinvoke: true
---

# React Performance

Optimizing React is measurement plus three levers: **do less work**, **do it later**,
and **send less code**. Everything else is folklore. The dominant cause of "React is
slow" is a component tree re-rendering dozens of times per interaction, and the
dominant cause of "our app is slow to load" is a JavaScript bundle that was never
split.

Two hard rules:

1. Measure before and after. Every claim of improvement needs a recorded number.
2. Fix architecture problems (component boundaries, state placement, request
   waterfalls) before micro-optimizations. Reordering matters: memoizing a component
   that renders 200 times per second because its state is in the wrong place does
   nothing.

## When to use this

- A page feels sluggish, a list of hundreds of rows stutters, or input feels laggy.
- First contentful paint or time to interactive is worse than the budget.
- A user reports the app "gets slower as it grows".
- You need to decide between memoizing, code splitting, and virtualization.
- A bundle report shows one dependency dominating the payload.

## Quick start

Split the heavy part of a route out of the main bundle and give the loading state a
stable skeleton, so the initial render does not wait on the expensive module:

```tsx
import { Suspense, lazy, useMemo, useState } from 'react';

const ReportsDashboard = lazy(
  () => import('./ReportsDashboard').then((m) => ({ default: m.ReportsDashboard })),
);

export function ReportsPage() {
  const [range, setRange] = useState<'7d' | '30d'>('7d');
  const label = useMemo(() => `Reports, last ${range}`, [range]);

  return (
    <main>
      <h1>{label}</h1>
      <label htmlFor="range">Range</label>
      <select
        id="range"
        value={range}
        onChange={(event) => setRange(event.target.value as '7d' | '30d')}
      >
        <option value="7d">7 days</option>
        <option value="30d">30 days</option>
      </select>
      <Suspense fallback={<div aria-busy="true">Loading reports</div>}>
        <ReportsDashboard range={range} />
      </Suspense>
    </main>
  );
}
```

`React.lazy` needs a **default export**. Named exports need the `.then` mapping shown
above. Without a `<Suspense>` boundary above the lazy component, the app throws when
the chunk resolves.

## Profiling: where the time actually goes

Use the React DevTools browser extension. It includes the Profiler tab with a commit
timeline, a flamegraph, a ranked "views" list, and a "why did this render" tree that
attributes each render to a prop change or an update source.

1. Profile a **development** build. Production builds disable some instrumentation and
   double renders from StrictMode are absent.
2. Record the exact interaction that feels slow, not page load. Interaction latency
   is usually a render problem; load time is usually a bundle problem.
3. In the flamegraph, sort by duration. A component that is slow **once** at mount is a
   code-splitting or data-fetching problem. A component that is slow **every commit** is
   a re-render problem.
4. Use "why did this render" to find which prop changed. That tells you whether to
   stabilize the prop, split the component, or move the state.
5. Fix one thing. Re-record. Compare the same interaction.

For a programmatic measurement, wrap a subtree in `react`'s `Profiler`. It reports
`actualDuration` per commit, which is useful in a test harness or a performance
regression test:

```tsx
import { Profiler, type ProfilerOnRenderCallback } from 'react';

const onRender: ProfilerOnRenderCallback = (id, phase, actualDuration) => {
  if (actualDuration > 16) {
    console.warn(`[perf] ${String(id)} ${phase} took ${actualDuration.toFixed(1)}ms`);
  }
};

export function App() {
  return (
    <Profiler id="checkout" onRender={onRender}>
      <CheckoutPage />
    </Profiler>
  );
}
```

`actualDuration` is a debugging aid, not a production metric. Use your browser's real
performance tooling for shipped measurement.

## Memoization: the decision table

| Situation | Action |
| --- | --- |
| Props are stable and the child renders a large subtree | `React.memo` on the child |
| An expensive pure computation feeds a memoized child | `useMemo` |
| A callback prop is passed to a memoized child | `useCallback` |
| A text input that must stay responsive while typing elsewhere | `useDeferredValue` on the slow value, or `startTransition` for the update |
| An expensive update the user can interrupt | `startTransition` plus a pending flag |
| Nothing was measured as slow | Do nothing |

```tsx
import { memo, useCallback, useDeferredValue, useState } from 'react';

interface ResultListProps {
  query: string;
  results: { id: string; title: string }[];
}

const ResultList = memo(function ResultList({ results }: ResultListProps) {
  return (
    <ul>
      {results.map((r) => (
        <li key={r.id}>{r.title}</li>
      ))}
    </ul>
  );
});

export function Search() {
  const [query, setQuery] = useState('');
  const [results] = useState<{ id: string; title: string }[]>([]);
  const deferredQuery = useDeferredValue(query);
  const isStale = deferredQuery !== query;

  const handleSubmit = useCallback(() => {
    // Runs after the input state update, so typing stays responsive.
    void submitSearch(deferredQuery);
  }, [deferredQuery]);

  return (
    <div>
      <label htmlFor="q">Search</label>
      <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} />
      <button type="button" onClick={handleSubmit} disabled={isStale}>
        Search
      </button>
      <ResultList results={results} />
    </div>
  );
}

async function submitSearch(q: string): Promise<void> {
  await Promise.resolve(q);
}
```

Note: `memo(ResultList)` here is only useful because `results` comes from state and is
therefore referentially stable across renders. If you built `results` with `filter()` in
the component body, the memo does nothing.

Also note `query` is passed to nothing here, so `useDeferredValue` is illustrative only.
In a real app the deferred value is what feeds the expensive filter or fetch.

**Do not memoize by default.** Memoization adds a dependency array to keep correct, and
a wrong one produces stale UI that is far worse than a slow render. The full cost
analysis and custom comparator pitfalls are in
`../react-architecture/references/memoization.md`.

## Virtualization for long lists

Rendering 5,000 rows means 5,000 React elements, 5,000 DOM nodes, and a large memory
and layout cost. Virtualization renders only the visible window plus a small
overscan, and recycles row elements as the user scrolls.

Prefer a maintained virtualizer library rather than writing your own. Two widely used
options:

- **TanStack Virtual** (`@tanstack/react-virtual`): `useVirtualizer` with
  `getScrollElement`, `estimateSize`, and `getItemKey`. Documentation:
  https://tanstack.com/virtual/latest/docs/framework/react/react-virtual
- **react-window**: a lower-level API that has changed across major versions. Check the
  installed version's documentation before writing code.

```tsx
import { useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface Row {
  id: string;
  label: string;
}

export function VirtualList({ rows }: { rows: Row[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 44,
    getItemKey: (index) => rows[index].id,
    overscan: 8,
  });

  return (
    <div
      ref={scrollRef}
      style={{ height: 480, overflow: 'auto' }}
      tabIndex={0}
      role="list"
      aria-label="Results"
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
        {virtualizer.getVirtualItems().map((item) => (
          <div
            key={item.key}
            role="listitem"
            aria-setsize={rows.length}
            aria-posinset={item.index + 1}
            data-index={item.index}
            ref={virtualizer.measureElement}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${String(item.start)}px)`,
            }}
          >
            {rows[item.index].label}
          </div>
        ))}
      </div>
    </div>
  );
}
```

Virtualization tradeoffs to state up front:

- **Accessibility.** A virtualized list is not in the accessibility tree in the usual
  way: off-screen rows are absent, so a screen reader user cannot browse them. Offer
  the full data in an alternative, and use `aria-setsize` and `aria-posinset` as above
  so position information survives.
- **Search and find-in-page** stops working for off-screen content. Ctrl+F will not
  find text that is not rendered.
- **SEO.** Crawlers may not see the full list. If the list is the page's content,
  paginate server-side instead.
- **Variable heights** need `measureElement`; fixed heights are much cheaper.
- **Print and screenshot** show only the rendered window. Render a full copy for print.

## Code splitting and lazy loading

Split at route boundaries first. Each route a user does not visit should not be in the
initial bundle.

| What | How |
| --- | --- |
| Route component | Framework-level lazy routes, or `React.lazy` inside your router definition |
| Heavy, rarely used widget | `React.lazy` plus a `<Suspense>` boundary around just that widget |
| Large vendor library | Prefer a per-feature dynamic `import()`, or a smaller alternative |
| Icon set | Import individual icons, not the whole set |
| Locale files | Dynamic `import()` per locale, load on demand |
| Below-the-fold content | Lazy plus a root margin on the intersection that triggers it |

```tsx
import { lazy, Suspense } from 'react';

const SettingsPanel = lazy(() => import('./SettingsPanel'));

export function Sidebar() {
  return (
    <nav>
      <button type="button" onClick={openSettings}>
        Settings
      </button>
      {isSettingsOpen ? (
        <Suspense fallback={<p>Loading settings</p>}>
          <SettingsPanel />
        </Suspense>
      ) : null}
    </nav>
  );
}

declare function openSettings(): void;
```

Two splitting mistakes:

- **Splitting too finely.** Each chunk is an extra HTTP request and a new dependency
  graph. Splitting every component produces many tiny chunks that often load slower
  overall. Split by route and by genuinely heavy widget.
- **Deferring work the user immediately sees.** Lazy-loading the first thing above the
  fold adds a round trip to the critical path. Measure with real users or real
  lab timings, not intuition.

## Bundle analysis

You cannot optimize what you have not measured. Build a production bundle and inspect
it.

```bash
# Vite: emits a manifest and reports chunk sizes per entry
npx vite build

# Vite with an interactive visualizer of the module graph
npx vite-bundle-visualizer

# Webpack
npx webpack --profile --json=stats.json
npx webpack-bundle-analyzer stats.json
```

Other real options: `source-map-explorer` against a source-mapped build, and
Bundlephobia for checking the cost of a dependency before you add it
(https://bundlephobia.com). Verify the flags your bundler accepts with `--help`; bundler
CLI surface changes between major versions.

When you read a bundle report, look for:

- One dependency dominating the bundle. Often a date library, a chart library, a
  moment/locale bundle, or an icon set imported wholesale.
- A duplicate copy of the same library at different versions, caused by incompatible
  peer dependency ranges.
- Application code that landed in the vendor chunk because a plugin forced it.
- Source maps and inline assets inflating the JavaScript file.

## Render waterfall avoidance

A waterfall is any request that cannot start until an earlier one finishes.

- **Server data**: start independent requests together with `Promise.all`, or give each
  section its own `<Suspense>` so the framework streams them in parallel.
- **Client effects**: a `useEffect` that fetches, then a second effect that fetches
  based on the first result, serializes two round trips. Hoist the dependency so both
  start at once.
- **Cascading components**: a component that renders `null` until data arrives prevents
  its children from rendering at all. Render the shell immediately and let each data
  region stream.

```tsx
import { Suspense } from 'react';

export default function Dashboard() {
  // Both fetches start when their Suspense boundaries render, not sequentially.
  return (
    <main>
      <h1>Dashboard</h1>
      <Suspense fallback={<RevenueSkeleton />}>
        <Revenue />
      </Suspense>
      <Suspense fallback={<OrdersSkeleton />}>
        <Orders />
      </Suspense>
    </main>
  );
}

declare function Revenue(): React.ReactNode;
declare function Orders(): React.ReactNode;
declare function RevenueSkeleton(): React.ReactNode;
declare function OrdersSkeleton(): React.ReactNode;
```

To make a waterfall measurable, enable network throttling in your browser's devtools
and record a full page load in a production build. Do not measure waterfalls on a local
unthrottled connection.

## Images

Images are usually the largest bytes on a page and the most common cause of layout
shift. These are plain HTML attributes, not React APIs:

```tsx
<img
  src="/photos/harbour-800.jpg"
  srcSet="/photos/harbour-400.jpg 400w, /photos/harbour-800.jpg 800w, /photos/harbour-1600.jpg 1600w"
  sizes="(max-width: 600px) 100vw, (max-width: 1200px) 50vw, 600px"
  width={800}
  height={500}
  loading="lazy"
  decoding="async"
  alt="Fishing boats moored in a harbour at dawn"
/>
```

- `sizes` must describe the actual layout at each breakpoint. A wrong `sizes` makes
  `srcSet` pick the wrong file and wastes bandwidth.
- Always set `width` and `height` (or an `aspect-ratio` style) to reserve space and
  avoid cumulative layout shift.
- `loading="lazy"` on below-the-fold images only. Lazy-loading the hero image delays
  the largest contentful paint.
- `alt` describes the content, not the file. Use `alt=""` for decorative images.
- Use modern formats (AVIF, WebP) with fallbacks, and serve responsive sizes from a CDN
  or image pipeline. Framework components such as Next.js `next/image` and Vite image
  plugins generate equivalent attributes automatically; check your framework's current
  documentation for exact props.

## Avoiding unnecessary re-renders

Work down this list; it is ordered by impact.

1. **State in the right place.** State above a consumer that does not read it re-renders
   that consumer. This is the top cause of wide re-renders.
2. **Stable props into memoized children.** Objects and functions built inline in JSX
   or in the component body break `memo`.
3. **Don't store derived state.** Derived state means an effect, and effects that set
   state cause an extra render pass on every change.
4. **Split contexts by update frequency.** One fast value in a Context re-renders every
   consumer.
5. **Give lists stable keys.** Index keys force a full patch on reorder.
6. **Only then add `memo`/`useMemo`/`useCallback`,** with the Profiler open.

Also: React DevTools can highlight components that re-render while you interact. Use
that to find the specific subtrees a single interaction touches.

## Checklist

- A production build is profiled for the slow interaction and for initial load.
- Every route boundary is code-split; the initial bundle contains no unused routes.
- The bundle report has been read and the largest contributors are named.
- Lists over roughly 100 complex rows are virtualized, with the accessibility caveats
  handled.
- No `memo`, `useMemo`, or `useCallback` without a measured reason.
- Images have explicit dimensions, responsive `sizes`, and descriptive `alt`.
- Independent data requests start in parallel; no effect chains fetch sequentially.
- Critical-path requests are not lazy-loaded.

## Common mistakes

- **`useEffect` for data fetching on mount, with `loading`/`error`/`data` states.**
  Symptom: an extra render pass, a flash of empty UI, and a request that starts after
  hydration. Fix: use the framework's server data support or a query library.
- **Memoizing every handler.** Symptom: a large `useCallback` surface with dependencies
  that are subtly wrong. Fix: memoize only callbacks consumed by `memo`ized children.
- **`React.lazy` with no `Suspense` above it.** Symptom: `A component suspended while
  responding to synchronous input` or a blank route. Fix: add a boundary at or above
  the lazy component.
- **Lazy-loading the above-the-fold component.** Symptom: LCP gets worse after the
  "optimization". Fix: split the route, not the critical path.
- **Virtualizing a 200-row list.** Symptom: scroll jitter and broken find-in-page for
  no gain. Fix: virtualize only when the DOM cost is actually measured.
- **`loading="lazy"` on every image including the hero.** Symptom: LCP regression.
  Fix: eager-load the LCP image, and consider `fetchpriority="high"` on it.
- **Hand-rolled virtualizer.** Symptom: scroll position drifts and overscan pops.
  Fix: use a maintained library.
- **A wide Context holding socket data.** Symptom: a fast-updating value re-renders
  the whole app. Fix: store with selectors.

## Anti-patterns

- **Adding `memo` to every component as a style rule.** It encodes the mistake of
  unstable props into the codebase and makes review harder.
- **Performance work without a recorded before/after number.** Unfalsifiable and
  unreviewable. If you cannot measure it, do not claim it.
- **Optimizing `StrictMode` double rendering away.** Development double rendering is a
  correctness check. Removing StrictMode to make the Profiler look clean hides real
  impure renders.
- **Bundle splitting per component.** Many tiny chunks usually regress load time.
- **"Debounce everything" as a performance strategy.** Debouncing hides a slow render;
  it does not make the render faster, and it adds latency.
- **Copying numbers from a blog post.** Benchmarks are hardware-, build-, and
  version-specific. Measure on your own page.
- **Treating Lighthouse scores as a budget for the whole product.** Lab scores are a
  smoke test. Use real user monitoring for shipped regression detection.

## References

- React DevTools: https://react.dev/learn/react-developer-tools
- `<Profiler>`: https://react.dev/reference/react/Profiler
- `memo`: https://react.dev/reference/react/memo
- `useMemo` and `useCallback`: https://react.dev/reference/react/useMemo
- `useDeferredValue`: https://react.dev/reference/react/useDeferredValue
- `useTransition`: https://react.dev/reference/react/useTransition
- `lazy` and `Suspense`: https://react.dev/reference/react/lazy
- `<link rel="preload">` and resource hints: https://developer.mozilla.org/docs/Web/HTML/Reference/Attributes/rel/preload
- `srcset` and `sizes`: https://developer.mozilla.org/docs/Web/HTML/Reference/Attributes/srcset
- Core Web Vitals: https://web.dev/articles/vitals
- TanStack Virtual: https://tanstack.com/virtual/latest/docs/framework/react/react-virtual
- Bundlephobia: https://bundlephobia.com
- Webpack bundle analyzer: https://webpack.github.io/webpack-bundle-analyzer/
- Deeper notes: `../react-architecture/references/memoization.md`

**Verify before relying on this:** the React Compiler, Server Components, and the form
action hooks all changed shape across recent React releases. Confirm the current API
at react.dev before adopting anything in this file.
