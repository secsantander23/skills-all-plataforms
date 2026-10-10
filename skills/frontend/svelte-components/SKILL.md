---
name: Svelte Components and Runes
description: Covers Svelte 5 runes including state, derived, effect, props and bindable, component composition with snippets, stores, SvelteKit routing and load functions, form actions, and migrating from Svelte 4 syntax. Use when the user asks about writing Svelte components, reactivity in SvelteKit, passing data between components, routing and data loading, or upgrading a Svelte 4 codebase.
metadata:
  opencode/autoinvoke: true
---

# Svelte Components and Runes

Svelte compiles your component into a small imperative update function. Svelte 5's
runes mode makes that model explicit: you declare **what is state** with `$state`,
**what is derived** with `$derived`, and **what has side effects** with `$effect`.
Everything else is a normal assignment the compiler can track.

The shift from Svelte 4 matters because the two models have different mental models:
Svelte 4 used `let` plus `$:` labels and two-way binding by convention; Svelte 5 uses
runes, explicit `$props()`, and event attributes instead of event directives. A codebase
can mix the two during a migration, and that is where most bugs come from.

## When to use this

- Writing or reviewing a Svelte component with runes.
- Deciding what should be `$state`, what should be `$derived`, and what belongs in an
  `$effect`.
- Passing data and callbacks between components with `$props`.
- SvelteKit routing, `load` functions, streaming, and form actions.
- Migrating a Svelte 4 project (`export let`, `$:`, `on:click`, `createEventDispatcher`,
  slots).

## Quick start

```svelte
<!-- src/lib/components/TaskList.svelte -->
<script lang="ts">
  import { untrack } from 'svelte';

  interface Task {
    id: string;
    title: string;
    done: boolean;
  }

  let {
    tasks = [],
    onToggle,
    readOnly = false,
  }: {
    tasks?: Task[];
    onToggle?: (id: string) => void;
    readOnly?: boolean;
  } = $props();

  let query = $state('');

  // Derived: recomputed only when query or tasks changes.
  const visible = $derived(
    tasks.filter((t) => t.title.toLowerCase().includes(query.trim().toLowerCase())),
  );

  const remaining = $derived(tasks.filter((t) => !t.done).length);

  function handleInput(event: Event): void {
    query = (event.currentTarget as HTMLInputElement).value;
  }
</script>

<section>
  <label>
    Filter
    <input {query} oninput={handleInput} />
  </label>
  <p>{remaining} of {tasks.length} remaining</p>

  <ul>
    {#each visible as task (task.id)}
      <li>
        <label>
          <input
            type="checkbox"
            checked={task.done}
            disabled={readOnly}
            onchange={() => untrack(() => onToggle?.(task.id))}
          />
          <span class:done={task.done}>{task.title}</span>
        </label>
      </li>
    {:else}
      <li>No tasks match.</li>
    {/each}
  </ul>
</section>

<style>
  .done {
    text-decoration: line-through;
  }
</style>
```

Two things to notice:

- `{#each visible as task (task.id)}` uses a **keyed** each block. That is the Svelte
  equivalent of a React list key, and you want it whenever the list reorders, filters,
  or deletes.
- `untrack` is used so reading reactive state inside a callback does not accidentally
  create a dependency. You need it only in effect-like contexts; in ordinary event
  handlers it is not required. Verify its availability in your Svelte version before
  relying on it broadly.

## Runes

| Rune | Purpose | Where it is valid |
| --- | --- | --- |
| `$state(value)` | Reactive mutable state | Top level of a component, or inside `.svelte.ts`/`.svelte.js` modules |
| `$state.raw(value)` | State that is not proxied deeply | Same |
| `$derived(expr)` | Value computed from state | Top level only |
| `$derived.by(fn)` | Computed value from a function body with statements | Top level only |
| `$effect(fn)` | Side effects after the DOM updates | Top level only, never in `if` |
| `$effect.pre(fn)` | Side effects before the DOM updates | Top level only |
| `$props()` | Declare the component's props | Exactly once, top level |
| `$bindable()` | Mark a prop as supporting two-way binding | Inside `$props()` destructuring |
| `$inspect(...).with(fn)` | Log value changes in development | Top level |

`$state` is deeply reactive through a Proxy, like Vue's `reactive`. Assigning to a
property triggers updates. Reassigning the whole variable also triggers updates.

```svelte
<script lang="ts">
  let count = $state(0);
  let items = $state<string[]>([]);

  // Derived values
  const doubled = $derived(count * 2);
  const summary = $derived.by(() => {
    const unique = new Set(items);
    return `${unique.size} unique of ${items.length}`;
  });

  // Effects run only in the browser, after the DOM is updated.
  $effect(() => {
    document.title = `Count: ${count}`;
  });

  // Return a cleanup; Svelte calls it before re-running and on destroy.
  $effect(() => {
    const id = setInterval(() => {
      count += 1;
    }, 1000);
    return () => clearInterval(id);
  });
</script>

<button onclick={() => (count += 1)}>{count} / {doubled}</button>
<p>{summary}</p>
```

Rules that catch people:

- **`$derived` cannot have side effects.** It must be pure. If you need to log or
  fetch inside a computed value, move it to `$effect` and store the result in
  `$state`.
- **`$derived` can be reassigned** (as of recent Svelte releases) which makes it act as
  an overrideable derived value. Do not rely on this unless you have verified support in
  your version; the stable pattern is to keep derived values read-only.
- **An `$effect` that writes state it also reads loops forever.** Example: reading
  `filtered` and then writing `filtered` inside the same effect. Compute instead.
- **`$effect` does not run during server rendering.** Data fetching belongs in a
  SvelteKit `load` function, not in an effect.
- **`$effect` must be called during component initialization.** Calling it from an event
  handler or inside an `if` block in the script does not create an effect.

### Shared reactive state in modules

State cannot live in a plain `.ts` module, because the compiler does not see it. Put it
in a `.svelte.ts` or `.svelte.js` file and the runes work there too.

```ts
// src/lib/state/cart.svelte.ts
import type { CartLine } from './types';

class Cart {
  lines = $state<CartLine[]>([]);

  get total(): number {
    return this.lines.reduce((sum, l) => sum + l.price * l.qty, 0);
  }

  add(line: CartLine): void {
    const existing = this.lines.find((l) => l.id === line.id);
    if (existing) existing.qty += line.qty;
    else this.lines.push({ ...line });
  }

  clear(): void {
    this.lines = [];
  }
}

export const cart = new Cart();
```

Class fields with `$state` work in `.svelte.ts`. Getters are read inside the class body,
so `total` stays reactive for consumers.

## Props and bindings

```svelte
<!-- Modal.svelte -->
<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    open = false,
    title,
    onclose,
    children,
    footer,
  }: {
    open?: boolean;
    title: string;
    onclose?: () => void;
    children?: Snippet;
    footer?: Snippet;
  } = $props();
</script>

{#if open}
  <div role="dialog" aria-modal="true" aria-label={title}>
    <header>
      <h2>{title}</h2>
      <button type="button" aria-label="Close" onclick={() => onclose?.()}>x</button>
    </header>
    {@render children?.()}
    {#if footer}
      <footer>{@render footer()}</footer>
    {/if}
  </div>
{/if}
```

```svelte
<script lang="ts">
  import Modal from './Modal.svelte';

  let isOpen = $state(false);
  let name = $state('');
</script>

<Modal bind:open={isOpen} title="Rename" onclose={() => (isOpen = false)}>
  {#snippet footer()}
    <button type="button" onclick={() => (isOpen = false)}>Done</button>
  {/snippet}

  <label>
    Name
    <input bind:value={name} />
  </label>
</Modal>
```

Binding rules:

- `bind:value` works on inputs, textareas, selects, checkboxes, and contenteditable.
- `bind:this={el}` gives you the DOM element or the component instance.
- **A component can only bind to a prop declared with `$bindable()`.** Without it,
  `bind:open` silently does nothing, which is the most common Svelte 5 binding bug.
- For a boolean, `bind:open` is shorthand for `bind:open={open}` and only binds when
  the prop is assignable.

## Snippets instead of slots

Snippets replace slots. They are typed with the `Snippet` type from `svelte` and take
parameters.

```svelte
<!-- Table.svelte -->
<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    rows,
    head,
    row,
    empty,
  }: {
    rows: { name: string; count: number }[];
    head: Snippet;
    row: Snippet<[string, number]>;
    empty?: Snippet;
  } = $props();
</script>

<table>
  <thead><tr>{@render head()}</tr></thead>
  <tbody>
    {#each rows as [name, count] (name)}
      <tr>{@render row(name, count)}</tr>
    {:else}
      {#if empty}
        <tr><td colspan="2">{@render empty()}</td></tr>
      {/if}
    {/each}
  </tbody>
</table>
```

```svelte
<!-- Usage -->
<script lang="ts">
  import Table from './Table.svelte';

  const rows = [
    { name: 'api', count: 12 },
    { name: 'web', count: 30 },
  ];
</script>

<Table {rows}>
  {#snippet head()}
    <th scope="col">Name</th>
    <th scope="col">Count</th>
  {/snippet}
  {#snippet row(name, count)}
    <td>{name}</td>
    <td>{count}</td>
  {/snippet}
  {#snippet empty()}
    Nothing to show
  {/snippet}
</Table>
```

A snippet is declared with `{#snippet name(params)}` and rendered with
`{@render name(params)}`. Named snippets in a component body become props. The
`children` snippet is the direct replacement for the default `<slot />`.

`{@render}` requires a value that is definitely a snippet. If a snippet prop might be
missing, either guard with `{#if}` or pass a fallback, as above.

## Stores

Stores predate runes and still work everywhere, including in SvelteKit `load` results
and shared modules. They are still the right tool for state that must be shared across
many components or written outside a component.

```ts
// src/lib/stores/preferences.ts
import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';

export type Theme = 'light' | 'dark';

function persistedTheme() {
  let initial: Theme = 'light';
  if (browser) {
    const stored = window.localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') initial = stored;
  }
  const store = writable<Theme>(initial);

  if (browser) {
    store.subscribe((value) => window.localStorage.setItem('theme', value));
  }
  return store;
}

export const theme = persistedTheme();

export const isDark = derived(theme, (t) => t === 'dark');

export function toggleTheme(): void {
  theme.update((t) => (t === 'light' ? 'dark' : 'light'));
}

// get() reads a value outside a component.
export function currentTheme(): Theme {
  return get(theme);
}
```

Auto-subscription works with the `$store` prefix in `.svelte` files: `$theme` reads and
writes the store. That prefix only exists in component files, not in `.ts` modules,
which is why `get(store)` and `store.subscribe` exist.

Choosing between stores and runes state:

| Situation | Use |
| --- | --- |
| State owned by one component | `$state` |
| State shared by many components, written anywhere | Store, or a class in a `.svelte.ts` module |
| State derived from another source | `$derived` in the component, or `derived` for cross-component |
| State that must be read outside any component | Store (`get`) or a `.svelte.ts` module |

## SvelteKit routing and data loading

A route is a directory under `src/routes` named with `+`-prefixed files.

| File | Role |
| --- | --- |
| `+page.svelte` | The page component |
| `+page.js` / `+page.ts` | Universal `load`; runs on server and client |
| `+page.server.js` / `+page.server.ts` | Server-only `load`; can read cookies, headers, and the database |
| `+layout.svelte` | Persistent shell around nested pages |
| `+layout.js` / `+layout.server.js` | Layout `load`; data merges from parents to children |
| `+error.svelte` | Error page for that segment |
| `+server.js` | A route endpoint that returns a `Response` |

```ts
// src/routes/projects/[id]/+page.server.ts
import { error, redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, depends }) => {
  const user = locals.user;
  if (!user) redirect(303, '/login');

  depends('app:projects');

  const project = await getProject(params.id, user.id);
  if (!project) error(404, 'Project not found');

  return { project };
};

declare function getProject(id: string, userId: string): Promise<{ id: string; name: string } | null>;
```

```svelte
<!-- src/routes/projects/[id]/+page.svelte -->
<script lang="ts">
  import type { PageProps } from './$types';

  let { data, children }: PageProps = $props();
</script>

<h1>{data.project.name}</h1>
{@render children?.()}
```

Load rules that trip people up:

- **`load` runs on the server for the first request and again on the client for some
  navigations.** It must not depend on browser-only APIs unless you guard them. Use a
  universal `load` for data that does not need server context; use a server `load` for
  anything requiring cookies, secrets, or private data.
- **Layout data is available to child pages; page data is not available to layouts.**
  If a layout needs page data, fetch it in the layout.
- **A `load` that fails throws, it does not return.** Use `error(status, message)` from
  `@sveltejs/kit`.
- **Sibling params are not available.** `+page.server.ts` for `[id]` sees `params.id`.
- **Never return a class instance or a function from `load`.** Return serializable
  data; SvelteKit serializes it.

### Form actions

```ts
// src/routes/projects/[id]/+page.server.ts
import { fail } from '@sveltejs/kit';
import type { Actions } from './$types';

export const actions = {
  default: async ({ request, params }) => {
    const form = await request.formData();
    const name = String(form.get('name') ?? '').trim();

    if (name.length < 3) {
      return fail(400, { name, error: 'Name must be at least 3 characters' });
    }
    if (name.length > 80) {
      return fail(400, { name, error: 'Name must be 80 characters or fewer' });
    }

    await updateProject(params.id, name);
    return { success: true };
  },
} satisfies Actions;

declare function updateProject(id: string, name: string): Promise<void>;
```

```svelte
<script lang="ts">
  import { enhance } from '$app/forms';
  import type { ActionData, PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let submitting = $state(false);
</script>

<form
  method="POST"
  use:enhance={() => {
    submitting = true;
    return async ({ update }) => {
      submitting = false;
      await update();
    };
  }}
>
  <label>
    Project name
    <input name="name" value={form?.name ?? data.project.name} />
  </label>
  {#if form?.error}
    <p role="alert">{form.error}</p>
  {/if}
  <button type="submit" disabled={submitting}>
    {submitting ? 'Saving' : 'Save'}
  </button>
</form>
```

`fail()` returns the field values alongside the error so the form can be re-rendered
with what the user typed. `use:enhance` makes submission happen without a full page
reload while keeping the real `<form method="POST">` as the no-JavaScript fallback.
Import `enhance` from `$app/forms`. Return data from SvelteKit `load` and read it via
`$app/state` in newer projects; `$app/stores` still exists but is the legacy path.
Check which your installed SvelteKit version documents before using either.

## Svelte 4 to Svelte 5 migration

| Svelte 4 | Svelte 5 |
| --- | --- |
| `export let value` | `let { value = default } = $props()` |
| `export let value = 1` | `let { value = 1 } = $props()` |
| `$: doubled = count * 2` | `const doubled = $derived(count * 2)` |
| `$: { doThing() }` for side effects | `$effect(() => { doThing() })` |
| `on:click={fn}` | `onclick={fn}` |
| `on:submit\|preventDefault={fn}` | `onsubmit` plus `event.preventDefault()` in the handler |
| `<slot />` and `let:item` | `{@render children?.()}` and snippet parameters |
| `createEventDispatcher` | Callback props |
| `bind:this` | `bind:this` (unchanged) |
| `$:` reading a function's result | Compute in the expression, or use `$derived.by` |
| Component instantiating another | Direct import and render; no `new Component({ target })` |

```svelte
<!-- Before (Svelte 4) -->
<script>
  export let items = [];
  export let onRemove = () => {};
  $: total = items.length;
</script>

{#each items as item}
  <button on:click={() => onRemove(item.id)}>{item.title}</button>
{/each}
<p>{total}</p>
```

```svelte
<!-- After (Svelte 5) -->
<script>
  let { items = [], onRemove = () => {} } = $props();
  const total = $derived(items.length);
</script>

{#each items as item (item.id)}
  <button onclick={() => onRemove(item.id)}>{item.title}</button>
{/each}
<p>{total}</p>
```

Migration order that works:

1. Run the official migration tooling, or upgrade one component at a time. Svelte 5
   supports legacy components, so a mixed codebase compiles.
2. Convert `export let` to `$props()`, including `export let x = 1` to
   `{ x = 1 }`.
3. Convert `$:` statements. A reactive statement with statements in its body becomes
   `$effect`; a pure assignment becomes `$derived`.
4. Convert `on:x` to `x` attributes and event modifiers to explicit `preventDefault()`,
   `stopPropagation()`, or the `capture` option.
5. Convert slots to snippets and `createEventDispatcher` to callback props.
6. Only then move shared state to runes, and only then consider dropping stores.

The official migration guide at `https://svelte.dev/docs/svelte/v5-migration-guide`
lists the full mapping and the automatic codemod. Use it rather than working from memory.

## Checklist

- Every `$state` variable is genuinely mutable state, not a computed value.
- `$derived` is pure and reads all its dependencies.
- `$effect` is top-level, returns cleanup, and does not write state it reads.
- Effects do not fetch data; `load` functions fetch data.
- Each block over changing data is keyed.
- Callback props are used instead of `createEventDispatcher`.
- Two-way binding targets props declared with `$bindable()`.
- Server-only code lives in `*.server.ts` files or in `+page.server.ts`.
- `load` returns serializable data and redirects with `redirect()`, not by returning.

## Common mistakes

- **`bind:` to a prop that is not `$bindable`.** Symptom: the child's value updates
  visually but the parent's variable never changes. Fix: declare it with
  `$bindable()`, or use a callback prop instead of two-way binding.
- **`$effect` that writes state it reads.** Symptom: an infinite update loop and a
  stack of repeated work in the profiler. Fix: derive the value instead.
- **`$effect` used for data fetching.** Symptom: a flash of empty UI, duplicate
  requests on navigation, and no server-side data. Fix: use a `load` function.
- **`$derived` with a side effect inside.** Symptom: the effect runs at an unpredictable
  time and may not run at all if nothing reads the value. Fix: move it into
  `$effect`.
- **Unkeyed `{#each}` over a mutating list.** Symptom: DOM nodes are reused for the
  wrong records and input state follows the index. Fix: add `(key)`.
- **Runes in a plain `.ts` module.** Symptom: a compile error saying `$state` is not
  defined. Fix: rename to `.svelte.ts`.
- **`$:` next to `$state` in the same component.** Symptom: duplicate updates and
  confusing behavior during migration. Fix: pick one model per component.
- **Passing a class instance from `load` to a component.** Symptom: a serialization
  error at request time. Fix: return plain data.
- **Reading `params` without checking for `undefined`.** Symptom: a runtime error on
  a route that matched a fallback. Fix: treat params as optional and handle the miss.

## Anti-patterns

- **`$effect` as a general-purpose reactive statement.** `$derived` is the right tool
  for almost everything you would reach for `$:` to do. Reserve `$effect` for the
  outside world: the DOM, timers, subscriptions, analytics, storage.
- **Every piece of state in a global store.** Runes with a plain prop passed down are
  clearer and free to test.
- **A store and runes state for the same data.** Symptom: two sources of truth that
  drift. Pick one per fact.
- **Copy-pasting `$props()` types instead of exporting a `Props` interface.** Export the
  props type so consumers get it, and use `satisfies` or an explicit type parameter.
- **Deeply nested prop drilling through many levels.** Introduce a snippet or move
  state up; do not build a store to avoid passing two props.
- **`{#each}` without a key "because the list never reorders".** It reorders as soon as
  someone adds a sort, and the bug appears in review rather than in your test.
- **Hand-writing `createEventDispatcher` replacements for the whole codebase before
  converting props.** Convert `export let` first; the callback props follow naturally.
- **Relying on undocumented internal compiler behavior.** If you need to know whether
  something is supported, read the Svelte documentation for your installed version.

## References

- Svelte documentation: https://svelte.dev/docs/svelte
- Runes reference: https://svelte.dev/docs/svelte/$state
- `$derived`: https://svelte.dev/docs/svelte/$derived
- `$effect`: https://svelte.dev/docs/svelte/$effect
- `$props`: https://svelte.dev/docs/svelte/$props
- Snippets: https://svelte.dev/docs/svelte/snippet
- Stores: https://svelte.dev/docs/svelte/store
- SvelteKit routing and `load`: https://svelte.dev/docs/kit/load
- SvelteKit form actions: https://svelte.dev/docs/kit/form-actions
- SvelteKit page and layout options: https://svelte.dev/docs/kit/page-options
- `$app/state`: https://svelte.dev/docs/kit/$app-state
- Svelte 4 to 5 migration guide: https://svelte.dev/docs/svelte/v5-migration-guide
- Svelte compiler and CLI: https://svelte.dev/docs/cli
- Svelte REPL: https://svelte.dev/playground

**Verify before relying on this:** runes and snippets are the stable Svelte 5 model, but
individual helpers (`$props.id()`, `$host()`, `untrack`, `$state.snapshot`, and
reassignment of `$derived`) have changed or stabilized at different points. Check the
API reference for your installed Svelte version before depending on the less common
runes.
