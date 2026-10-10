# ESLint flat config reference

Verify keys and rule names against the ESLint and plugin documentation for the version
you install. This file covers the concepts; the docs cover the exact surface.

- Configuration files: https://eslint.org/docs/latest/use/configure/configuration-files
- Rules: https://eslint.org/docs/latest/rules/
- Custom rules: https://eslint.org/docs/latest/extend/custom-rules
- Migrating to flat config: https://eslint.org/docs/latest/use/configure/migration-guide

## Shape

`eslint.config.js`, `.mjs`, `.cjs`, or `.ts` at the project root, exporting an array of
configuration objects. ESLint searches from the target file's directory upward until it
finds a config file, so a monorepo package can carry its own.

```javascript
// eslint.config.js
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  // Object with only `ignores` (and optionally `name`) is a global ignore set.
  globalIgnores(["dist/", "coverage/", "**/*.snap"]),

  // Later objects override earlier ones for the keys they set.
  {
    name: "project/base",
    files: ["**/*.{js,cjs,mjs,ts,cts,mts,tsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
    },
    linterOptions: {
      // Turn a stale suppression into a warning so it gets cleaned up.
      reportUnusedDisableDirectives: "warn",
      // Allow nobody to silence a rule with an inline comment.
      // noInlineConfig: true,
    },
    rules: {},
  },
]);
```

## Object keys

| Key | Meaning |
| --- | --- |
| `name` | Identifier used in messages and in the config inspector. Give every object one. |
| `basePath` | Subdirectory this object applies to, relative to the config file. |
| `files` | Glob patterns selecting the files this object applies to. |
| `ignores` | Patterns to exclude. Global when the object has no other keys. |
| `extends` | Strings, objects, or arrays to merge in. |
| `language` | Language plugin id, for non-JavaScript languages. |
| `languageOptions` | Parser, `ecmaVersion`, `sourceType`, `globals`, parser options. |
| `linterOptions` | `noInlineConfig`, `reportUnusedDisableDirectives`, `reportUnusedInlineConfigs`. |
| `plugins` | Plugin objects keyed by a short name. |
| `rules` | Rule severities and options. |
| `settings` | Shared values passed to rules. |
| `processor` | Pre and post processing, for non-JavaScript files. |

## Rules of precedence

- Objects matching the same file are merged, later winning.
- `files` and `ignores` use minimatch-style patterns evaluated relative to the config
  file location.
- Global `ignores` patterns may match directories with a trailing slash. Scoped
  `ignores` match file paths, so use `dist/**` rather than `dist/`.
- ESLint's defaults lint `**/*.js`, `**/*.cjs`, `**/*.mjs`. To lint other extensions,
  name them in a `files` array.
- A bare `files: ["**"]` behaves differently from naming extensions; prefer explicit
  extensions.

## Glob patterns

```javascript
files: [
  "src/**/*.ts",       // TypeScript under src
  "**/*.{ts,tsx}",      // TypeScript anywhere
  ["tests/**", "**/*.ts"], // AND: inside tests AND TypeScript
  "**/!(*.*)",          // files with no extension
]
```

## Globals

```javascript
import globals from "globals";

languageOptions: {
  globals: {
    ...globals.browser,
    ...globals.node,
  },
}
```

The `globals` package is the supported way to declare environment globals. Declaring
globals in an editor settings file rather than in the config is a common cause of a rule
that passes locally and fails in CI.

## Typescript-eslint

```javascript
import tseslint from "typescript-eslint";

export default [
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];
```

Type-checked configurations exist for the type-aware rule set; check the plugin's current
configuration names rather than assuming. The practical requirements are the same:

- `parserOptions` must point at a project so the rule can resolve types.
- Files must be inside a `tsconfig.json`. Excluded or generated files error out, so keep
  the lint run and the tsconfig consistent.
- `tsconfigRootDir` must be an absolute path; `import.meta.dirname` or `__dirname` works.
- Enable only where the code is type-checked. Type-aware rules on a project full of
  `any` are slow and uninformative.

## Plugins

```javascript
import examplePlugin from "eslint-plugin-example";

export default [
  {
    files: ["src/**/*.js"],
    plugins: { example: examplePlugin },
    extends: ["example/recommended"],
    rules: {
      "example/no-thing": "error",
    },
  },
];
```

Use `extends` for a plugin's shipped configuration so you get its parser, plugins, and
rule severities together. Reach for `plugins.configs.recommended` directly only when you
need to filter which parts you apply.

## Import ordering

The ecosystem's import-ordering solution is moving. Check the current documentation for
whichever plugin you use before writing a long import-order configuration, because this
area has changed repeatedly and a stale configuration is worse than none. A simple
alternative that needs no plugin is the built-in `sort-imports` rule with
`ignoreDeclarationSort` off, accepting that it is not as thorough.

## Suppressions

```typescript
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- reason here
```

Requirements for a suppression to be acceptable:

- It names the rule.
- It has a reason.
- It is scoped to the smallest region.
- Unused disable directives are reported, so stale ones get removed.

Turning on `reportUnusedDisableDirectives` is what stops the suppression list from
growing forever. It defaults to `"warn"`; raise it to `"error"` when you want it to block.

## Debugging a configuration problem

```bash
# Print the resolved configuration for a file.
npx eslint --print-config src/index.ts

# Show the lint result for one file without writing anything.
npx eslint src/index.ts --no-fix --format json

# See which files ESLint actually linted.
npx eslint . --format json | node -e "
  let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{
    const r=JSON.parse(s);
    console.log('files linted:', r.length);
    if (r.length === 0) console.log('WARNING: nothing was linted');
  });
"
```

If `--print-config` shows rules you did not set, a plugin config is contributing them.
If it shows rules you did set as absent, a later object or a scoped ignore removed them.

The `eslint/config-inspector` project provides a browser view of which config object
supplies which rule for a given file. It is the fastest way to understand a config that
has grown organically.

## Performance

- Keep type-aware rules out of the editor's on-save path if they are slow. Run them on
  commit and in CI.
- Scope expensive rule sets with `files` instead of applying them globally.
- Large shareable config arrays are a common cause of slow startup. Flatten them.
- If a specific dependency is slow to lint, ignore it. Linting your dependencies is not
  your job.

## Migration from legacy config

The migration tool exists and the documentation covers the mapping. Points worth knowing
before you run it:

- `.eslintrc.json`, `extends`, and `overrides` become array objects.
- Plugin names gain a prefix in rule names; plugins move into a `plugins` object.
- Environments become `languageOptions.globals`, usually via the `globals` package.
- `--ext` is gone. List the extensions in `files`.
- Processor configuration moves to a `processor` key or a dedicated plugin config.

Migrate the whole repository at once. A half-migrated config is the worst state to debug.