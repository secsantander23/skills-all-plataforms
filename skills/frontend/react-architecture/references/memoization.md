# Memoization in React: when it pays and when it does not

Memoization in React costs three things: a comparison on every update, retained
memory for cached values, and a dependency array that a human must keep correct. It
pays only when the skipped work is genuinely expensive.

## The four tools

| Tool | What it caches | Common misuse |
| --- | --- | --- |
| `React.memo(Component)` | Skips re-render when props are shallow-equal | Wrapping a component whose props are all recreated each render |
| `useMemo(fn, deps)` | The return value of `fn` | Wrapping cheap computation |
| `useCallback(fn, deps)` | The function identity of `fn` | Used without a memoized consumer |
| `memo(compare, Component)` | Result of a custom prop comparison | A comparator that returns `true` too often, silently dropping updates |

## Profile first

1. Run the app with a development build. StrictMode is intentional; do not disable it
   to make the Profiler quieter.
2. Open React DevTools, select the Profiler tab, record an interaction.
3. Look for components that render on almost every commit and that are expensive or
   large. "Renders on every commit" is not itself a defect.
4. Only then decide whether to split the component, stabilize a prop, or memoize.

The Profiler's "why did this render" tree names the props that changed and the
component that triggered the update. That tree is the whole diagnosis; guessing
without it wastes more time than the memo saves.

## What is actually worth memoizing

Worth it, usually:

- Rendering a large subtree (hundreds of rows, a chart, a syntax-highlighted block) from
  props that rarely change while an unrelated sibling updates.
- Sorting or filtering a large collection that feeds a memoized child.
- An expensive pure computation with measurable cost, such as layout math or a
  diff over thousands of items.
- A context value object that is read by many consumers.

Not worth it:

- String concatenation, small array filters over a few hundred short strings, or any
  object literal that a memoized child reads.
- `useCallback` on a handler passed to a plain DOM element. React attaches the same
  function to the DOM either way; the memoization is invisible.
- `useMemo` on a value that is used exactly once, in the same component, immediately.
- Everything, applied by a lint rule. Blanket `useMemo` produces worse code than none.

## Custom comparators

A custom comparator replaces the shallow comparison entirely. It is easy to write one
that hides real updates:

```tsx
import { memo } from 'react';

interface Props {
  user: { id: string; name: string; avatarUrl: string | null };
  onSelect: (id: string) => void;
}

const UserCard = memo(
  function UserCard({ user, onSelect }: Props) {
    return (
      <button type="button" onClick={() => onSelect(user.id)}>
        <img src={user.avatarUrl ?? '/avatar-fallback.png'} alt="" />
        {user.name}
      </button>
    );
  },
  (prev, next) => prev.user.id === next.user.id && prev.onSelect === next.onSelect,
);
```

That comparator says: ignore changes to `user.name` and `user.avatarUrl`. If the
profile screen is one of the consumers, the card silently shows stale data. A
comparator must be derived from what the component actually renders, and it must keep
function props in the comparison. When in doubt, prefer stabilizing props over
suppressing the comparison.

## Dependency arrays

`useMemo`, `useCallback`, and `useEffect` compare dependencies with `Object.is`. That
means:

- A dependency created inline (`{ id, tags: [] }`) is a new value every render and
  defeats the memo. This is the most common cause of "my useMemo does nothing".
- If a callback must read a value that changes often but should not retrigger the
  memo, keep the latest value in a ref and read the ref inside the callback. Then omit
  it from the dependency array, with a comment explaining why.

```tsx
import { useCallback, useEffect, useRef } from 'react';

export function useLatest<T>(value: T) {
  const ref = useRef(value);
  useEffect(() => {
    ref.current = value;
  }, [value]);
  return ref;
}

// handler keeps a stable identity while still reading the newest value.
export function useStableHandler<T extends (...args: never[]) => unknown>(fn: T): T {
  const ref = useLatest(fn);
  return useCallback(((...args: never[]) => ref.current(...args)) as T, [ref]);
}
```

The React ESLint plugin's `exhaustive-deps` rule will flag intentional omissions. A
disable comment with a one-line reason is the correct response; do not delete the rule
from the config.

## Memoizing context

A context value created inline re-renders every consumer on every provider render. Memo
the value object. Then consider splitting contexts by update rate: a `ThemeContext` that
changes on demand and a `SocketContext` that changes every second should not be the
same provider, and heavy consumers of one should not subscribe to the other.

When a selector-based store is a better fit, see `state-management.md`.

## Common failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| `useMemo` recomputes every render | A dependency is recreated inline, or `deps` omits something used | Move object construction into the memo, or fix the deps |
| Memoized child still re-renders on unrelated parent state | A prop is an object, array, or function literal | Stabilize the prop or drop `memo` |
| UI stops updating after adding a custom comparator | Comparator ignores a prop the component renders | Add the prop to the comparison |
| `useCallback` wrapper causes stale reads | Callback reads state not in the deps | Add the dependency, or use the ref pattern above |
| Memory grows after memoizing large objects | Cached values retained until deps change | Check whether the memo is actually needed |

## Verify before relying on this

Automatic memoization via React Compiler changes the calculus for `useMemo` and
`useCallback`. Its availability, defaults, and the lint rules it adds vary by React
version and build setup. Check the current documentation at
https://react.dev/reference/react/compiler before hand-writing memoization that a
compiler could handle, and measure either way.
