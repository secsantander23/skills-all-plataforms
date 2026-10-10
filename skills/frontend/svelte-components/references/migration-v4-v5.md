# Svelte 4 to Svelte 5 migration reference

Read `../SKILL.md` first. This file is the detailed mapping table and the failure modes
you hit during migration.

## Syntax mapping

| Svelte 4 | Svelte 5 |
| --- | --- |
| `export let value` | `let { value } = $props()` |
| `export let value = 1` | `let { value = 1 } = $props()` |
| `export let a, b` | `let { a, b } = $props()` |
| `$$props`, `$$restProps` | `let props = $props()` then destructure `props` |
| `$: x = a + b` | `const x = $derived(a + b)` |
| `$: if (x) { ... }` | `$effect(() => { ... })` |
| `$: { statement; }` | `$effect(() => { statement; })` |
| Reactive function `$: f = () => ...` | Plain `const f = () => ...` |
| `on:click={fn}` | `onclick={fn}` |
| `on:click|preventDefault={fn}` | `onsubmit={(e) => { e.preventDefault(); fn(e); }}` |
| `on:click|once={fn}` | Call a wrapper that guards, or use an action |
| `on:input\|debounce` | Debounce in your handler or with an action |
| `class:active={x}` | `class:active={x}` (unchanged) |
| `style:color={c}` | `style:color={c}` (unchanged) |
| `bind:value` | `bind:value` (unchanged) |
| `bind:this` | `bind:this` (unchanged) |
| `bind:clientWidth` | `bind:clientWidth` (unchanged) |
| `<slot />` | `{@render children?.()}` |
| `<slot prop={x} />` | `{@render children?.(x)}` with `Snippet<[typeof x]>` |
| `<slot name="head" />` | Named snippet `{#snippet head()}` and `{@render head()}` |
| `let:item` | Snippet parameters |
| `slot="item" on:slotchange` | Snippets; there is no slot lifecycle event |
| `createEventDispatcher()` | Callback props |
| `<svelte:component this={C} />` | `<C />` with `{#if}` for dynamic components |
| `<svelte:self />` | Import the component by name |
| `<svelte:window on:keydown />` | `<svelte:window onkeydown={fn} />` |
| `new App({ target })` | `mount(App, { target })` |
| `$app/stores` | `$app/state` in current SvelteKit (legacy path still present) |

## Prose step by step

1. **Update tooling first.** Run your package manager's update for Svelte and the
   SvelteKit packages together, then run the project's own check script. Fix what the
   compiler reports before making manual changes.
2. **Convert props.** Mechanical: `export let` becomes destructured `$props()` with the
   same defaults. Do this first because it is the highest count and the lowest risk.
3. **Convert reactive statements.** Classify each `$:` line:
   - A pure assignment or expression becomes `$derived`.
   - Statements with side effects (assignments, function calls) become `$effect`.
   - A reactive statement that only calls a function with no arguments is usually a bug
     in the original; delete it.
4. **Convert events.** `on:x` to `x` attributes. Modifiers become explicit calls:
   `preventDefault()`, `stopPropagation()`. For `once`, write a wrapper.
5. **Convert slots to snippets.** Default slot becomes `children`. Named slots become
   named snippets. `let:` becomes a snippet parameter.
6. **Convert `createEventDispatcher` to callback props.** Define a typed props object
   with the callbacks, and have the parent pass functions.
7. **Migrate shared state last.** Only now move stores to runes or a `.svelte.ts`
   module, so the diff stays reviewable.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Cannot use export let in runes mode` | The file opted into runes but still declares props with `export let` | Convert to `$props()` |
| Binding does nothing | Component is props-based and the target prop is not `$bindable()` | Add `$bindable()` or use a callback |
| Snippet prop is `undefined` at render | The parent passed a non-snippet, or passed nothing | Guard with `{#if}` or supply a default snippet in destructuring |
| Effect runs but the DOM is stale | The effect reads a value asynchronously, outside tracking | Move the read before the `await`, or drive it from state |
| Infinite effect loop | The effect writes state that it also reads | Derive instead |
| State shared between components is not shared | Two components each declare their own `$state` | Move it to a store or a `.svelte.ts` module |
| `state is not defined` in a module | Runes used in a plain `.ts` file | Rename to `.svelte.ts` |
| Component no longer updates after adding `$derived` | The derived value is mutated somewhere | Assign to the derived variable instead of mutating it, if supported |
| Hydration mismatch | Random values or `Date.now()` during render | Compute in an effect or a `load` function |

## Component instantiation

Svelte 4 mounted a component imperatively:

```js
// Svelte 4
const app = new App({ target: document.getElementById('app'), props: { name: 'x' } });
app.$destroy();
```

Svelte 5 uses functions:

```js
import { mount, unmount } from 'svelte';
import App from './App.svelte';

const target = document.getElementById('app');
const app = mount(App, { target, props: { name: 'x' } });
unmount(app);
```

Server rendering uses `render` from `svelte/server` and hydration uses `hydrate` from
`svelte`. In practice you should use the framework (SvelteKit, or a Vite plugin) rather
than wiring this yourself.

## Incremental migration advice

- Svelte 5 compiles legacy components, so a partial migration is a valid intermediate
  state. Keep it short; a component in legacy mode cannot use runes.
- Migrate by feature directory rather than file-by-file across the whole tree. A
  directory is either legacy or runes mode, and code moves together.
- Run your tests after each step. The compiler catches syntax-level changes; tests
  catch the event-modifier and slot conversions, which are where behavior changes
  silently.
- Do not run an automated codemod and a manual rewrite at once. One mechanism at a time
  keeps the diff reviewable.

## References

- Official migration guide: https://svelte.dev/docs/svelte/v5-migration-guide
- Svelte 5 announcement and overview: https://svelte.dev/blog
- Runes: https://svelte.dev/docs/svelte/$state
- Snippets: https://svelte.dev/docs/svelte/snippet
- Legacy lifecycle docs (for comparison): https://svelte.dev/docs/svelte/legacy-lifecycle
- `svelte-check`: https://svelte.dev/docs/cli/svelte-check
