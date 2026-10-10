# tsconfig, Module Resolution, and Declaration Files

## Minimal strict baseline

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "noFallthroughCasesInSwitch": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"]
}
```

Notes on each entry:

- `target` and `lib` are separate. `lib` sets which standard library declarations exist;
  leaving it unset means "whatever `target` implies", which silently excludes newer
  built-ins like `Array.prototype.at`.
- `skipLibCheck: true` stops the compiler from type-checking `.d.ts` files from
  dependencies. This is a build-time optimisation and does not skip your own code.
- `noEmit: true` keeps `tsc` a checker only; the bundler or `tsc` build script handles
  output. Remove it if `tsc` is your build step.
- `forceConsistentCasingInFileNames` matters on Windows and macOS, which are
  case-insensitive by default; it stops a build that passes locally from failing on Linux.

## The strict bundle, member by member

`"strict": true` turns on all of these. You can also set them individually.

| Flag | What breaks without it |
| --- | --- |
| `noImplicitAny` | `function f(x) {}` silently becomes `any`; typos go unnoticed |
| `strictNullChecks` | `null` assignable everywhere; `undefined` never surfaces |
| `strictFunctionTypes` | unsound parameter positions accepted in function types |
| `strictBindCallApply` | `bind`/`call`/`apply` lose their arity checking |
| `strictPropertyInitialization` | `class C { x: number }` with no assignment compiles |
| `noImplicitThis` | `this` with no defined receiver |
| `alwaysStrict` | no `"use strict"` emit (implied by `strict`) |
| `useUnknownInCatchVariables` | `catch (e)` typed `any`; here `e` is `unknown` |

Adopting `strictNullChecks` in an existing codebase: the bulk of errors are implicit
`undefined` from array access, `Map.get`, and uninitialised fields. Turn on
`noUncheckedIndexedAccess` separately so you can distinguish "null came from the type" from
"null came from an unchecked index".

## Migration order that works

1. `strict: true`. Expect the largest error count, concentrated in `null` handling.
2. Fix by narrowing, not by `!`. Where narrowing is genuinely impossible, annotate the
   boundary with a comment explaining why.
3. Add `noImplicitOverride`, `noFallthroughCasesInSwitch`,
   `noPropertyAccessFromIndexSignature`.
4. Add `noUncheckedIndexedAccess` per-directory or per-package, with
   `// @ts-expect-error` (with a reason) on the few sites that are provably safe.
   `@ts-expect-error` fails the build if the error disappears, so it self-cleans.
   `@ts-ignore` does not, which is why it should not be the default.
5. Add `exactOptionalPropertyTypes` last. It changes assignability for every optional
   property in every dependency's types, so it is disruptive and best done in isolation.

## Module resolution modes

| `moduleResolution` | Honours `exports` | Requires extensions | Typical use |
| --- | --- | --- | --- |
| `classic` | no | no | legacy only |
| `node` (`node10`) | no | no | older Node toolchains |
| `node16` | yes | yes for ESM | Node ESM/CJS aware |
| `nodenext` | yes | yes for ESM | current Node recommendation |
| `bundler` | yes | no | bundler-driven apps (Vite, webpack, esbuild) |

`module` must be compatible: `Node16`/`NodeNext` resolution pairs with `Node16`/`NodeNext`
module emit; `bundler` resolution pairs with `esnext`/`preserve` module output.

Practical consequences:

- Under `Node16`/`NodeNext`, write `import { x } from "./x.js"` even though the file is
  `x.ts`. The specifier refers to the emitted JavaScript.
- Under `bundler`, `import { x } from "./x"` is fine and does not run in Node directly.
- A dependency's `package.json` `exports` map may expose a `types` condition. If the mode
  you picked does not resolve it, the import degrades to `any` rather than erroring. When a
  type unexpectedly disappears, check `exports` first.

### Monorepo aliases

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@shared/*": ["packages/shared/src/*"],
      "@app/*": ["apps/web/src/*"]
    }
  }
}
```

`paths` only affects type resolution. The runtime (bundler, tsx, jest, ts-node) needs its
own matching alias, or the two drift and you get `Cannot find module` at runtime with a
clean type check. Prefer package entry points with `workspaces` when you can.

## Type-only imports and emit

```ts
import type { Config } from "./config.js";
import { load, type Config as LoadedConfig } from "./loader.js";
export type { Config };
export type * from "./types.js";
```

Without `import type`, the emitted JavaScript may keep the import statement, which:

- breaks a build where the dependency is `devDependencies`-only or browser-only,
- makes the module graph depend on a value you never used,
- turns a pure type import into a circular runtime dependency.

`verbatimModuleSyntax: true` enforces the distinction instead of erasing the difference,
so emitted modules match what you wrote. `importsNotUsedAsValues` and
`preserveValueImports` existed for this purpose and are deprecated; do not add them to new
projects. Check the tsconfig reference for current status before touching either.

`isolatedModules: true` is required by most bundlers (esbuild, SWC) because they transpile
file-by-file without type information. It forbids constructs that need whole-program
analysis, such as re-exporting a type with `export { SomeType }`. Use `export type`.

## Declaration files

```ts
// dist/index.d.ts (abridged shape of what tsc emits)
export declare function greet(name: string): string;
export interface User {
  id: string;
  createdAt: Date;
}
export type UserId = User["id"];
export declare class Session {
  readonly id: string;
  constructor(opts: { ttlSeconds?: number });
  close(): void;
}
```

For a hand-maintained `.d.ts` next to a JavaScript file:

```ts
// express-style middleware used from JS, typed from TS
export type NextFunction = (err?: unknown) => void;
export type RequestHandler = (req: unknown, res: unknown, next: NextFunction) => void;

declare global {
  interface Array<T> {
    chunk(size: number): T[][];
  }
}
```

Prefer generating declarations with `declaration: true` over maintaining them by hand.
Hand-written `.d.ts` files drift from the implementation silently.

## Declaration merging and augmentation

- `interface` + `interface` with the same name merges. Duplicate members with different
  types are an error.
- `class` + `interface` with the same name: the interface members are added to the
  class's instance type. The class does not need to implement them.
- `namespace` + `function`, `namespace` + `namespace`, `enum` + `enum` all merge.
- `declare global` inside a module file adds ambient declarations visible project-wide.
  A `.d.ts` without any top-level `import`/`export` is already global.

Use augmentation to add a property your own library owns. Do not monkey-patch
`Array.prototype` or built-in interfaces to work around a missing API; you are making every
consumer's type checker accept something the runtime does not provide.

## Project references

```json
{
  "files": [],
  "references": [{ "path": "./packages/core" }, { "path": "./packages/web" }]
}
```

Each referenced package has `composite: true` and emits declarations. `tsc --build`
compiles them in dependency order and caches with `.tsbuildinfo`. This gives real
incremental builds in a monorepo; verify the exact flags against the project-references
docs for your TypeScript version before adopting.

## Verify before relying on this

Compiler options, resolution modes, and the exact behaviour of `verbatimModuleSyntax`,
`isolatedModules`, and `const` type parameters have all changed across releases. Check:

- https://www.typescriptlang.org/tsconfig/
- https://www.typescriptlang.org/docs/handbook/project-references.html

before copying a flag list from an older project into a new one.