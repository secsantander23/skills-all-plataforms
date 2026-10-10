# Svelte reactivity model reference

Read `../SKILL.md` first. This file covers the reactivity semantics in detail: what
each rune tracks, when effects run, and the boundary between runes and legacy Svelte 4
behavior.

## What is tracked and when

| Rune | Tracked on | Runs/updates |
| --- | --- | --- |
| `$state` | Any mutation of a proxied property, or reassignment of the variable | Re-runs the effects that read it |
| `$state.raw` | Only reassignment of the variable | Whole-value replacement triggers updates |
| `$derived` | Every reactive read that happens during its evaluation | Lazily, when read, invalidated by a dependency change |
| `$effect` | Every reactive read in the callback body | After the DOM updates, batched |
| `$effect.pre` | Same | Before the DOM updates |

Key consequences:

- **Reads inside a function only count if that function is called during the tracking
  scope.** A helper that reads state after an `await` is not tracked, because the
  tracking scope has already ended. Use `$effect` for anything asynchronous.
- **`$derived` is lazy.** If nothing reads it, it never evaluates. Never rely on a
  derived value for its side effects.
- **Effects are batched.** Several state changes in one tick produce one effect run.
  Reads inside the effect see the final values, not the intermediate ones.

## Deep reactivity and proxies

`$state` objects and arrays are proxied deeply. That means:

- `state.items.push(x)` is reactive.
- `state.nested.deep.value = 1` is reactive.
- Storing a DOM node, a class instance you do not want proxied, or an external library
  object will be proxied too. Use `$state.raw` for values you replace wholesale, or
  hold such objects outside `$state`.

`$state.snapshot(value)` produces a plain, deeply-cloned, non-reactive copy. Use it
when handing reactive state to a library that stores or serializes it.

## When effects run, and ordering

- `$effect.pre` runs before the DOM is updated; use it to measure or to read layout
  that the update is about to change.
- `$effect` runs after the DOM is updated but before the browser paints.
- Effect cleanup runs before the next run and on component destroy. Returning a
  function from an effect is the only supported teardown.

```svelte
<script>
  let width = $state(0);
  let el = $state<HTMLDivElement | null>(null);

  $effect.pre(() => {
    // Read layout before Svelte's own updates are flushed.
    console.log('pre', el?.offsetWidth, width);
  });

  $effect(() => {
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
    });
    observer.observe(el);
    return () => observer.disconnect();
  });
</script>

<div bind:this={el}>{width}</div>
```

Note the dependency boundary: the observer effect reads `el` only, so the callback
assignment to `width` does not re-trigger the observer.

## When to use each primitive

| You are doing | Use |
| --- | --- |
| Deriving a value for the template | `$derived` |
| Deriving a value with statements or branches | `$derived.by` |
| Syncing with the DOM, timers, storage, or a subscription | `$effect` with cleanup |
| Handling an event | An inline handler, not an effect |
| Fetching data on navigation or on first render | A SvelteKit `load` function |
| Shared state outside components | A store, or a class in `.svelte.ts` |

## Legacy mode coexistence

Svelte 5 can compile components written for Svelte 4. In a mixed codebase:

- A component using `export let` is in legacy mode for its props. It cannot also use
  `$props()`.
- `$:` reactive statements only work in legacy-mode components.
- Event directives (`on:click`) are deprecated but still compile.
- `createEventDispatcher` still works; callback props are the forward path.
- Slot syntax still works; snippets are the forward path.

Do not mix the two models inside a single component. Pick one, convert, and let the
compiler tell you about the leftovers.

## Performance notes

- Deep `$state` proxies are not free. For large collections that you always replace
  wholesale, `$state.raw` plus reassignment is cheaper.
- Derived values are cached and recomputed only when their dependencies change. Do not
  wrap cheap derivations in an effect.
- Each keyed `{#each}` block diffs keys. Unkeyed blocks diff by index, which is correct
  only for static lists.
- `$effect` that writes state creates a render pass. Prefer deriving.
- Svelte compiles to direct DOM operations, so the usual React-style memoization
  techniques are not needed and add cost. If you find yourself adding manual caching
  in a Svelte component, you usually want a store or a derived value instead.

## Verify before relying on this

The following runes and options exist in the Svelte 5 line but have changed shape or
availability across releases: `$state.snapshot`, `$state.eager`, `$props.id()`,
`$host()`, `untrack`, `flushSync` behavior, and assigning to a `$derived` value. Check
https://svelte.dev/docs/svelte/ for the version your project pins before depending on
any of these.

For tooling, the Svelte language server and the `svelte-check` command validate `.svelte`
files. Run it in CI. Confirm the exact command against your project's `package.json`
scripts and the CLI documentation at https://svelte.dev/docs/cli.
