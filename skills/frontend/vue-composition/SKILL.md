---
name: Vue 3 Composition API
description: Covers ref, reactive, computed, watch and watchEffect, composable design, the script setup macro API, defineProps and defineEmits contracts, provide and inject with typed keys, Vue Router navigation, and reactivity pitfalls. Use when the user asks about Vue 3 setup syntax, building composables, wiring component APIs, sharing state through injection, or debugging a reactivity or watcher bug.
metadata:
  opencode/autoinvoke: true
---

# Vue 3 Composition API

The Composition API gives you plain functions and reactive objects instead of a class
hierarchy. It works because Vue wraps values in Proxies: reading a property inside a
render function or a computed registers a dependency, and writing to it triggers the
subscribers.

The mental model: **a composable is a function that owns reactive state and returns
plain values**. Components are consumers of composables. The API only works if the
reactive boundary and the reactive read stay in the same function scope, which is the
source of most Vue bugs.

## When to use this

- Writing or reviewing a `<script setup>` component.
- Extracting logic into a reusable composable.
- Deciding whether something belongs in a prop, a `ref`, a `provide`, or a store.
- Debugging "the template does not update", "the watcher never fires", or
  "watcher fires too many times".
- Setting up typed props, emits, models, slots, and provide/inject keys.

## Quick start

```vue
<!-- src/components/Counter.vue -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    label: string;
    initial?: number;
    step?: number;
    max?: number;
  }>(),
  { initial: 0, step: 1, max: 100 },
);

const emit = defineEmits<{
  change: [value: number];
  limitReached: [];
}>();

const count = ref(props.initial);

// Derived, cached, recomputed only when count or step changes.
const remaining = computed(() => props.max - count.value);
const isAtMax = computed(() => count.value >= props.max);

// Options object form when you need more control than two positional args give.
watch(count, (next, prev) => {
  emit('change', next);
  if (next > prev && isAtMax.value) {
    emit('limitReached');
  }
});

function increment(): void {
  count.value = Math.min(count.value + props.step, props.max);
}

function reset(): void {
  count.value = props.initial;
}

defineExpose({ reset });
</script>

<template>
  <div>
    <label>{{ label }}</label>
    <output>{{ count }}</output>
    <p>Remaining capacity: {{ remaining }}</p>
    <button type="button" :disabled="isAtMax" @click="increment">Increase</button>
    <button type="button" @click="reset">Reset</button>
  </div>
</template>
```

Usage, with the contract explicit at the call site:

```vue
<script setup lang="ts">
import { ref } from 'vue';
import Counter from './Counter.vue';

const lastChange = ref<number | null>(null);
const showCounter = ref(true);
</script>

<template>
  <Counter
    v-if="showCounter"
    label="Seats"
    :initial="2"
    :max="5"
    @change="lastChange = $event"
    @limit-reached="showCounter = false"
  />
  <p v-if="lastChange !== null">Last change: {{ lastChange }}</p>
</template>
```

## Reactive primitives

| API | Use for | Notes |
| --- | --- | --- |
| `ref(value)` | Anything replaceable: primitives, arrays, objects | Access with `.value` in script; auto-unwrapped in templates |
| `reactive(obj)` | An object whose fields you always mutate in place | Not usable for primitives or arrays you replace |
| `computed(fn)` | Derived, cacheable value | Lazily evaluated, cached until a dependency changes |
| `readonly(obj)` | Exposing state that consumers must not write | Wraps deep by default |
| `shallowRef(obj)` | Large objects where you replace rather than mutate | No deep tracking; much cheaper |
| `toRef(obj, key)` / `toRefs(obj)` | Per-property refs into a reactive object | Keeps reactivity through destructuring |
| `toValue(source)` | Read a ref, a getter, or a plain value uniformly | Great for composable arguments |
| `effectScope()` | Grouping effects so they can be stopped together | Used by component libraries |
| `markRaw(obj)` | Telling Vue to skip an object entirely | Last resort; breaks reactivity for that object |

```ts
import { computed, ref, toValue, type Ref } from 'vue';

// Accept a ref, a getter, or a raw value. The caller decides.
export type MaybeCount = number | Ref<number> | (() => number);

export function useCounter(source: MaybeCount, step = 1) {
  const internal = ref(0);
  const total = computed(() => toValue(source) + internal.value);

  function increment(): void {
    internal.value += step;
  }

  function addBy(n: number): void {
    internal.value += n;
  }

  return { internal, total, increment, addBy, step };
}
```

Callers can pass `10`, a `ref(10)`, or `() => selected.value`. Prefer
`MaybeRefOrGetter<T>` from `vue` as the parameter type when you want Vue's own
generic across many types.

Rule of thumb: use `ref` unless you have a specific reason for `reactive`. `ref` has
one consistent access syntax (`.value`), it works with primitives, and replacing the
whole object is cheap and predictable. `reactive` returns a Proxy and mutating it in
place means an entirely different mental model.

## `computed` versus `watch`

`computed` for derived values, `watch` for side effects.

| Situation | Use |
| --- | --- |
| Display a value derived from state | `computed` |
| Trigger an HTTP request when an id changes | `watch` |
| Write to localStorage on change | `watch` (or `watchEffect`) |
| Reset a form when a prop changes | `watch` |
| Show/hide a DOM element | A `v-if` on a computed boolean, not a watcher |

```ts
import { ref, watch } from 'vue';

export function useFetch<T>(url: Ref<string>) {
  const data = ref<T | null>(null);
  const error = ref<Error | null>(null);
  const loading = ref(false);
  let controller: AbortController | null = null;

  watch(
    url,
    async (next, _prev, onCleanup) => {
      controller?.abort();
      const local = new AbortController();
      controller = local;
      loading.value = true;
      error.value = null;
      onCleanup(() => local.abort());
      try {
        const res = await fetch(next, { signal: local.signal });
        if (!res.ok) throw new Error(`Request failed with status ${res.status}`);
        data.value = (await res.json()) as T;
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          error.value = e as Error;
        }
      } finally {
        if (controller === local) loading.value = false;
      }
    },
    { immediate: true },
  );

  return { data, error, loading };
}
```

`onCleanup` (the third watcher argument) is the supported way to cancel or tear down
before the next run. Abort the previous request when the source changes, or you get
race conditions where a slow earlier response overwrites a newer one.

Watches are **not** immediate by default. `{ immediate: true }` runs once on setup.
`deep: true` is needed to watch mutations inside a nested object or array, and it is
expensive because Vue traverses the value. Prefer watching a narrow getter:

```ts
watch(
  () => form.address.city, // narrow: tracks one string
  onCityChange,
);

// Instead of deep: true on the whole object
watch(form, onAnyChange, { deep: true });
```

`watchEffect` runs immediately and auto-tracks everything it reads. Use it when the
dependencies are an implementation detail:

```ts
watchEffect((onCleanup) => {
  const id = setInterval(() => void poll(), 5_000);
  onCleanup(() => clearInterval(id));
});
```

## Composable design

A composable is a function that starts with `use`, sets up reactive state, and returns
it. It should be usable from another composable.

Good composables are:

- **Stateless where possible.** Accept inputs, return outputs. Ambient state makes
  testing and reuse hard.
- **Tolerant of ref and raw inputs.** Accept `Ref<T> | T` and normalize with `toValue`,
  or accept a getter.
- **Self-cleaning.** Any `watch`, timer, or listener is scoped to the calling component
  when called from `setup`, so it is disposed automatically.
- **Read-only at the boundary** where consumers should not write.

```ts
// src/composables/usePagination.ts
import { computed, ref, toValue, type ComputedRef, type MaybeRefOrGetter, type Ref } from 'vue';

export interface Pagination<T> {
  page: Ref<number>;
  pageCount: ComputedRef<number>;
  pageItems: ComputedRef<readonly T[]>;
  setPage: (n: number) => void;
  next: () => void;
  previous: () => void;
}

export function usePagination<T>(
  items: MaybeRefOrGetter<readonly T[]>,
  pageSize: MaybeRefOrGetter<number>,
): Pagination<T> {
  const page = ref<number>(1);
  const all = computed(() => toValue(items));
  const size = computed(() => Math.max(1, toValue(pageSize)));
  const pageCount = computed(() => Math.max(1, Math.ceil(all.value.length / size.value)));
  const pageItems = computed(() => {
    const start = (page.value - 1) * size.value;
    return all.value.slice(start, start + size.value);
  });

  function setPage(n: number): void {
    page.value = Math.min(Math.max(1, n), pageCount.value);
  }

  return {
    page,
    pageCount,
    pageItems,
    setPage,
    next: () => setPage(page.value + 1),
    previous: () => setPage(page.value - 1),
  };
}
```

Conventions:

- One composable per file, file named after the composable, in a `composables/`
  directory.
- Return an object with named keys rather than a long positional tuple.
- If it wraps a browser API, return a cleanup function or ensure the scope disposal
  handles it. If you must return a disposer for use outside a component, wrap the
  setup in `effectScope()`.
- Optional auto-import exists (unplugin-auto-import), but explicit imports are easier
  to grep, and required by most linters. Pick one convention per project.

## Component contracts with the `<script setup>` macros

| Macro | Purpose |
| --- | --- |
| `defineProps` | Declare props; type-only or runtime declaration |
| `withDefaults` | Supply defaults for a type-only `defineProps` |
| `defineEmits` | Declare emitted events and their payloads |
| `defineModel` | Declare a prop and matching emit for `v-model` |
| `defineSlots` | Type the slot props |
| `defineExpose` | Control what a parent ref can reach |
| `defineOptions` | Component-level options such as `name` or `inheritAttrs` |

These are compiler macros. They need no import and cannot be called at runtime.

```vue
<script setup lang="ts">
interface Row {
  id: string;
  label: string;
}

const model = defineModel<string>({ default: '' });

const { row, rows, disabled = false } = defineProps<{
  row: Row;
  rows?: Row[];
  disabled?: boolean;
}>();

const emit = defineEmits<{
  select: [row: Row];
  'row-focus': [id: string, index: number];
}>();

defineSlots<{
  actions?: (props: { row: Row }) => unknown;
  header?: () => unknown;
}>();
</script>

<template>
  <div :aria-disabled="disabled">
    <slot name="header" />
    <button type="button" :disabled="disabled" @click="emit('select', row)">
      {{ row.label }}
    </button>
    <input v-model="model" :aria-label="`Rename ${row.label}`" />
    <slot name="actions" :row="row" />
  </div>
</template>
```

Prop declaration styles:

- **Type-only declaration** is the clearest and gives full TypeScript inference in the
  template. Defaults go through `withDefaults`, or through a reactive props destructure
  default if your Vue version supports it (that feature stabilized in a recent 3.5
  release; verify against your project's Vue version matrix before relying on it).
- **Runtime declaration** supports validators and is required if props must be
  validated at runtime, for example in a library consumed by JavaScript callers:

```ts
const props = defineProps({
  size: {
    type: String as () => 'sm' | 'md' | 'lg',
    required: false,
    default: 'md',
    validator: (v: unknown) => ['sm', 'md', 'lg'].includes(v as string),
  },
  count: { type: Number, default: 0 },
});
```

Guidance:

- **Never mutate a prop.** Emit an event or use `defineModel` instead. Writing to a
  prop is a Vue warning in development and silently wrong in production.
- **Declare every emit** with its payload types. Undeclared emits fall through as
  native listeners and end up on the root element.
- **`defineExpose` is an explicit public API.** Without it, a parent using a template
  ref sees only the instance, not your `ref`s and methods.
- **Prefer `inheritAttrs: false` plus explicit `v-bind="$attrs"`** on the element you
  actually want attributes to land on, when the component has a multi-root template.

## `provide` and `inject`

Provide/inject is dependency injection for a subtree. It is stable for values that do
not change often. Always type the injection key, or TypeScript gives you `unknown`.

```ts
// src/keys.ts
import type { InjectionKey, Ref } from 'vue';

export interface ThemeContext {
  mode: Ref<'light' | 'dark'>;
  setMode: (mode: 'light' | 'dark') => void;
}

export const ThemeKey: InjectionKey<ThemeContext> = Symbol('theme');
```

```vue
<script setup lang="ts">
import { provide, ref } from 'vue';
import { ThemeKey } from '@/keys';

const mode = ref<'light' | 'dark'>('light');
provide(ThemeKey, {
  mode,
  setMode: (next) => {
    mode.value = next;
  },
});
</script>
```

```vue
<script setup lang="ts">
import { inject } from 'vue';
import { ThemeKey } from '@/keys';

const theme = inject(ThemeKey, null);
if (theme === null) {
  throw new Error('ThemeKey was not provided by an ancestor component');
}
</script>
```

Always pass a default to `inject` (`inject(ThemeKey, null)`) and handle the missing
case, so the failure is a clear message rather than a null dereference deep in a child.

To make a provided value reactive, provide a `ref`, not a plain value. Providing
`{ count: 0 }` and mutating `count` does not notify anything; the consumers captured a
snapshot. Also note that `inject` is **not** reactive to the provide call itself: a
component that calls `inject` cannot react to a provider appearing later.

## Vue Router patterns

```ts
// src/router/index.ts
import { createRouter, createWebHistory } from 'vue-router';

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: () => import('../views/HomeView.vue') },
    {
      path: '/projects/:id',
      name: 'project',
      component: () => import('../views/ProjectView.vue'),
      props: true,
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFoundView.vue') },
  ],
  scrollBehavior(to, from, savedPosition) {
    return savedPosition ?? { top: 0 };
  },
});
```

Route-level `component: () => import(...)` is the built-in code split. Pair it with
Suspense-aware loading state, or at least a fallback in your layout, so navigation does
not flash blank.

```vue
<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';

const route = useRoute();
const router = useRouter();
const draft = ref('');
const dirty = ref(false);

const projectId = ref('');
onMounted(() => {
  projectId.value = String(route.params.id);
});

watch(
  () => route.params.id,
  (id) => {
    projectId.value = String(id);
  },
);

function save(): void {
  void router.push({ name: 'project', params: { id: projectId.value } });
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  const ok = window.confirm('Discard unsaved changes?');
  return ok;
});
</script>

<template>
  <form @submit.prevent="save">
    <input v-model="draft" @input="dirty = true" />
  </form>
</template>
```

Router rules that matter:

- Route params are strings. `route.params.id` is `"42"`, not `42`. Convert at the
  boundary.
- Use named routes and `router.push({ name, params })` over hand-built path strings.
  Param encoding bugs are a common source of broken links.
- In-component guards are `onBeforeRouteLeave` and `onBeforeRouteUpdate`. Global guards
  go on the router instance. Return `false` or a route location to redirect.
- Prefer the router's data loading (route `props` with server-provided data, or a data
  layer) over fetching in `onMounted` with a `route` watcher. The watcher form
  re-renders the component on navigation instead of updating in place.

## Global state: Pinia

Use a store for state shared across routes or independent component trees.

```ts
// src/stores/session.ts
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';

export const useSessionStore = defineStore('session', () => {
  const token = ref<string | null>(null);
  const user = ref<{ id: string; name: string } | null>(null);
  const isAuthenticated = computed(() => token.value !== null);

  function signIn(nextToken: string, nextUser: { id: string; name: string }) {
    token.value = nextToken;
    user.value = nextUser;
  }

  function signOut() {
    token.value = null;
    user.value = null;
  }

  return { token, user, isAuthenticated, signIn, signOut };
});
```

```vue
<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useSessionStore } from '@/stores/session';

const session = useSessionStore();
const { user, isAuthenticated } = storeToRefs(session); // keeps reactivity
const { signOut } = session; // actions are not reactive state

session.signIn('abc', { id: '1', name: 'Sam' });
</script>
```

`storeToRefs` exists because destructuring a store directly loses reactivity on its
state properties. Destructuring actions is fine.

## Reactivity pitfalls

| Symptom | Cause | Fix |
| --- | --- | --- |
| A destructured value never updates | Destructuring reads the value once | Keep the `ref`, or use `toRefs` / a reactive props destructure where supported |
| Template shows `[object Object]` for a ref inside an array or a plain object | `ref` auto-unwrap only applies at the top level of reactive state | Store `ref` values in `reactive()` state, or use `.value` deliberately |
| Watcher never fires | Watching a `.value` primitive, or a nested property without a getter | Watch `() => obj.nested.key`, and set `immediate: true` for the initial run |
| Watcher fires repeatedly with no visible change | Watching a getter that creates a new object each time | Return a primitive, or compare inside the watcher |
| Deep watcher makes the app janky | `deep: true` traverses everything on each change | Watch a narrow getter, or use `shallowRef` plus explicit replacement |
| State updates are not visible immediately after `await` | DOM updates are batched on the microtask queue | `await nextTick()` when you must read the DOM after a state change |
| `reactive()` returns read-only-looking data | `readonly()` was used on the provider side | Keep writable state and expose `readonly()` only at the boundary |
| Component leaks listeners after `onUnmounted` | Subscription created outside the setup scope | Create it in `setup`, or wrap in `effectScope()` and stop it |

Additional cautions:

- `reactive()` requires an object. `reactive(0)` and `reactive([])` as roots do not
  behave the way you expect; use `ref`.
- Do not pass a `reactive` object as a prop if the child might mutate it. Pass a
  `readonly` view or plain data.
- Do not store Vue reactive proxies inside third-party libraries that serialize or
  compare by identity; use `toRaw` deliberately and understand what you lose.
- Template refs need `ref="name"` plus a matching declared `ref`, or the newer
  `useTemplateRef` helper. Check which your Vue version supports.

## Checklist

- Composable files are named `useX.ts`, have one responsibility, and take inputs as
  arguments rather than reading globals.
- Derived values use `computed`; effects that touch the outside world use `watch` or
  `watchEffect`.
- Every watch has cleanup via `onCleanup`, and every request is abortable.
- Props, emits, models, and slots are declared with the macros, and nothing writes to a
  prop.
- Injection keys are typed `InjectionKey<T>` and consumers handle the missing case.
- Store state is read through `storeToRefs`.
- Async route components are used for code splitting.
- Debugging with Vue Devtools component inspector and reactivity timeline.

## Common mistakes

- **Destructuring `props` and expecting updates.** Symptom: the initial value renders
  and later prop changes are ignored. Fix: keep `props.x`, or use `toRef` /
  `toRefs`.
- **Writing `count.value++` where `count` came from `props`.** Symptom: Vue warns
  "Set operation on key ... failed: target is readonly" and the parent and child
  disagree. Fix: emit an update event or use `defineModel`.
- **Forgetting `immediate: true`.** Symptom: the first fetch never runs and the page is
  empty until a change occurs. Fix: pass the option, or call the handler once.
- **Unaborted race between requests.** Symptom: search results for the old query appear
  after the new query finished. Fix: `onCleanup` plus `AbortController`, and ignore
  results whose request is no longer current.
- **`provide` of a plain object with nested mutation.** Symptom: consumers never
  update. Fix: provide a `ref`.
- **`inject` without a default.** Symptom: `undefined` propagates and the error
  surfaces far from the cause. Fix: `inject(Key, null)` plus an explicit error.
- **Undeclared emits.** Symptom: a listener lands on the root element as an unknown
  attribute and the handler never runs. Fix: declare emits with `defineEmits`.
- **`reactive()` on a value you then replace.** Symptom: watchers on the old object
  keep firing forever. Fix: use `ref` for replaceable values.

## Anti-patterns

- **A composable that both reads and writes module-level singletons.** It works in one
  app and breaks in tests, in SSR, and in any second consumer. Take inputs as arguments.
- **Every piece of state in Pinia.** Global state is harder to delete than local state
  and hides which component owns what. Start local; promote when a second tree needs it.
- **`watchEffect` used as a general-purpose "computed with logging".** It hides its
  dependencies, runs eagerly, and is harder to reason about than a computed plus a
  watch with an explicit source.
- **Deep watching a large object to drive a checkbox.** Symptom: the UI is correct but
  the app stutters. Fix: watch the specific field.
- **Wrapping `reactive` objects in `toRaw` to work around reactivity problems.** You
  remove reactivity everywhere the object is used, and the next bug is much harder.
- **Mixing Options API and Composition API in one file.** It works, but the boundary
  rules are confusing and reviewers guess wrong. Pick one per file and per project.
- **`deep: true` as a default habit.** It is a performance decision disguised as a
  convenience.
- **Calling `provide` in a conditional branch.** A conditional `provide` runs only on
  the render where the condition was true, so descendants render inconsistently. Call
  it unconditionally at setup.

## References

- Vue Composition API guide: https://vuejs.org/guide/extras/composition-api-faq.html
- Reactivity API reference: https://vuejs.org/api/reactivity-core.html
- `<script setup>`: https://vuejs.org/api/sfc-script-setup.html
- `defineProps` and `defineEmits`: https://vuejs.org/api/sfc-script-setup.html#defineprops
- `ref` and `reactive`: https://vuejs.org/api/reactivity-core.html#ref
- `computed`: https://vuejs.org/api/reactivity-core.html#computed
- `watch` and `watchEffect`: https://vuejs.org/api/reactivity-core.html#watch
- `provide` and `inject`: https://vuejs.org/api/composition-api-dependency-injection.html
- Composition API FAQ: https://vuejs.org/guide/extras/composition-api-faq.html
- Vue Router: https://router.vuejs.org/guide/
- Vue Router navigation guards: https://router.vuejs.org/guide/advanced/navigation-guards.html
- Pinia: https://pinia.vuejs.org/core-concepts/
- Vue DevTools: https://devtools.vuejs.org/
- Vite (the standard build tool for Vue): https://vite.dev/guide/

**Verify before relying on this:** reactive props destructure, `useTemplateRef`, and
`useId` were introduced or stabilized in recent Vue 3 minors. Check the Vue release
notes and the API reference for the exact version your project pins before depending on
them.
