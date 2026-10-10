---
name: linting-formatting
description: Covers linting and formatting for JavaScript and TypeScript, including ESLint flat config, Biome, Prettier, rule selection, type-aware linting with typescript-eslint, custom rules, editor integration, pre-commit hooks with lint-staged and husky, enforcing checks in CI, and monorepo lint configuration. Use when the user mentions ESLint, Biome, Prettier, a lint rule, lint-staged, husky, format on save, or a lint config that stopped working.
metadata:
  opencode/autoinvoke: true
---

# Linting and Formatting

Two jobs with different failure modes. Formatting is mechanical and should be
uncontroversial; linting encodes judgement and needs a deliberate rule set. Keep them on
separate commands so a format change never blocks a lint discussion.

Verify before relying on this: ESLint configuration has changed shape between majors, and
Biome changed its configuration schema and rule-group model in v2. Check the tool's own
configuration reference for the version in your lockfile before asserting a key name.

## When to use this

- Linting or formatting fails locally but passes in CI, or the reverse.
- A rule fires constantly and the team has started suppressing it.
- A config change broke a whole repository and the error message is unclear.
- Setting up linting, formatting, or pre-commit hooks on a new project.
- Linting is slow enough that developers skip it.

## Quick start

A minimal, correct setup for TypeScript with ESLint, a formatter, and a pre-commit hook.

```json
{
  "scripts": {
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "typecheck": "tsc --noEmit",
    "prepare": "husky"
  },
  "devDependencies": {
    "@eslint/js": "9.14.0",
    "eslint": "9.14.0",
    "globals": "15.12.0",
    "husky": "9.1.6",
    "lint-staged": "15.2.10",
    "prettier": "3.3.3",
    "typescript": "5.6.3",
    "typescript-eslint": "8.14.0"
  }
}
```

```javascript
// eslint.config.js
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig(
  globalIgnores(["dist/**", "build/**", "coverage/**", "node_modules/**"]),

  js.configs.recommended,
  tseslint.configs.recommended,

  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { projectService: true },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": "error",
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },

  {
    files: ["**/*.test.ts"],
    languageOptions: { globals: { ...globals.node } },
    rules: { "no-console": "off" },
  },
);
```

```json
{
  "lint-staged": {
    "*.{js,ts,tsx}": ["eslint --fix", "prettier --write"],
    "*.{json,md,yml,yaml}": ["prettier --write"]
  }
}
```

```bash
npx husky init
# .husky/pre-commit, no shebang needed in recent husky versions
npx lint-staged
```

Confirm the current hook bootstrap procedure for your husky version; the recommended
`husky init` flow changed between majors, and `npm run prepare` wiring differs from a
global install approach.

## Flat config, not the legacy format

ESLint's current configuration is a flat config file: an array of configuration objects,
each of which may carry `files`, `ignores`, `languageOptions`, `plugins`, `rules`, and
`linterOptions`. Later objects override earlier ones.

The legacy `.eslintrc` format with `extends` and `overrides` is legacy configuration.
Migrate rather than mixing. Two files with different formats, or a leftover
`.eslintrc` picked up alongside `eslint.config.js`, is a common cause of "the same rule
behaves differently in two places".

Key rules of flat config:

- `ignores` alone in an object means global ignores. Combined with other keys such as
  `rules`, it means scoped ignores for that object only. Use the `globalIgnores` helper
  when you mean global.
- Global ignore patterns match directories with a trailing slash, `dist/`. Scoped ignores
  match file paths, `dist/**`.
- A configuration object with no `files` applies to everything another object matches.
- ESLint searches for the config from the file's directory upward, so a monorepo package
  can have its own config.
- Config files can be `eslint.config.js`, `.mjs`, `.cjs`, or `.ts`. TypeScript config
  files need extra setup on Node; check the current documentation.

```javascript
// eslint.config.mjs
import js from "@eslint/js";
import { defineConfig } from "eslint/config";

export default defineConfig([
  {
    files: ["**/*.js", "**/*.cjs", "**/*.mjs"],
    plugins: { js },
    extends: ["js/recommended"],
    rules: {
      semi: ["error", "always"],
      eqeqeq: ["error", "smart"],
    },
  },
]);
```

## Rule selection

Enable the recommended set, add a small number of rules that catch real bugs, and leave
style to the formatter.

| Category | Example intent | Recommended |
| --- | --- | --- |
| Correctness | Unreachable code, forgotten promises, invalid regex, lost awaits | Yes |
| Security | Unsafe regular expression, object injection into a prototype chain | Yes, selectively |
| Suspicious | Implicit fallthrough, unused expressions | Yes |
| Type-aware | Floating promises, unsafe `any`, misused promises | Yes on TypeScript, with the performance cost |
| Style | Quotes, semicolons, indentation, member ordering | No. That is the formatter's job |

Enabling "all" rules is not thorough, it is unusable. A rule set that produces 400
warnings on day one produces 400 suppressions by week two and no signal thereafter.

### Rules that earn their keep

Commonly worth enabling in a TypeScript service:

```javascript
rules: {
  // Type-aware correctness, from the type-checked configs.
  "@typescript-eslint/no-floating-promises": "error",
  "@typescript-eslint/no-misused-promises": "error",
  "@typescript-eslint/await-thenable": "error",

  // Security-relevant.
  "no-eval": "error",
  "no-implied-eval": "error",

  // Bug classes.
  eqeqeq: ["error", "always"],
  "no-self-compare": "error",
  "no-return-await": "off",
  "require-atomic-updates": "off",
}
```

Check each rule's current documentation for its exact name, options, and behaviour in the
installed version before adding it. Rule names and semantics have changed across major
versions, and a rule that does not do what you assumed is worse than no rule.

### Handling unavoidable violations

Prefer, in order:

1. Fix the code.
2. Refactor so the pattern disappears.
3. Disable the rule for a specific file with a comment and a reason.
4. Configure an exception for a pattern that is legitimately allowed, with a comment.

```typescript
// Reason: the platform throws on a synchronous call in this path; the throw is handled
// by the caller's try/catch and turning it into a rejection would change behaviour.
```

Do not turn a rule off globally to silence one file.

## Type-aware linting

Type-aware rules need the type information from a TypeScript program, which makes them
slower and requires the files to be in a project.

```javascript
// eslint.config.js
import tseslint from "typescript-eslint";

export default [
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ["**/*.ts"],
    languageOptions: {
      parserOptions: {
        // Use the nearest tsconfig.json for each file. Faster and more accurate than a
        // single project reference for a multi-package repo.
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
];
```

Costs and constraints:

- Noticeably slower than syntax-only linting. Measure before enabling it across a large
  repository, and consider a two-tier approach: syntax-only on save, type-aware on
  commit and in CI.
- Files must be included in a `tsconfig.json`, or you get a "file not found in project"
  error. Exclude generated files from both the tsconfig and the lint run.
- Type-aware rules will not help if the project is not actually type-checked, or if
  `strict` is off enough that the types carry no information.

Run the type checker separately. Lint does not replace `tsc --noEmit`, and a project with
`any` everywhere gets very little from type-aware rules.

## Biome

Biome is a single tool that formats and lints, written in Rust. It replaces the
ESLint-plus-Prettier pairing when its rule coverage is sufficient for the project.

```jsonc
// biome.jsonc
{
  "$schema": "./node_modules/@biomejs/biome/configuration_schema.json",
  "files": {
    "includes": ["**", "!!**/dist", "!!**/node_modules"]
  },
  "formatter": {
    "enabled": true,
    "indentStyle": "space",
    "indentWidth": 2,
    "lineWidth": 100
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true,
      "suspicious": {
        "noExplicitAny": "error",
        "noConsole": "warn"
      },
      "style": {
        "useConst": "error"
      }
    }
  },
  "javascript": {
    "formatter": {
      "quoteStyle": "single",
      "semicolons": "asNeeded"
    }
  }
}
```

Notes on the schema, which changed significantly in Biome v2:

- `files.includes` replaced the earlier ignore-style arrays; negated entries force
  exclusion. Check the v2 reference for the exact glob semantics rather than carrying a
  v1 pattern across.
- `linter.rules.recommended` is superseded by a preset model. Use the group and rule
  structure shown above and verify against the configuration reference for your version.
- `overrides` uses `includes` rather than `files` to select which files it applies to.
- Run `biome migrate` to update a v1 configuration, or `biome init --jsonc` to scaffold
  a new one.

Advantages: one binary, one configuration, fast enough that on-save linting is
realistic. Limitations: fewer rules than ESLint, and no equivalent to ESLint's deep plugin
ecosystem or type-aware rules. Choose it when its coverage meets your needs and accept
that some rules will be missing.

## Prettier

```json
{
  "semi": true,
  "singleQuote": false,
  "printWidth": 100,
  "tabWidth": 2,
  "trailingComma": "all"
}
```

Key point: a formatter is not negotiable per file. If a file can opt out, the diff noise
returns. Configure `ignorePatterns` for generated files and vendored directories, not for
files that are inconvenient.

Editor integration:

```json
// .vscode/settings.json (repository level, committed)
{
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "editor.formatOnSave": true,
  "[typescript]": { "editor.defaultFormatter": "esbenp.prettier-vscode" },
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit"
  }
}
```

This is the single highest-leverage configuration in the whole area. Format on save
removes formatting from diffs, from review, and from arguments about style.

## Pre-commit hooks

```bash
npx husky init
npx husky add .husky/pre-commit
```

`.husky/pre-commit` should be short. Linting staged files only, with lint-staged, keeps
the hook under a second or two. Running a full repository lint on every commit trains
developers to use `--no-verify`, which disables the control entirely.

```json
{
  "lint-staged": {
    "*.{ts,tsx}": ["eslint --fix", "prettier --write"],
    "*.{js,cjs,mjs}": ["eslint --fix", "prettier --write"],
    "*.{json,css,html,md,yml,yaml}": ["prettier --write"]
  }
}
```

Use `--fix` in the hook so the developer's committed code is already clean. If a rule
cannot auto-fix, the commit is blocked with the remaining error, which is the intended
behaviour.

A type check is usually too slow for a pre-commit hook. Put it in a pre-push hook or in
CI.

## CI

CI is the authority. Hooks are a convenience that people can bypass.

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
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm run format:check
      - run: npm run typecheck
```

Split lint from typecheck into separate jobs so a type error does not hide a lint error.
Cache the dependency install. Pin the Node version.

Check the configuration file itself in CI. A config that fails to load can silently lint
nothing in some tools, and a green build that linted zero files is worse than a red one.

## Monorepo lint configuration

Two shapes:

1. One root config with per-directory overrides, using `basePath` to scope each object.
   Simple, one install, one version.
2. A base config package extended by each package. Explicit, independently versioned, one
   extra indirection.

For a moderate monorepo, shape one. For Biome, a nested configuration extends the root
with the string `"//"`, which resolves the project root at any nesting depth.

```javascript
// eslint.config.js
import globals from "globals";
import tseslint from "typescript-eslint";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig(
  globalIgnores(["**/dist/**", "**/build/**", "**/node_modules/**"]),
  tseslint.configs.recommended,
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
);
```

Full per-directory configs, Biome nested configurations, affected-package detection, and
the editor settings that keep local and CI in agreement are in
[references/monorepo-lint.md](references/monorepo-lint.md).

## Custom rules

Write one when a bug pattern recurs and no built-in rule covers it. A worked example, a
`RuleModule` implementation, and the plugin wiring are in
[references/custom-rules.md](references/custom-rules.md).

Every custom rule needs a `meta.description`, a schema, a stable `messageId` rather than
an inline string, a test, and an autofix only when the fix is provably safe. An unfixable
rule that fires on legitimate code is worse than no rule, because the only response is a
suppression.

## Checklist

- One formatter, configured in a committed file, applied on save.
- ESLint flat config, with no leftover legacy configuration.
- Global ignores declared with the `globalIgnores` helper, matching directories.
- Recommended rules on; style rules delegated to the formatter.
- Every non-default rule is justified in a comment near its configuration.
- Type-aware rules only where the project is genuinely type-checked.
- No rule disabled globally to accommodate one file.
- Unused disable directives are reported, so suppressions do not accumulate.
- Pre-commit runs lint-staged on staged files only, with `--fix`.
- CI runs lint, format check, and typecheck as separate jobs.
- Linting actually lints: the CI log shows a non-zero file count.
- Monorepo has one shared config with per-directory overrides.

## Common mistakes

- **Mixed config formats.** Symptom: rules apply in some directories and not others.
  Cause: both `.eslintrc` and `eslint.config.js` present. Fix: delete the legacy file.
- **Global ignore written as a scoped one.** Symptom: a directory is still linted.
  Cause: `ignores: ["dist"]` combined with other keys does not match the directory the
  way `globalIgnores(["dist/"])` does. Fix: use the helper for global scope.
- **Rules pass on Node, fail in CI.** Symptom: "environment not defined" for a browser
  global, or the reverse. Cause: globals configured per directory only in the local
  config. Fix: declare globals in the config, not in an editor settings file.
- **Lint passes on zero files.** Symptom: green CI, no output. Cause: `files` patterns
  that match nothing, or the tool lints only default extensions. Fix: log the file count
  and assert a minimum in CI.
- **Lint is too slow, so hooks get bypassed.** Fix: staged files only, syntax-only rules
  on save, type-aware rules on commit and CI.
- **Rule fired 400 times, team suppressed it everywhere.** Root cause: the rule was
  enabled without reading the existing codebase. Fix: enable, fix, then enforce.
- **Editor and CI disagree.** Symptom: a file reformatted in CI that the editor left
  alone. Cause: different formatter settings. Fix: one committed config, and check the
  editor is using it.
- **Formatting blocked by a syntax error.** Fix: the formatter gives up on unparseable
  files, and often emits nothing useful. Run the type checker or parser first for the
  diagnostic.

## Anti-patterns

- Enabling every rule from a "recommended plus everything" preset on day one.
- Disabling rules in an editor settings file rather than in the shared config, so the
  behaviour is invisible to everyone else.
- Pre-commit hooks that run the full suite and take minutes.
- A formatter that files can opt out of.
- Relying on hooks as the enforcement mechanism. They are bypassable by design.
- Custom rules without tests, or with an autofix that changes behaviour.
- Accepting lint warnings indefinitely in CI, so the warning count becomes meaningless.
- Two formatters configured at once, with different opinions.

## References

- ESLint configuration files: https://eslint.org/docs/latest/use/configure/configuration-files
- ESLint rule reference: https://eslint.org/docs/latest/rules/
- typescript-eslint: https://typescript-eslint.io/
- Biome configuration: https://biomejs.dev/reference/configuration/
- Biome getting started: https://biomejs.dev/guides/getting-started/
- Prettier options: https://prettier.io/docs/options/
- lint-staged: https://github.com/lint-staged/lint-staged
- husky: https://typicode.github.io/husky/