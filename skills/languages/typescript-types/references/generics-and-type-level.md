# Generics and Type-Level Computation

## Where `infer` appears

`infer` only exists inside a conditional type, and only in the `extends` position.

```ts
type ElementOf<T> = T extends (infer U)[] ? U : never;
type Unwrap<T> = T extends Promise<infer U> ? U : T;
type FnReturn<T> = T extends (...args: never[]) => infer R ? R : never;
```

Two constraints on the `infer` pattern: the checked type must be naked (no array or
object wrapper) for distribution to work, and `...args: never[]` is the safe parameter
list when you only care about the return type.

### Distribution and `NoInfer`

A naked type parameter distributes over unions:

```ts
type ToArray<T> = T extends unknown ? T[] : never;
type R = ToArray<"a" | "b">; // string[]
```

Opt out of distribution with a tuple wrapper:

```ts
type ToArrayNonDist<T> = [T] extends [unknown] ? T[] : never;
type R2 = ToArrayNonDist<"a" | "b">; // ("a" | "b")[]
```

`NoInfer<T>` blocks a candidate from being inferred from that position, which is how you
force inference to come from the *other* argument. It is a built-in utility type; check the
utility-types reference if you need its exact definition.

```ts
function setValue<T>(obj: { value: T }, next: NoInfer<T>): { value: T } {
  obj.value = next;
  return obj;
}

setValue({ value: 1 }, "hello" as any); // T infers from the object, not from next
```

## Constraint patterns

```ts
// Keys whose values extend V
type KeysOfType<T, V> = { [K in keyof T]-?: T[K] extends V ? K : never }[keyof T];

// Async function shape
type AnyFn = (...args: any[]) => any;
type AsyncFn = (...args: any[]) => Promise<unknown>;

// Route handler: preserves the parameter type by inferring the whole tuple
type RouteHandler<Ctx, Params = unknown> = (ctx: Ctx, params: Params) => void | Promise<void>;

function on<P>(handler: RouteHandler<{ db: string }, P>): RouteHandler<{ db: string }, P> {
  return handler;
}
```

**Reverse mapped types** map over the *value* type and key the result by the source
property name. `KeysOfType` above is the canonical example: mapping over `T` produces an
object of keys, then indexing by `keyof T` produces the union of the matching keys.

## Variadic tuples and tuple inference

```ts
type Tail<T extends readonly unknown[]> = T extends readonly [unknown, ...infer R] ? R : [];

type First<T extends readonly unknown[]> = T extends readonly [infer F, ...unknown[]] ? F : never;

function concat<A extends readonly unknown[], B extends readonly unknown[]>(
  a: A,
  b: B,
): [...A, ...B] {
  return [...a, ...b];
}
```

Variadic tuple types let a function *preserve* arity and element types, which ordinary
generic parameters cannot express. Use them in wrapper/decorator utilities where the
parameter list must pass through untouched.

## Recursive conditional types

```ts
type DeepReadonly<T> = T extends (infer U)[]
  ? readonly DeepReadonly<U>[]
  : T extends object
    ? { readonly [K in keyof T]: DeepReadonly<T[K]> }
    : T;

type DeepPartial<T> = T extends (infer U)[]
  ? DeepPartial<U>[]
  : T extends object
    ? { [K in keyof T]?: DeepPartial<T[K]> }
    : T;

type Jsonify<T> =
  | string | number | boolean | null
  | Jsonify<T>[]        // error: circular reference on a naive version
  | { [K in keyof T]: Jsonify<T[K]> };
```

Recursive type aliases are supported, but a self-referential one must terminate. If the
compiler reports a circularity error, the recursion has no base case for some
instantiation. Add an explicit primitive base case.

## Type-level performance

Deep recursion over large object types is slow. Practical rules:

- Put a cheap branch first so the compiler short-circuits: check `T extends string | number |
  | boolean | null | undefined` before recursing into `T[K]`.
- Avoid `PartialDeep` / `Jsonify` style helpers on huge inferred types; annotate the
  boundary type manually instead.
- Break a long chain into named intermediate aliases. Error messages and caching both
  improve.

## Deriving types from implementation

```ts
export async function fetchUser(id: string): Promise<User> { /* ... */ }

export type FetchUser = typeof fetchUser;
export type UserId = Parameters<typeof fetchUser>[0];
export type User = Awaited<ReturnType<typeof fetchUser>>;
```

This is the right direction of dependency: implementation to types, not types restated by
hand. When writing `.d.ts` files for a library you do not own, prefer importing the real
types over re-declaring them.

## Const assertions and const type parameters

```ts
const routes = {
  home: "/",
  user: "/user",
} as const;
// routes.home: "/" — a literal type, not string

type Route = (typeof routes)[keyof typeof routes]; // "/" | "/user"
```

`as const` makes properties `readonly` and uses literal types. It is the cheapest way to
keep a literal from widening. Applied to a function return value it does the same for
everything the function produces:

```ts
function palette() {
  return { primary: "#0a0", secondary: "#0a0a0a" } as const;
}
type Primary = (typeof palette)["primary"];
```

A *const type parameter* (`<const T>`) applies the same behaviour to an inferred
parameter without an `as const` at the call site. This is a newer feature; confirm it is
available in the TypeScript version your project pins before depending on it, and see the
release notes linked from the handbook.

## Mapped type modifiers

```ts
interface Config { readonly a?: string; b?: number }

// make optional -> required
type Required<T> = { [K in keyof T]-?: Exclude<T[K], undefined> };
// make required -> optional
type Partial<T> = { [K in keyof T]+?: T[K] };

// remove readonly
type Mutable<T> = { -readonly [K in keyof T]: T[K] };
```

`-?` strips `undefined` from the value as well as the optionality, which is why the
handbook's `Required<T>` differs from the naive `{ [K in keyof T]-?: T[K] }`.

## Type guards for library APIs

Some libraries return widened types where a literal is intended. Re-assert with a guard
rather than a cast:

```ts
function toStatus(value: string): LoadStatus {
  const known: readonly string[] = ["idle", "loading", "success", "error"];
  if (known.includes(value)) return value as LoadStatus;
  throw new RangeError(`unknown status: ${value}`);
}
```

The guard keeps the cast in one audited place instead of spreading it across call sites.