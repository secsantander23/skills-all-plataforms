---
name: TypeScript Type System
description: Covers structural typing, union and intersection narrowing, discriminated unions, generics and constraints, conditional and mapped types, strict compiler flags, type-only imports, declaration merging, module resolution, and the satisfies operator. Use when the user asks about TypeScript types or type errors, when typing a public API, when modelling domain state as a discriminated union, when configuring tsconfig strict flags, when debugging bad type inference, or when writing declaration files.
metadata:
  opencode/autoinvoke: true
---

# TypeScript Type System

TypeScript types are not a separate language that runs; they are a static model that is
erased at build time. The core mental model is *structural*: a type is a set of members,
and two types are related when their members are compatible. Everything else in this skill
is a consequence of that one rule.

## When to use this

- The user asks "why is this type error happening" or mentions `tsc` errors.
- The user is modelling domain state (loading, success, empty, error) and needs types that
  make illegal states unrepresentable.
- The user is writing a library or SDK that ships `.d.ts` files.
- The user is configuring `tsconfig.json`, turning on `strict`, or fighting `any` leakage.
- The user asks about generics, `extends` constraints, conditional types, mapped types,
  `keyof`, template literal types, or `satisfies`.
- The user hits import resolution errors after switching between ESM and CommonJS.

## Quick start

A domain with illegal states made unrepresentable, plus an exhaustive `switch`:

```ts
type LoadState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "error"; error: Error };

function render<T>(state: LoadState<T>): string {
  switch (state.status) {
    case "idle":
      return "Nothing requested yet";
    case "loading":
      return "Loading...";
    case "success":
      return `Loaded ${String(state.data)}`;
    case "error":
      return `Failed: ${state.error.message}`;
    default: {
      const exhaustive: never = state;
      return exhaustive;
    }
  }
}

const ok = { status: "success", data: 42 } as const satisfies LoadState<number>;
// ok.status is narrowed to the literal "success", so ok.data is number
```

`satisfies` validates without widening: `ok` keeps the literal types of the object you
wrote, so you get autocomplete on the exact shape *and* the compile-time check that it
matches `LoadState<number>`.

## Structural typing, not nominal typing

Two unrelated classes with the same public shape are assignable to each other. Go's
interfaces are nominal; TypeScript's are not.

```ts
class Point { constructor(public x: number, public y: number) {} }
class Meters { constructor(public x: number, public y: number) {} }

const p: Point = new Meters(1, 2); // no error: same structure
```

Practical consequence: an object literal is assignable to any type with matching members,
so *shape*, not intent, is what the checker compares. This is why TypeScript needs
branding helpers when you want nominal behaviour.

```ts
declare const brand: unique symbol;

export type UserId = string & { readonly [brand]: "UserId" };
export type OrderId = string & { readonly [brand]: "OrderId" };

function getUser(id: UserId): string {
  return id;
}

const raw = "u_1" as string;
getUser(raw as UserId);      // cast: trust me
// getUser("u_1");           // error: string is not UserId
```

Two realistic escape hatches, in order of preference:

1. Change the design so the plain value is enough.
2. Use `unique symbol` branding, which makes mixing the two IDs a compile error.

`as unknown as T` is the last resort, not a first move. It disables checking entirely.

## Narrowing: how TypeScript tracks what is possible

Control-flow analysis tracks the type of every `let`/`const` and of properties it can
prove. Narrowing forms:

```ts
function narrow(value: string | number | null | undefined) {
  if (typeof value === "string") return value.toUpperCase(); // string
  if (value === null || value === undefined) return;          // null | undefined
  return value.toFixed(2);                                     // number
}

class Dog { bark(): void {} }
class Cat { meow(): void {} }

function speak(animal: Dog | Cat) {
  if (animal instanceof Dog) animal.bark(); // narrowed to Dog
  else animal.meow();
}

type Person = { name: string };
type Robot = { serial: string };

function describe(x: Person | Robot) {
  if ("serial" in x) return x.serial; // Robot
  return x.name;                       // Person
}
```

Key rule: narrowing a discriminated union requires the discriminant to be a *literal type*
(unless the object is declared `const`/`readonly` literal). `{ status: "success" }`
inferred as `{ status: string }` will not narrow. Fix with `as const`, an explicit type
annotation, or an interface with a literal-typed property.

Array element narrowing works too, and gives you type-safe filtering without a type guard:

```ts
interface Task { kind: "parse"; file: string } | { kind: "upload"; file: string; target: string };

const tasks: Task[] = [];
const parseTasks = tasks.filter((t) => t.kind === "parse");
// parseTasks: Task[] narrowed to the parse variant
```

User-defined type guards let you narrow a single value:

```ts
function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function greet(input: unknown): string {
  return isNonEmptyString(input) ? `Hello, ${input}` : "Hello, stranger";
}
```

Do not write `function isX(v: unknown): boolean` and cast at the call site. The
`value is T` return type is the entire point.

## Unions, intersections, and `never`

| Operator | Meaning | Use for |
| --- | --- | --- |
| `A \| B` | value is either | modelling alternatives, discriminated state |
| `A & B` | value has both | composing a value with extra fields |
| `unknown` | any value, nothing known | inputs you intend to validate (preferred over `any`) |
| `any` | opts out of checking | genuine escape hatch, use sparingly |
| `never` | no value possible | exhaustiveness, "this branch is impossible" |

Intersections of object literals are how you attach metadata to an existing type:

```ts
type Timestamped = { createdAt: Date };
type Entity = { id: string };
type Row = Entity & Timestamped & { tags: readonly string[] };
```

```ts
type Unreachable = string & number; // never
```

`unknown` vs `any` is the single highest-value habit. `unknown` forces you to narrow
before use, and the compiler tells you exactly where; `any` silently disables checking for
everything it touches, including downstream code that never mentioned it.

## Generics and constraints

```ts
// Constraint: T must have the shape of Record<string, unknown>
function countEntries<T extends Record<string, unknown>>(obj: T): number {
  return Object.keys(obj).length;
}

// Constraint as a key union: restrict which properties are allowed
type Person = { name: string; age: number; email: string };

function pluck<T, K extends keyof T>(obj: T, keys: readonly K[]): Pick<T, K> {
  const out = {} as Pick<T, K>;
  for (const k of keys) out[k] = obj[k];
  return out;
}

const sub = pluck({ name: "Ada", age: 36 } as Person, ["name", "age"]);
// sub: { name: string; age: number }
```

- Use `extends` for constraints, `=` for default type arguments: `function f<T = string>()`.
- `extends` on a class also constrains the *value* (only that class or subclasses), which is
  different from `extends` on a type parameter.
- Constraint failure looks like `Argument of type 'X' is not assignable to parameter of type
  'Record<string, unknown>'` with a "could be instantiated with a different subtype"
  note. That note means the type *might* be assignable but is not provably so; the fix is
  usually a stronger constraint or widening the target type.

Two type parameters that constrain each other:

```ts
function getOrCreate<K extends string, V>(map: Map<K, V>, key: K, make: () => V): V {
  if (map.has(key)) return map.get(key) as V;
  const value = make();
  map.set(key, value);
  return value;
}
```

## Conditional and mapped types

These are *type-level* computation: they run during checking and are erased.

```ts
type Unwrap<T> = T extends Promise<infer U> ? U : T;
type A = Unwrap<Promise<string>>;   // string
type B = Unwrap<number>;            // number

type Nullable<T> = T | null;

type KeysOfUnion<T> = T extends T ? keyof T : never; // distributes over unions

type Getters<T> = { [K in keyof T as `get${Capitalize<string & K>}`]: () => T[K] };
type PersonGetters = Getters<{ name: string; age: number }>;
// { getName: () => string; getAge: () => number }

// Combining: filter and remap in one pass
type ReadonlyById<T extends { id: string }> = {
  readonly [K in keyof T as K extends "id" ? K : never]: T[K];
} & Omit<T, "id">;

// Optional and readonly modifiers inside a mapped type
type PartialDeep<T> = { [K in keyof T]?: T[K] extends object ? PartialDeep<T[K]> : T[K] };
```

Key modifiers you can prefix the mapped key with: `+?`, `-?`, `+readonly`, `-readonly`.
`as` remaps and filters keys. `[K in keyof T as ...]` is the standard way to build a
dictionary of only the properties of a given type:

```ts
type PropertyOfType<T, V> = { [K in keyof T as T[K] extends V ? K : never]: T[K] };
```

Builtin utilities worth knowing: `Partial`, `Required`, `Readonly`, `Pick`, `Omit`,
`Record`, `Exclude`, `Extract`, `NonNullable`, `ReturnType`, `Parameters`, `Awaited`.
Full list and semantics: see the handbook utility-types page in References.

## Strict compiler flags

`"strict": true` is a bundle. Turning it on in an existing codebase produces errors; that
is the point, but stage it.

| Flag | What it catches |
| --- | --- |
| `noImplicitAny` | parameters and members with no inference source |
| `strictNullChecks` | `null`/`undefined` not assignable to other types (the big one) |
| `strictFunctionTypes` | parameter contravariance violations |
| `strictBindCallApply` | `bind`/`call`/`apply` signature checks |
| `strictPropertyInitialization` | class fields not definitely assigned in the constructor |
| `noImplicitThis` | `this` with no clear receiver |
| `alwaysStrict` | emits `"use strict"`, implied by `strict` |
| `useUnknownInCatchVariables` | `catch (e)` is `unknown`, so you must narrow |

Additional flags worth enabling, one at a time:

| Flag | Effect | Cost |
| --- | --- | --- |
| `noUncheckedIndexedAccess` | `arr[i]` becomes `T \| undefined` | real churn; valuable on untrusted data |
| `exactOptionalPropertyTypes` | `a?: string` no longer accepts `{ a: undefined }` | real churn; fixes a genuine soundness hole |
| `noImplicitOverride` | requires `override` on overriding members | low |
| `noPropertyAccessFromIndexSignature` | forces `obj["k"]` for index-signature keys | low |
| `noUnusedLocals` / `noUnusedParameters` | dead code | low, but pairs with underscore convention |
| `noFallthroughCasesInSwitch` | catches missing `break` | low |

The churn flags (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`) are best
introduced after the strict bundle. Order of adoption:

1. `strict` (biggest correctness win, largest error count)
2. `noImplicitOverride`, `noFallthroughCasesInSwitch`, `noPropertyAccessFromIndexSignature`
3. `noUncheckedIndexedAccess` per-module with `// @ts-expect-error` for known-safe sites
4. `exactOptionalPropertyTypes` last, it interacts with every third-party API's `?`

Run `tsc --noEmit` in CI. Do not rely on an editor-only check.

## Type-only imports and declaration merging

`import type` is erased at compile time, so it never survives into emitted JavaScript.
That matters when the import is only for typing: without it, a plain `import` can force a
runtime module resolution that breaks a server build or drags in a browser-only dependency.

```ts
import type { User } from "./user.js";            // erased entirely
import { loadUser, type User as UserT } from "./api.js"; // mixed, `type` modifier

export type { User };
export type * from "./types.js";                  // re-export types only
```

If a project sets `"verbatimModuleSyntax": true`, type-only imports must be marked with
`import type`; the compiler errors instead of silently rewriting them, which keeps the
emitted module graph predictable. Check your `tsconfig.json` and the module docs before
assuming either behaviour.

Declaration merging composes declarations with the same name:

```ts
interface Window {
  appConfig: AppConfig;
}
declare global {
  interface Window { appConfig: AppConfig }
}

namespace Util {
  export function noop(): void {}
}
namespace Util {
  export const VERSION = "1";   // merged with the block above
}
```

Merging an `interface` with a `class` adds the interface's members to the class's instance
type. Merging interfaces lets third-party packages augment a type you do not own. Merging
enums works too, but only across declarations, and it is the merging rule most likely to
surprise you. Prefer augmentation through `declare global` over reopening a namespace.

## Module resolution

`moduleResolution` picks how a specifier becomes a file. Values you will meet:

| Value | Behaviour |
| --- | --- |
| `classic` | legacy, pre-`node_modules` lookup; avoid |
| `node` (`node10`) | classic Node directory walk, no `exports` field |
| `node16` / `nodenext` | honours `package.json` `type`, `exports`, and `imports` |
| `bundler` | bundler semantics, allows extensionless relative specifiers |

Two consequences bite people:

- Under `node16`/`nodenext`, relative ESM imports must include the emitted file extension:
  `import { x } from "./x.js"` even when the source file is `x.ts`. The specifier is
  resolved as runtime JavaScript, not as TypeScript source.
- A package that sets `"type": "module"` and a package that does not can resolve the same
  specifier differently. When a type suddenly disappears only in your build, check the
  package's `exports` map before suspecting your own code.

Path mapping is `baseUrl` plus `paths` (both inherited from the old module resolution world).
Prefer resolving relative paths and package entry points; reach for `paths` only when you
need aliases across a monorepo. Always verify the current resolution rules in the module
reference in the official docs, since the option set changes between modes.

## Checklist

- [ ] `strict` is on; the remaining flags are added deliberately, not all at once.
- [ ] Domain state uses a discriminated union, not a bag of optional fields plus booleans.
- [ ] Every `switch` over a discriminated union ends in a `never` check.
- [ ] Type guards are declared `value is T`, not `boolean`.
- [ ] Untrusted input is `unknown` and narrowed before use, not cast.
- [ ] No bare `any` in exported signatures; grep for `any` before release.
- [ ] Type-only imports use `import type` so the emitted JS does not depend on them.
- [ ] Public library types are exported explicitly rather than inferred from internals.
- [ ] `tsc --noEmit` runs in CI, not just in the editor.
- [ ] Branded IDs exist where mixing two same-shaped primitives would be a real bug.

## Common mistakes

**"Object literal may only specify known properties" on a variable you already declared.**
Excess property checking applies to *fresh* object literals, which widens the set of types
that can trigger it. Fix by annotating the variable, or by including the property in the
type. Do not disable it.

**`Type 'undefined' is not assignable to type 'X'` right after an `if (!x) return`.**
The guard did not narrow because `x` is a property of a mutable object or the check
happened inside a callback. Copy to a local `const`, or re-read the property in the guard
so control-flow analysis can track it.

**`Type 'X' is not assignable to type 'never'` in the `assertNever` default branch.**
A union member is not handled in the `switch`. Add the case. This is the exhaustive check
doing its job.

**`Property 'x' comes from an index signature, so it must be accessed with ['x'].**
Enablement of `noPropertyAccessFromIndexSignature`, or the type genuinely is an index
signature. Use the bracket form and type the index.

**`Object is possibly 'null'` from a callback argument.** Callbacks invoked later lose the
narrowing from the enclosing scope unless the value is a `const`. Assign to a `const` first,
or re-narrow inside the callback.

**`Cannot find module './foo' or its corresponding type declarations` after switching to
`node16`/`nodenext`.** Add the `.js` extension to the relative specifier.

**A type from a library became `any` after enabling `skipLibCheck` changes or a new
version.** Check whether the package ships `exports` types conditions that your resolution
mode now honours differently.

## Anti-patterns

- **Reaching for `any` to silence an error.** Use `unknown` plus narrowing; the error is
  information about a real gap.
- **`as` casts to bypass the checker.** Every `as` is an unchecked assertion that moves a
  failure from compile time to production.
- **`!` (non-null assertion) as a habit.** It says "I believe this is never null" without
  proving it. Prove it with a guard.
- **Optional fields plus booleans for state** (`isLoading?: boolean`, `data?: T`,
  `error?: Error`). This permits `isLoading: true` with both `data` and `error` set. Use a
  discriminated union.
- **One giant interface with 60 optional properties** instead of composing smaller ones.
  Nothing is required, so nothing is checked.
- **`Record<string, any>` for structured data.** Use `unknown` and validate at the edge.
- **Enum for a set of values that crosses a JSON boundary.** String-literal unions survive
  JSON round-trips; enums do not (numeric enums especially).
- **Deeply nested conditional types with no named intermediate types.** Name the steps;
  the error messages become readable.
- **Writing types by hand for library data.** Derive from the source with `typeof` /
  `Awaited<ReturnType<...>>` so they cannot drift.
- **Trusting the editor alone.** The editor uses your local `tsconfig.json`; CI compiles
  with the repository's settings, which may be stricter.

## References

- TypeScript Handbook: introduction and every topic referenced above
  - https://www.typescriptlang.org/docs/handbook/intro.html
  - https://www.typescriptlang.org/docs/handbook/2/narrowing.html
  - https://www.typescriptlang.org/docs/handbook/2/everyday-types.html
  - https://www.typescriptlang.org/docs/handbook/2/generics.html
  - https://www.typescriptlang.org/docs/handbook/2/conditional-types.html
  - https://www.typescriptlang.org/docs/handbook/2/mapped-types.html
  - https://www.typescriptlang.org/docs/handbook/2/objects.html
  - https://www.typescriptlang.org/docs/handbook/2/functions.html
  - https://www.typescriptlang.org/docs/handbook/2/modules.html
  - https://www.typescriptlang.org/docs/handbook/utility-types.html
  - https://www.typescriptlang.org/docs/handbook/declaration-files.html
- `tsconfig.json` reference, including every flag named above
  - https://www.typescriptlang.org/tsconfig/
- Compiler options that affect module emit and resolution
  - https://www.typescriptlang.org/tsconfig/#moduleResolution
  - https://www.typescriptlang.org/tsconfig/#verbatimModuleSyntax
- Playground for reproducing a type quickly
  - https://www.typescriptlang.org/play

For deeper reading:

- `references/narrowing-and-unions.md` — narrowing algorithm, discriminated union recipes,
  exhaustiveness helpers.
- `references/generics-and-type-level.md` — constraint patterns, infer sites, variadic
  tuples, advanced type-level utilities.
- `references/tsconfig-and-modules.md` — flag-by-flag adoption guide, module resolution
  modes, declaration merging and `.d.ts` patterns.