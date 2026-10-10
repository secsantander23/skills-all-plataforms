# Monorepo recipes and diagnostics

Working configurations for `monorepo-workflow`. Tool option names change between majors;
verify against the `$schema` URL your installed tool generates before adopting a snippet.

## Diagnosing a slow monorepo CI run

Work in this order; the answer is usually in the first three steps.

```bash
# 1. How many projects, and how much does one full run cost?
npx nx show projects
npx nx run-many -t test --all --verbose

# 2. Is the cache actually hitting?
npx nx reset
npx nx run-many -t test --all        # first run: cache misses
npx nx run-many -t test --all        # second run: should be near-instant
```

If the second run is slow, the cache key is unstable. Usual causes:

- An input declared as `["default", "^default"]` includes files outside the project, such as a
  shared config that changes on every commit.
- `outputs` is not declared, so the build's output is hashed as an input.
- An environment variable is part of the hash and changes every run.

Turborepo equivalent:

```bash
npx turbo run build --filter=@acme/ui --dry=json   # inspect the task graph and inputs
npx turbo run build --filter=@acme/ui --force       # bypass cache, measure true cost
```

## Splitting CI by package group

If you cannot make affected detection correct, partition instead. Path filters are reliable
because they do not depend on graph correctness.

```yaml
name: ci
on:
  pull_request:
    branches: [main]

jobs:
  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'pnpm'
      - run: pnpm install --frozen-lockfile
      - run: pnpm exec turbo run build test lint --filter=@acme/web...

  docs:
    runs-on: ubuntu-latest
    if: contains(github.event.pull_request.labels.*.name, 'docs')
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'pnpm'
      - run: pnpm install --frozen-lockfile
      - run: pnpm run docs:build
```

`--filter=@acme/web...` includes the package and its dependencies, which is what you want for
a build job.

## Preventing dependency cycles

Nx ships an ESLint rule for this. In `.eslintrc.json`:

```json
{
  "extends": ["nx/enforce-module-boundaries"],
  "rules": {
    "@nx/enforce-module-boundaries": [
      "error",
      {
        "enforceBuildableLibDependency": true,
        "allow": [],
        "depConstraints": [
          {
            "sourceTag": "type:app",
            "onlyDependOnLibsWithTags": ["type:app", "type:lib"]
          },
          {
            "sourceTag": "type:lib",
            "onlyDependOnLibsWithTags": ["type:lib"]
          }
        ]
      }
    ]
  }
}
```

Tag each project in `nx.json` under `tags`:

```json
{
  "tags": ["type:app", "scope:web"]
}
```

`enforceBuildableLibDependency` is the option that catches an app importing another app. Tag
names are yours; only the rule name and option names matter.

## Shared configuration packages

A common pattern is a `config-*` package per tool, consumed through the package manager's
`exports` field rather than a bare file path:

```json
{
  "name": "@acme/config-lint",
  "version": "0.0.0",
  "private": true,
  "exports": {
    "./eslint": "./eslint.config.mjs",
    "./typescript": "./tsconfig.json"
  },
  "files": ["eslint.config.mjs", "tsconfig.json"]
}
```

Consumers extend it by path in their own config:

```js
// eslint.config.mjs in a consuming package
import base from '@acme/config-lint/eslint';

export default [
  ...base,
  {
    rules: {
      // package-specific overrides
    },
  },
];
```

Keep the shared config minimal. Every rule in it applies to every package, and a rule that is
right for 80 packages is usually wrong for the one that trips it.

## TypeScript project references

`tsconfig.base.json` at the root holds shared compiler options; each package extends it:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "declaration": true,
    "composite": true,
    "skipLibCheck": true
  }
}
```

A package's `tsconfig.json`:

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "outDir": "./dist",
    "rootDir": "./src"
  },
  "include": ["src/**/*"],
  "references": [{ "path": "../utils" }]
}
```

`composite` and `references` are what make incremental `tsc --build` work across packages.
Without them, every package recompiles every dependency on every build.

Module specifier note: `NodeNext` module resolution requires that a package's `package.json`
declares `"type": "module"` or not, and that relative imports carry explicit file extensions
(`.js`) when running compiled output. Check your toolchain before switching resolution modes.

## Commit and PR conventions

Use the squash merge target branch as the boundary between internal packages and external
consumers.

- A commit that changes `packages/ui`'s **public** API and its exports needs a `feat!` /
  `BREAKING CHANGE` marker.
- A commit that only changes an internal detail, with no export change, does not — even if the
  file lives in a published package.

Making that distinction mechanically is hard; making it a review rule works. State the rule in
`CONTRIBUTING.md`.

## Verify before relying on this

- Nx configuration schema: generate with `npx nx init`, then copy the `$schema` from the file
  your Nx version writes. https://nx.dev/nx-api/nx/documents/nx-json
- Turborepo configuration: https://turbo.build/repo/docs/reference/configuration
- pnpm workspace globs and catalogs: https://pnpm.io/workspaces and https://pnpm.io/catalogs
- Yarn: confirm your major version's CLI at https://yarnpkg.com/features/workspace