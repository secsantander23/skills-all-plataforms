# Composable patterns and testing

Read `../SKILL.md` first. This file covers composable conventions that survive a
codebase, `effectScope` for non-component usage, and how to test composables.

## Composable signature conventions

| Situation | Signature | Why |
| --- | --- | --- |
| Caller always has a value ready | `(options: Options)` | Simplest, easiest to read |
| Caller may not have the value yet | `(source: Ref<T> | null, ...)` | Compose onto the caller's ref so the watcher reacts to changes |
| Optional dependency | `(source: Ref<T | null>, ...)` | `null` disables the behavior; no `enabled` boolean needed |
| Callback needed | `(fn: () => void, ...)` | Keep the callback out of the reactive graph |

An `enabled` ref is usually a smell: a nullable source expresses the same thing and
keeps one code path.

## Controlling lifetime

Effects created in `setup()` are owned by the component and torn down automatically.
Effects created later (in an event handler, in a module, in a callback) are not. Use
`effectScope()` for library code that must work both ways:

```ts
import { effectScope, onScopeDispose, ref, watch } from 'vue';

export function createScheduler() {
  const scope = effectScope(true); // detached: not tied to the current component
  const paused = ref(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  scope.run(() => {
    watch(paused, (isPaused) => {
      clearTimeout(timer);
      if (isPaused) return;
      timer = setInterval(() => {
        void flush();
      }, 5_000);
    });
  });

  onScopeDispose(() => {
    clearTimeout(timer);
  });

  return {
    paused,
    dispose: () => scope.stop(),
  };
}

declare function flush(): Promise<void>;
```

Rule: inside a composable, pair every `watch` with `onScopeDispose` cleanup if it
allocates anything, or ensure the watcher itself is disposed with the scope.

## Return shape

- Return named functions for anything a consumer is expected to call.
- Return `readonly()` wrappers for state that consumers should not write.
- Return a `stop()` when the composable creates listeners, observers, or timers.
- Do not return both a value and a setter for the same thing; use `defineModel` in
  components and an explicit `setX` in composables.

## Testing composables

Composable logic is plain code. Test it by calling it directly with
`effectScope`, which lets you assert on state and then stop everything deterministically.

```ts
import { effectScope, nextTick } from 'vue';
import { describe, expect, it, vi } from 'vitest';
import { useCounter } from '../src/composables/useCounter';

describe('useCounter', () => {
  it('increments and reflects a ref source', () => {
    const scope = effectScope();
    scope.run(() => {
      const { total, increment } = useCounter(() => 10);
      expect(total.value).toBe(10);
      increment();
      expect(total.value).toBe(11);
    });
    scope.stop();
  });

  it('reads the latest source after it changes', async () => {
    const source = { value: 1 };
    const scope = effectScope();
    scope.run(() => {
      const { total } = useCounter(() => source.value);
      source.value = 5;
      expect(total.value).toBe(6); // computed is lazy but re-evaluates on read
    });
    await nextTick();
    scope.stop();
  });

  it('stops timers on dispose', () => {
    vi.useFakeTimers();
    const scope = effectScope();
    const spy = vi.fn();
    scope.run(() => {
      // composable under test
      void spy;
    });
    scope.stop();
    vi.advanceTimersByTime(60_000);
    expect(spy).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
```

Test component integrations with `@vue/test-utils`: `mount` a component, drive it
through `trigger` or by setting props, and `await nextTick()` before asserting on
rendered output. Check the current `@vue/test-utils` documentation for the exact
import path, since it has moved between major versions.

## SSR considerations

- A composable that touches `window`, `document`, or `localStorage` at setup time breaks
  during server rendering. Guard with `import.meta.env.SSR` or check
  `typeof window !== 'undefined'`, or move the browser-only work into `onMounted`.
- State shared at module scope is shared across requests during SSR and leaks data
  between users. Module-level state in a composable is a server-rendering bug, not just
  a testing inconvenience.
- Use `useId()` or `useTemplateRef()`-style helpers rather than your own counter for
  ids, if your Vue version provides them, so ids stay unique across requests.

## Performance notes

- `computed` is lazy and cached. A computed read in a template re-evaluates only when
  a tracked dependency changed.
- A `watch` with `deep: true` traverses the entire value on every change. Narrow the
  source before widening the traversal.
- Avoid creating many watchers in a loop. One watcher over an array of ids with a
  `for` loop inside the callback is cheaper than one watcher per id.
- `shallowRef` plus explicit assignment is often faster than deep `reactive` for large
  collections, at the cost of losing per-field tracking.
- Measure in Vue DevTools' performance panel or the browser profiler before
  restructuring. Template refs in a large list can be more expensive than the render
  itself.

## Verify before relying on this

`useTemplateRef`, `useId`, `defineModel`, and reactive props destructuring all
stabilized in recent Vue 3 releases, and their behavior with older compilers differs.
Check the Vue release notes and https://vuejs.org/api/ for your pinned version before
building on them.
