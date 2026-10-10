---
name: React Application Architecture
description: Covers component composition and boundaries, state colocation, lifting and deriving state, Context versus external stores, Rules of Hooks, Server Components, error boundaries, list keys, and memoization tradeoffs. Use when the user asks about structuring a React app, deciding where state should live, splitting large components, debugging re-render or hook bugs, or reviewing a component tree.
metadata:
  opencode/autoinvoke: true
---

# React Application Architecture

React's core mental model is a pure function from state to UI. Architecture work is
almost entirely about deciding **where state lives**, **where the component boundaries
are**, and **which updates are allowed to re-render what**. Get those three wrong and
performance, correctness, and testability all degrade together.

The single most useful heuristic: push state as far down the tree as the code that
reads and writes it, and push components as far up as the code that needs to vary
their structure.

## When to use this

- Deciding whether a piece of state belongs in a component, a Context, or a store.
- A component has grown past a few hundred lines and needs splitting.
- Debugging "it re-renders everything" or a stale-closure bug.
- Migrating between Client Components and Server Components.
- Adding error handling around a subtree that can fail at render time.
- Reviewing a PR that adds Context, memo, or an effects-based data fetch.

## Quick start

A component that owns its own state, exposes events, and keeps derived values computed
during render rather than stored:

```tsx
import { useId, useState } from 'react';

export interface TodoItem {
  id: string;
  label: string;
  done: boolean;
}

export function TodoList({ initialItems }: { initialItems: TodoItem[] }) {
  // State is colocated: only TodoList and its children need it.
  const [items, setItems] = useState<TodoItem[]>(initialItems);
  const [filter, setFilter] = useState<'all' | 'open' | 'done'>('all');
  const emptyMessageId = useId();

  // Derived during render. No useEffect, no second source of truth.
  const visible = items.filter((item) => {
    if (filter === 'all') return true;
    return filter === 'done' ? item.done : !item.done;
  });
  const remaining = items.reduce((n, item) => (item.done ? n : n + 1), 0);

  function toggle(id: string) {
    setItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, done: !item.done } : item)),
    );
  }

  return (
    <section aria-labelledby={emptyMessageId}>
      <p id={emptyMessageId}>
        {remaining} of {items.length} remaining
      </p>
      <div role="group" aria-label="Filter todos">
        {(['all', 'open', 'done'] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {value}
          </button>
        ))}
      </div>
      {visible.length === 0 ? (
        <p>Nothing to show.</p>
      ) : (
        <ul>
          {visible.map((item) => (
            <li key={item.id}>
              <label>
                <input
                  type="checkbox"
                  checked={item.done}
                  onChange={() => toggle(item.id)}
                />
                {item.label}
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

Note what is absent: no `useEffect` mirroring `items` into a "filtered" state, no
`useMemo` on a cheap filter, no Context. That is intentional.

## Component composition and boundaries

Split a component when the reasons to change diverge, not when the file gets long.

| Signal | What it means |
| --- | --- |
| The same JSX shape repeats with different data | Extract a component |
| Part of the subtree needs its own state that nothing above reads | Extract to colocate state |
| Part of the subtree re-renders far more often than the rest | Extract so you can memoize the boundary |
| Part of the subtree can fail independently | Extract to wrap an error boundary |
| Part of the subtree must not ship to the browser | Extract to a Server Component |

Strong signals that you should **not** split: the piece is three lines of JSX, it is
only used once, and splitting it requires threading six props to avoid prop drilling.
Prop drilling through two or three levels is often cheaper than a Context.

Compose with `children` to let the parent control structure:

```tsx
import type { ReactNode } from 'react';

interface CardProps {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Card({ title, actions, children }: CardProps) {
  return (
    <article className="card">
      <header>
        <h2>{title}</h2>
        {actions}
      </header>
      {children}
    </article>
  );
}

// Parent decides layout and content. Card stays agnostic.
export function ProductPage() {
  return (
    <Card title="Widget" actions={<button type="button">Buy</button>}>
      <p>Shipping in 3 to 5 days.</p>
    </Card>
  );
}
```

Prefer several small props over a single `config` object. A props object forces the
consumer to spread it and hides which fields are actually used, which defeats
`memo` comparisons and TypeScript excess-property checking.

## State: colocate, lift, derive

**Colocate.** State used by one component and its children lives in that component.
Adding it to a shared parent means every sibling re-renders when it changes.

**Lift only on a real write-sharing need.** Lift when two siblings must read *and*
write the same value. Lift for coordination, not for convenience. Coordinate through
a reducer or a callback prop instead of a bag of booleans:

```tsx
import { useReducer } from 'react';

type Action =
  | { type: 'stepUp' }
  | { type: 'stepDown' }
  | { type: 'reset' };

function reducer(state: number, action: Action): number {
  switch (action.type) {
    case 'stepUp':
      return state + 1;
    case 'stepDown':
      return state - 1;
    case 'reset':
      return 0;
  }
}

export function Stepper() {
  const [count, dispatch] = useReducer(reducer, 0);
  return (
    <div>
      <output>{count}</output>
      <button type="button" onClick={() => dispatch({ type: 'stepUp' })}>
        +1
      </button>
      <button type="button" onClick={() => dispatch({ type: 'stepDown' })}>
        -1
      </button>
      <button type="button" onClick={() => dispatch({ type: 'reset' })}>
        reset
      </button>
    </div>
  );
}
```

**Derive, do not store.** If a value can be computed from existing state or props
during render, compute it. Storing derived state means writing an effect that keeps
it in sync, and every such effect is a chance to desync.

Bad, and it breaks the moment `items` changes outside the toggle handler:

```tsx
// Wrong: two sources of truth plus a sync effect.
const [items, setItems] = useState(initialItems);
const [remaining, setRemaining] = useState(initialItems.length);
useEffect(() => {
  setRemaining(items.filter((i) => !i.done).length);
}, [items]);
```

Good, computed inline as shown in the quick start. Reach for `useMemo` only when the
computation is measurably expensive (see `references/memoization.md`).

**Effects are for synchronizing with systems outside React**: subscriptions, timers,
the DOM, the network, imperative third-party widgets. If an effect only reads and
writes state you own, delete it.

## Context versus an external store

Context is a dependency-injection mechanism, not a state manager. Every consumer of a
Context re-renders when the value identity changes, regardless of which field it read.

| Need | Use |
| --- | --- |
| Stable, rarely changing value (theme object, i18n functions, feature flags) | Context |
| High-frequency, frequently changing value (mouse position, form state, cache) | External store with selectors |
| Cross-tree state with complex transitions | Reducer plus Context, or a store |
| Server-fetched, read-only data | Server Components and framework data APIs |

Split a single Context by update frequency. One Context holding `{user, theme,
notifications}` re-renders every consumer when notifications tick.

```tsx
import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

interface ThemeValue {
  scheme: 'light' | 'dark';
  setScheme: (scheme: 'light' | 'dark') => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [scheme, setScheme] = useState<'light' | 'dark'>('light');
  // Identity changes only when scheme changes, not on every provider render.
  const value = useMemo(() => ({ scheme, setScheme }), [scheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (ctx === null) {
    throw new Error('useTheme must be used inside <ThemeProvider>');
  }
  return ctx;
}
```

For state that updates often and is read in many places, prefer an external store
that supports selector subscriptions (Zustand, Jotai, Redux Toolkit, or a small
`useSyncExternalStore` implementation of your own). Components then subscribe to the
slices they read. Details and a hand-rolled store example are in
`references/state-management.md`.

## Rules of Hooks and the pitfalls that break them

Hooks must be called at the top level of a function component or a custom hook. React
identifies hooks by call order, so these all break it:

- Calling a hook inside a condition, loop, `try`, or nested function.
- Calling hooks after an early `return`.
- Calling React hooks inside a class method or a non-component function.

```tsx
// Wrong: conditional hook call. React logs "Rendered more hooks than during the
// previous render" or "Rendered fewer hooks than expected".
function Panel({ collapsed }: { collapsed: boolean }) {
  if (!collapsed) {
    const [open, setOpen] = useState(false); // hook count differs per render path
    return <button onClick={() => setOpen(!open)}>{String(open)}</button>;
  }
  return null;
}

// Right: always call, then branch on the value.
function Panel({ collapsed }: { collapsed: boolean }) {
  const [open, setOpen] = useState(false);
  if (collapsed) return null;
  return (
    <button type="button" onClick={() => setOpen(!open)}>
      {open ? 'collapse' : 'expand'}
    </button>
  );
}
```

Other real failure modes:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ReferenceError: setX is not defined` in a callback | Missing dependency array entry, so the closure captured the first render's value | Include the setter or read state from a functional update |
| Effect runs twice in development | StrictMode double-invokes render and effect | Make effects cleanup-safe and idempotent; do not "fix" it by removing StrictMode |
| Stale props inside a timer or subscription | Effect closure captured old props | Include the value in the dependency array, or use a ref for the latest value |
| Infinite effect loop | Effect sets state that the effect also reads | Move the value into derived state instead |
| Custom hook called conditionally | Helper function that itself calls hooks was wrapped in an `if` | Call the hook unconditionally at the component top level |

A general rule for external subscriptions: return a cleanup from every effect.

```tsx
import { useEffect } from 'react';

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
```

## Server Components and Client Components

This area has churned across React releases and frameworks. The stable model:

- A **Server Component** runs only on the server. It is `async`, may read data
  directly, and cannot use `useState`, `useEffect`, event handlers, or browser APIs.
  It may be a plain function with no directive at all.
- A **Client Component** runs during server rendering *and* in the browser. It needs
  the `'use client'` directive at the top of the module, which puts that module and
  everything it imports into the client bundle.
- A Server Component may import and render Client Components and pass them serializable
  props. A Client Component cannot import a Server Component.

```tsx
// app/products/[id]/page.tsx  (Server Component, no directive)
import { notFound } from 'next/navigation';
import { getProduct } from '@/lib/catalog';
import { AddToCartButton } from './AddToCartButton';

export default async function ProductPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const product = await getProduct(id);
  if (!product) notFound();
  return (
    <main>
      <h1>{product.name}</h1>
      <p>{product.description}</p>
      <AddToCartButton productId={product.id} price={product.price} />
    </main>
  );
}
```

```tsx
// app/products/[id]/AddToCartButton.tsx  (Client Component)
'use client';

import { useState } from 'react';

export function AddToCartButton({
  productId,
  price,
}: {
  productId: string;
  price: number;
}) {
  const [added, setAdded] = useState(false);
  return (
    <button type="button" onClick={() => setAdded(true)}>
      {added ? 'Added to cart' : `Add to cart - $${price.toFixed(2)}`}
    </button>
  );
}
```

Design rules that keep the split clean:

1. Push `'use client'` as far down as possible. A `'use client'` boundary at the route
   root effectively opts the whole page into client rendering.
2. Never pass a function, class instance, or `Symbol` from a Server Component to a
   Client Component. Pass plain serializable data and let the client own the behavior.
3. Fetch in parallel with `Promise.all`, not sequential `await`s, or you serialize the
   data waterfall.
4. Use `<Suspense>` around slow subtrees so a partial page streams.

`use()` lets you read a promise or context inside components including Client
Components. Form actions in current React versions include `useActionState`,
`useFormStatus`, and `useOptimistic`. **Verify before relying on this:** the exact set
of stable hook signatures has changed across React major versions and across RSC
frameworks. Check the API reference at the URL in References rather than trusting a
snippet from memory.

## Error boundaries

Error boundaries catch render-time throws in descendants. They are still class
components; there is no stable hook equivalent. They do **not** catch errors in event
handlers, in async callbacks, or in the boundary's own render.

```tsx
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  private reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error !== null) {
      return this.props.fallback ? (
        this.props.fallback(error, this.reset)
      ) : (
        <p role="alert">This section failed to load.</p>
      );
    }
    return this.props.children;
  }
}
```

Place boundaries at meaningful failure seams: per route segment, per widget, per
widget in a dashboard. A single boundary at the app root replaces the whole UI with an
error page, which is worse than the crash it caught. Always give a way to retry.

For errors in event handlers or data fetching, catch them explicitly and surface a
message; a boundary will not help.

## Lists and keys

Keys tell React which item in a list is which across renders.

| Key | Result |
| --- | --- |
| Stable database id | Correct: preserves DOM, focus, input state, and transition identity |
| Array index | Only correct for a static list that never reorders, filters, or deletes |
| `Math.random()` or `Date.now()` | Every render remounts every row. Loses focus and input state |
| Duplicate of another key | Undefined behavior and duplicated DOM |

```tsx
<ul>
  {rows.map((row) => (
    <li key={row.id}>{row.label}</li>
  ))}
</ul>
```

Keys are scoped to the array. A key is not a prop, is not readable by the child, and
is not a DOM id. Reordering a keyed list moves nodes; reordering an index-keyed list
makes React patch every row in place, which is both a correctness bug (input values
follow the position, not the record) and the usual cause of slow reorders.

If you render a nested list, the inner key must be unique among siblings, not globally.

## Memoization tradeoffs

`React.memo`, `useMemo`, and `useCallback` cost memory and code complexity and buy
nothing unless the work is real. Measure with the React DevTools Profiler before
adding them.

```tsx
import { memo, useCallback, useMemo, useState } from 'react';

interface RowProps {
  id: string;
  label: string;
  selected: boolean;
  onSelect: (id: string) => void;
}

const Row = memo(function Row({ id, label, selected, onSelect }: RowProps) {
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(id)}
      >
        {label}
      </button>
    </li>
  );
});

export function Table({ rows }: { rows: { id: string; label: string }[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // Stable identity is required here: a new function every render defeats memo on Row.
  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const visible = useMemo(
    () => rows.filter((r) => r.label.toLowerCase().includes(query.toLowerCase())),
    [rows, query],
  );

  return (
    <div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} />
      <ul>
        {visible.map((row) => (
          <Row
            key={row.id}
            id={row.id}
            label={row.label}
            selected={row.id === selectedId}
            onSelect={handleSelect}
          />
        ))}
      </ul>
    </div>
  );
}
```

What this example shows and what it does not:

- `useCallback` here is only useful **because** `Row` is wrapped in `memo`. A stable
  callback with no memoized child is pure overhead.
- `useMemo` is only useful because `visible` is passed as a prop into memoized rows,
  so a new array would re-render every row.
- Prop objects always have a new identity, so memo does nothing unless the underlying
  values are referentially stable.
- `memo` uses a shallow `Object.is` comparison per prop. An object or function prop
  that is recreated each render defeats it.

Automatic memoization (React Compiler) can remove the manual `useMemo`/`useCallback`
boilerplate, but its availability and defaults vary by setup. Check the official
documentation for the version you are on before assuming it is active.

Details, profiler workflow, and the list of memoization cases that are almost always a
waste are in `references/memoization.md`.

## Custom hooks

A custom hook is a plain function whose name starts with `use` that may call other
hooks. It is the standard way to extract logic without adding a component layer.

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';

interface AsyncState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
}

export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: undefined,
    error: undefined,
    loading: true,
  });
  const latest = useRef(0);

  const run = useCallback(async () => {
    const token = ++latest.current;
    setState((prev) => ({ ...prev, loading: true }));
    try {
      const data = await fn();
      if (token === latest.current) setState({ data, error: undefined, loading: false });
    } catch (error) {
      if (token === latest.current) {
        setState({ data: undefined, error: error as Error, loading: false });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    void run();
  }, [run]);

  return state;
}
```

Rules that keep custom hooks safe:

- Never call a custom hook conditionally. Name it `useX` and treat the rules of hooks
  as binding.
- Return a tuple for two or three related values, an object once there are more, and
  keep it stable in shape.
- Do not return a JSX-returning function with a capitalized name. That hides a
  component from the linter and from React DevTools.

## Checklist

- No state is derived from other state; no effect exists purely to sync two pieces of
  React state.
- Every stateful widget owns its own state unless a sibling must write the same value.
- Component boundaries fall along "reasons to change" lines, not line counts.
- Every subscription effect returns a cleanup function.
- Hooks are called unconditionally at the top level of components and custom hooks.
- Lists use stable, unique, data-derived keys.
- Every `'use client'` boundary is as deep in the tree as the interaction requires.
- Error boundaries exist per major route or widget, each with a retry path.
- Every `memo`/`useMemo`/`useCallback` traces back to a Profiler observation.

## Common mistakes

- **Effect used to mirror props into state.** Symptom: the UI shows one render behind,
  and a second effect has to "correct" it. Fix: derive it during render, or key the
  component on the prop to reset its state.
- **Context holding unrelated, frequently changing values.** Symptom: typing in one
  modal re-renders an unrelated chart. Fix: split by update frequency or use a selector
  store.
- **A callback prop recreated every render.** Symptom: `memo` children still re-render
  and the Profiler shows no reduction. Fix: `useCallback`, or remove `memo`.
- **Setting state inside a `useEffect` that reads that same state.** Symptom: maximum
  update depth exceeded. Fix: compute the value during render.
- **Awaiting requests sequentially in a Server Component.** Symptom: slow TTFB. Fix:
  `Promise.all`, or wrap each section in `<Suspense>` so they stream in parallel.
- **Assuming an error boundary catches a failed `fetch`.** Symptom: error boundary
  never fires. Fix: boundaries only catch render and lifecycle errors. Handle async
  errors in the component that started them.
- **`key={index}` on a reorderable list.** Symptom: input focus jumps and checkbox
  state follows the row position. Fix: use the record id.

## Anti-patterns

- **A global state store holding everything.** A store is not an excuse to make all
  data globally reachable. Local state is cheaper to reason about and to delete.
- **One giant `useReducer` with a switch over the whole app.** Action names start
  colliding and every dispatch becomes a stringly-typed guess. Scope reducers to a
  feature.
- **`useEffect` for data fetching plus a `loading` boolean plus a `data` state plus an
  `error` state.** This is three states that can be in illegal combinations. Prefer the
  framework's data primitives or a query library with a documented cache.
- **Memoizing everything preemptively.** `useMemo` on a string concat or a small array
  filter adds a dependency array to maintain and buys nothing measurable.
- **`children` replaced by a `renderContent` prop that takes a component type.** You
  lose the ability to pass different content per call site and gain a prop type that
  fights inference.
- **Context used for dependency injection of a huge SDK client.** The value changes on
  every provider render and every consumer re-renders. Create the client once outside
  React or in a module-level singleton.
- **Splitting a component into a `.container.tsx` and `.view.tsx` purely by filename.**
  File layout is not component architecture. Separate only when the data access and
  the presentation genuinely have different change reasons.

## References

- React API reference: https://react.dev/reference/react
- Rules of Hooks: https://react.dev/reference/rules/rules-of-hooks
- `useState`: https://react.dev/reference/react/useState
- `useEffect`: https://react.dev/reference/react/useEffect
- `useMemo` and `useCallback`: https://react.dev/reference/react/useMemo
- `memo`: https://react.dev/reference/react/memo
- `useSyncExternalStore`: https://react.dev/reference/react/useSyncExternalStore
- Thinking in React (state colocation, derived state): https://react.dev/learn/thinking-in-react
- React Server Components: https://react.dev/reference/rsc/server-components
- Error handling during rendering: https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary
- List rendering and keys: https://react.dev/learn/rendering-lists
- React DevTools Profiler: https://react.dev/reference/react/Profiler
- Deeper notes: `references/memoization.md`, `references/state-management.md`
