# Monorepo lint and format configuration

## Decide the shape first

| Shape | Structure | Use when |
| --- | --- | --- |
| Root config with overrides | One config at the root, per-directory `basePath` or `files` | Fewer than roughly twenty packages, or one team |
| Base config package | A package exporting a flat config array, extended by each package | Packages need independent lint rules or versions, or the config is shared across repositories |

Start with the root config. Move to a base package when you have a concrete reason:
different rules per domain group that fight with `files` patterns, or a need to publish
the rules to a separate repository.

## Root config with per-directory scoping

```javascript
// eslint.config.js
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig(
  globalIgnores([
    "**/dist/**",
    "**/build/**",
    "**/coverage/**",
    "**/node_modules/**",
    "**/*.snap",
  ]),

  js.configs.recommended,
  tseslint.configs.recommended,

  {
    name: "shared/base",
    files: ["**/*.{js,cjs,mjs,ts,cts,mts,tsx}"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module" },
    linterOptions: { reportUnusedDisableDirectives: "warn" },
    rules: {
      eqeqeq: ["error", "always"],
      "no-console": "warn",
    },
  },

  {
    name: "apps/web",
    basePath: "apps/web",
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
  },

  {
    name: "apps/api",
    basePath: "apps/api",
    files: ["src/**/*.ts"],
    languageOptions: { globals: globals.node },
    rules: { "no-console": "off" },
  },

  {
    name: "tests",
    files: ["**/*.{test,spec}.{ts,tsx}", "**/__tests__/**"],
    languageOptions: { globals: { ...globals.node, ...globals.jest } },
    rules: { "no-console": "off", "@typescript-eslint/no-explicit-any": "off" },
  },
);
```

Note that a `basePath` scopes that object to a subdirectory. Without it, a `files`
pattern like `src/**/*.ts` applies everywhere a directory called `src` exists.

## Biome in a monorepo

Biome supports nested configurations that extend the project root.

```jsonc
// biome.jsonc at the root
{
  "$schema": "./node_modules/@biomejs/biome/configuration_schema.json",
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "files": { "includes": ["**", "!!**/dist", "!!**/coverage"] },
  "formatter": { "enabled": true, "indentStyle": "space", "indentWidth": 2, "lineWidth": 100 },
  "linter": { "enabled": true, "rules": { "recommended": true } }
}
```

```jsonc
// packages/frontend/biome.json
{
  "extends": "//"
}
```

`"//"` resolves to the project root without a relative path and works at any nesting
depth. A nested configuration that uses `"extends": "//"` is implicitly not a project
root; other nested configurations must say so explicitly with `"root": false`.

Use a relative path or a package specifier when you want to extend something other than
the root:

```jsonc
{
  "extends": ["./base.json", "@acme/biome-config"]
}
```

Configurations merge left to right, with later entries winning for single-valued options.

## Formatting across a monorepo

One Prettier configuration at the root. Editors find it by walking up, and the CLI
resolves it the same way.

```json
{
  "semi": true,
  "singleQuote": false,
  "printWidth": 100,
  "tabWidth": 2,
  "trailingComma": "all",
  "ignorePatterns": ["**/dist/**", "**/coverage/**", "**/generated/**"]
}
```

Only ignore genuinely generated or vendored files. A file that is ignored because it is
inconvenient to format will come back in the next refactor.

## Running lint across the workspace

```bash
# One pass from the root, using the root config.
npx eslint .

# Per package, useful for type-aware rules that need each package's tsconfig.
pnpm -r --if-present run lint

# Only changed packages, in a CI pull request workflow.
# Compare against the merge base rather than a branch name that might not exist locally.
git diff --name-only origin/main...HEAD -- '*.ts' '*.tsx' | sed 's|/[^/]*$||' | sort -u
```

Type-aware linting across a monorepo needs care: each package's `tsconfig.json` must
resolve. Either lint per package with its own project, or use a root project service
configuration that resolves the nearest tsconfig per file. Test both paths before
committing to one; a root-only project service frequently produces "file not found in
project" errors for packages excluded from the root tsconfig.

## Editor configuration that matches CI

```json
// .vscode/settings.json, committed at the repository root
{
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" },
  "eslint.useFlatConfig": true,
  "[typescript]": { "editor.defaultFormatter": "esbenp.prettier-vscode" },
  "typescript.tsdk": "node_modules/typescript/lib",
  "files.exclude": { "**/dist": true, "**/coverage": true }
}
```

`typescript.tsdk` matters in a monorepo. Without it the editor uses a global TypeScript
version and reports errors that CI does not, or misses ones CI does.

## CI with affected-package detection

```yaml
# .github/workflows/lint.yml
name: lint
on:
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # needed for merge-base comparisons
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - name: Lint the whole repository
        run: npm run lint
      - name: Typecheck every package
        run: pnpm -r run typecheck
      - name: Format check
        run: npm run format:check
```

Affected-only linting is a good optimisation once builds are slow, but run the full
lint at least nightly so a package that stopped being changed still gets checked.
Otherwise a package can accumulate violations indefinitely because nobody edits it.

## Consistency checks that scale

- One formatter version in the root lockfile, used by every editor and CI.
- One lint config, not a copy per package.
- A committed `tsconfig.base.json` that packages extend, so compiler options do not drift.
- A CI check that no package has its own committed ESLint or Biome config without an
  explicit reason.

```bash
# Flag drifted configs.
find . -name 'eslint.config.*' -not -path '*/node_modules/*' \
  -not -path './eslint.config.js'
find . -name 'biome.json*' -not -path '*/node_modules/*' -not -path './biome.jsonc'
```

## Practical pitfalls

| Symptom | Cause | Fix |
| --- | --- | --- |
| Different rules in different packages with no root config | Each package has its own config and ESLint stopped at the nearest one | One root config with `basePath` scoping |
| "File not found in project" for type-aware lint | Package excluded from the tsconfig the rule resolved | Align tsconfig includes with lint targets |
| Editor and CI disagree on formatting | Editor using a different config or a global install | Commit editor settings; use the local binary |
| Lint is fast locally, very slow in CI | Type-aware rules resolving from the repository root | Lint per package with per-package project service |
| A package skipped entirely | No script named `lint` and the root run misses it | CI asserts every workspace package has a lint script |