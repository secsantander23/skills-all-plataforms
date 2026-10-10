# Narrowing and Unions in Depth

## How narrowing actually works

Narrowing is *control-flow analysis over types*. The compiler keeps, at every program
point, a union of the types that value could still have. Each construct either filters
that union or resets it:

| Construct | Effect |
| --- | --- |
| `typeof x === "..."` | filters primitives and objects by typeof tag |
| `x instanceof C` | keeps types assignable to `C`'s instance side |
| `"prop" in x` | keeps object types that declare `prop` |
| `x === lit` / `switch` | filters literal and union member types |
| truthiness check | removes `null`, `undefined`, `false`, `0`, `""`, `0n`, `NaN` |
| `x != null` | removes both `null` and `undefined` |
| `x is T` guard | narrows to `T` in the true branch, `Exclude` in the false branch |
| `asserts x is T` | asserts the type for the rest of the scope |

A reset happens when the compiler cannot prove the value is unchanged: an assignment
inside a callback you cannot see, `any` flowing in, or a property access whose type is
not tracked across the boundary.

### Properties need help

Control-flow analysis tracks `let`/`const` variables and readonly properties well.
Mutable object properties are narrowed within a block but reset after a function call that
the compiler believes may modify them.

```ts
function f(input: { user?: { name: string } } | undefined) {
  const name = input?.user?.name?.trim();
  if (name) {
    // name: string | undefined -> narrowed to string
    return name.length;
  }
  return 0;
}
```

The `const` local is the reliable pattern. Chain optional access into a local, then
narrow the local.

### Assertion signatures narrow the whole scope

```ts
function assertDefined<T>(value: T): asserts value is NonNullable<T> {
  if (value === null || value === undefined) {
    throw new Error("Expected a defined value");
  }
}

let user = maybeUser;
assertDefined(user);
user.name; // user is narrowed for the rest of the function
```

This requires an explicit type annotation on the call target for the assertion to apply
(at the top level of a file or function body, the call must be to an explicitly typed
identifier). That is a real gotcha: `const a = assertDefined` loses the narrowing.

## Discriminated union recipes

### Async state, done well

```ts
type Async<T, E = Error> =
  | { status: "idle" }
  | { status: "pending"; startedAt: number }
  | { status: "resolved"; value: T; at: number }
  | { status: "rejected"; error: E; at: number };

function isSettled<A extends Async<unknown, unknown>>(s: A): boolean {
  return s.status === "resolved" || s.status === "rejected";
}
```

### Result type for operations that can fail without throwing

```ts
type Result<T, E> =
  | { ok: true; value: T }
  | { ok: false; error: E };

function mapResult<T, U, E>(
  r: Result<T, E>,
  f: (t: T) => U,
): Result<U, E> {
  return r.ok ? { ok: true, value: f(r.value) } : r;
}
```

### Exhaustiveness

```ts
export function assertNever(value: never, message?: string): never {
  throw new Error(message ?? `Unhandled variant: ${JSON.stringify(value)}`);
}

type Shape =
  | { kind: "circle"; r: number }
  | { kind: "rect"; w: number; h: number };

function area(s: Shape): number {
  switch (s.kind) {
    case "circle":
      return Math.PI * s.r ** 2;
    case "rect":
      return s.w * s.h;
    default:
      return assertNever(s);
  }
}

// Add a third variant and `area` fails to compile. That is the whole point.
```

### Multi-dimensional state

Two orthogonal booleans collapse into a union of four cases. Model it as four variants
when the combinations are mutually exclusive by meaning.

```ts
type Subscription =
  | { tier: "free"; usage: number }
  | { tier: "pro"; usage: number; renewsAt: string }
  | { tier: "cancelled"; cancelledAt: string; refund?: number };
```

Do not force a union when the combinations are genuinely independent; that is the case
where separate optional fields are correct.

## Narrowing collections

```ts
interface HttpEvent {
  type: "request";
  method: "GET" | "POST";
  path: string;
}
interface ErrorEvent {
  type: "error";
  message: string;
  status: number;
}
interface LogEvent {
  type: "log";
  level: "info" | "warn";
  text: string;
}

type Event = HttpEvent | ErrorEvent | LogEvent;

function summarize(events: readonly Event[]): string[] {
  const failures = events.filter((e): e is ErrorEvent => e.type === "error");

  // Works without an explicit predicate: the arrow's inferred type narrows the result
  const posts = events.filter((e) => e.type === "request" && e.method === "POST");

  return failures.map((e) => `${e.status}: ${e.message}`);
}
```

`readonly` arrays matter: `.filter` on a `readonly T[]` returns `T[]`, and accepting
`readonly T[]` as a parameter is more permissive for callers than accepting `T[]`.

### Partition into matched/unmatched

```ts
function split<T>(items: readonly T[], isValid: (item: T) => boolean) {
  const valid: T[] = [];
  const invalid: T[] = [];
  for (const item of items) (isValid(item) ? valid : invalid).push(item);
  return { valid, invalid } as const;
}
```

## Narrowing against library types

The trick that avoids all of the `instanceof` branching on known string-literal kinds:

```ts
function parseEvent(value: unknown): Event {
  if (typeof value !== "object" || value === null || !("type" in value)) {
    throw new TypeError("not an event");
  }
  switch (value.type) {
    case "request":
      return { type: "request", method: "GET", path: "/" };
    case "error":
      return { type: "error", message: "boom", status: 500 };
    case "log":
      return { type: "log", level: "info", text: "hello" };
    default:
      throw new TypeError(`unknown event type: ${String(value.type)}`);
  }
}
```

Validate untrusted data at one boundary, then work with the parsed type everywhere else.
Do not thread `unknown` through the whole application; it makes every function harder to
read and pushes validation into places that cannot perform it properly.

## Common narrowing failures

**`Property 'status' does not exist on type 'A' | 'B'` in the default branch.** The `never`
assignment is the intended failure, but an error on the *property access* means the union
was never discriminated in the first place. Check for literal types on the discriminant.

**`This comparison appears to be unintentional` between two non-overlapping types.** Your
discriminant values do not match on one side, so that `case` is unreachable. Often a typo
in a string literal.

**Narrowing lost inside `.map`.** Arrow callbacks may run later; the compiler cannot assume
`const` narrowing carries. Re-narrow inside the callback or capture a narrowed const:

```ts
const state: LoadState<User> = load();
// Capture a const so the callback keeps the narrowed type:
const active = state;
items.map((i) => format(active)); // active is the same narrowed reference
```

The real fix for async flows is to derive state rather than read a mutable variable.