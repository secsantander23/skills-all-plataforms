# State placement and stores

Read `../SKILL.md` first. This file covers the decision boundary between Context, a
reducer, and an external store, plus a hand-rolled `useSyncExternalStore` store you
can write yourself when no dependency is acceptable.

## Decision sequence

1. Does only this component and its children read **and** write it? Local `useState`.
2. Does a child write it that is several levels below the reader? Local state plus a
   callback prop, or a small reducer passed down as a stable value.
3. Do siblings share it? Lift one level, to the closest common ancestor.
4. Is it read-only and stable for the app's lifetime (i18n functions, a feature-flag
   object, an initialized client)? Context.
5. Does it change often, or is it read by many distant components that should re-render
   independently? External store with selectors.
6. Does it need complex transitions, undo, or time-travel? Reducer plus dispatch,
   usually delivered through Context.

## Why plain Context fails as a store

`useContext` has no selector. Any change to the provider value re-renders every
component under the provider, even if the component reads a slice that did not change.
Two mitigations before you reach for a dependency:

1. **Split providers by update frequency.** A slow context and a fast context in
   separate providers, with separate values, so a fast update only re-renders the
   subtree under the fast provider.
2. **Store the value in a stable object and mutate nothing**: the value's identity is
   the invalidation signal, so you must create a new object anyway. Splitting is the
   only real fix.

## Hand-rolled external store

`useSyncExternalStore` is the stable React primitive for subscribing to data that lives
outside React. This pattern works for a small amount of app state, for integrating a
non-React library, or for a library author.

```ts
// store.ts
export type Listener = () => void;

export interface Store<T> {
  getState: () => T;
  setState: (updater: (prev: T) => T) => void;
  subscribe: (listener: Listener) => () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<Listener>();
  return {
    getState: () => state,
    setState(updater) {
      const next = updater(state);
      if (Object.is(next, state)) return;
      state = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
```

```tsx
// useStore.ts
import { useCallback, useSyncExternalStore } from 'react';
import { createStore, type Store } from './store';

interface AppState {
  count: number;
  userName: string;
}

export const appStore = createStore<AppState>({
  count: 0,
  userName: 'anonymous',
});

export function useStoreSelector<T, S>(
  store: Store<T>,
  selector: (state: T) => S,
): S {
  const subscribe = useCallback(
    (onChange: () => void) => store.subscribe(onChange),
    [store],
  );
  const getSnapshot = useCallback(() => selector(store.getState()), [store, selector]);
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function useAppCount(): number {
  return useStoreSelector(appStore, (s) => s.count);
}
```

Two hazards with selector functions:

- The selector must return a **referentially stable** result for an unchanged slice.
  `s => ({ a: s.a })` allocates a new object each call and causes an infinite
  re-render loop. Return primitives, or memoize object results.
- A selector declared inline in a component is a new function each render, so
  `getSnapshot` changes identity each render. `useSyncExternalStore` re-reads the
  snapshot on every render, which is correct but does extra work. Hoist module-level
  selectors, or wrap them in `useCallback`.

```tsx
import { useCallback } from 'react';
import { appStore, useStoreSelector } from './useStore';

const selectSummary = (s: { count: number; userName: string }) => ({
  label: `${s.userName}: ${s.count}`,
});

// A selector returning a fresh object must be memoized or the snapshot is never
// referentially stable, and the component re-renders forever.
export function Summary() {
  const selector = useCallback(selectSummary, []);
  const summary = useStoreSelector(appStore, selector);
  return <p>{summary.label}</p>;
}
```

## Established libraries

When you take a dependency, prefer one with selector support and a stable public API:

- **Zustand**: `create` returns a hook you call at the top level of a component; state
  selectors are passed as the argument. Documentation: https://zustand.docs.pmnd.rs/
- **Jotai**: atom-based; derived state is another atom. Documentation:
  https://jotai.org/docs
- **Redux Toolkit**: `configureStore` plus `createSlice` and `createAsyncThunk`.
  Documentation: https://redux-toolkit.js.org/introduction/getting-started

Confirm the current API against the library's own docs. These libraries have all had
breaking API changes, and this file intentionally avoids pinning exact import shapes
beyond what the official pages guarantee.

## Server data is not UI state

Cached query results, infinite lists, and stale-while-revalidate behavior are a
different problem from UI state. Reach for a query library or the framework's server
data primitives rather than hand-rolling caching in a store. Hand-rolled caches
consistently get retry, deduplication, and invalidation wrong.

## Rules that survive library choice

- Keep derived data out of the store. Compute it at read time in the selector.
- Never store a promise. Store the resolved value plus status, or store the promise
  only if the library's cache owns the lifecycle.
- Make every store action serializable in spirit. Actions you cannot log cannot be
  debugged.
- Reset transient UI state (open panels, input drafts) on navigation, not in a store
  that outlives the page.
