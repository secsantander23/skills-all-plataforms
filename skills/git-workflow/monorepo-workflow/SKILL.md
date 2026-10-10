---
name: Monorepo Workflow
description: Covers monorepo layout conventions, affected-only builds, workspace package managers such as npm, pnpm, and Yarn, task orchestration and caching with Nx, Turborepo, Bazel, or Rush, dependency graph management and version bumping with Changesets or Lerna, CODEOWNERS ownership, and path-filtered CI. Use when the user asks how to structure a monorepo, speed up a slow monorepo CI, set up affected-only builds, manage internal package versions, or migrate to or from a monorepo.
metadata:
  opencode/autoinvoke: true
---

# Monorepo Workflow

A monorepo trades repository-count problems for graph problems. You gain atomic cross-package
commits, one CI config, and one place to change a shared type. You inherit a dependency graph,
cross-package test orchestration, and version coordination. This skill is about keeping that
trade favorable.

## When to use this

- Deciding whether to merge several repositories into one.
- Setting up a workspace with npm, pnpm, Yarn, or Bun.
- Making CI faster: affected-only builds, task caching, parallel execution.
- Bumping an internal package without breaking its consumers.
- Assigning ownership per directory with CODEOWNERS.
- Splitting a monorepo back apart, or on-boarding a team into an existing one.

## Quick start

A layout that works for most TypeScript/JavaScript teams:

```text
repo-root/
  package.json               # private, declares workspaces
  pnpm-workspace.yaml        # package globs
  nx.json                    # or turbo.json
  tsconfig.base.json         # shared compiler options, referenced by every package
  apps/
    web/                     # deployable
    admin/
  packages/
    ui/                      # published or shared library
    config-typescript/
    config-lint/
    utils/
  tooling/
    scripts/
  .github/
    CODEOWNERS
    workflows/
      ci.yml
```

Root `package.json`:

```json
{
  "name": "acme",
  "private": true,
  "packageManager": "pnpm@9.0.0",
  "workspaces": ["apps/*", "packages/*"],
  "scripts": {
    "build": "nx affected -t build",
    "test": "nx affected -t test",
    "lint": "nx run-many -t lint"
  }
}
```

`pnpm-workspace.yaml`:

```yaml
packages:
  - "apps/*"
  - "packages/*"
```

## Workspace package managers

| Manager | Declared in | Run in all | Run in one | Notes |
| --- | --- | --- | --- | --- |
| npm | `workspaces` in root `package.json` | `npm run build --workspaces` | `npm run build -w @acme/ui` | Ships with Node; symlinks `node_modules` |
| pnpm | `pnpm-workspace.yaml` | `pnpm -r run build` | `pnpm --filter @acme/ui run build` | Strict, symlinked `node_modules`; fastest installs |
| Yarn (Berry) | `workspaces` in root `package.json` | `yarn workspaces foreach run build` | `yarn workspace @acme/ui run build` | Plug'n'Play available; check your `.yarnrc.yml` |
| Bun | `workspaces` in root `package.json` | `bun run --filter '*' build` | `bun run --filter @acme/ui build` | Verify syntax against Bun docs before adopting |

pnpm filter selectors are worth internalizing — they express the graph directly:

```bash
pnpm --filter @acme/ui run build     # only that package
pnpm --filter @acme/ui... run build  # that package and everything it depends on (dependencies)
pnpm --filter ...@acme/ui run build  # that package and everything that depends on it (dependents)
pnpm -r --filter './packages/**' run build
pnpm --filter @acme/ui... --filter ...@acme/core run test
```

Confirm selector behavior against your package manager's documentation; the names vary slightly
between npm, pnpm, and Yarn.

## Task orchestration and caching

The reason a monorepo CI is slow is that every push runs every package's build, lint, and test.
Orcuts exists to compute which packages changed and which must be rebuilt downstream.

### Nx

```bash
npx nx graph                       # open the project dependency graph in a browser
npx nx affected -t test            # run `test` for packages affected by the current changes
npx nx affected -t build --base=main
npx nx run-many -t lint --all
npx nx affected -t test --exclude='**/e2e/**'
npx nx reset                       # clear the local cache
```

`nx.json`:

```json
{
  "$schema": "./node_modules/nx/schemas/nx-schema.json",
  "affected": {
    "defaultBase": "main"
  },
  "targetDefaults": {
    "build": {
      "cache": true,
      "inputs": ["default", "^default", "production"]
    },
    "test": {
      "cache": true,
      "inputs": ["default", "^default"]
    },
    "lint": {
      "cache": true
    }
  }
}
```

Nx derives the graph either from explicit `project.json` files or from the package manager's
workspace layout plus source imports. Prefer inferred targets when the project is young;
prefer explicit `project.json` for anything with non-obvious inputs (custom build inputs,
generated files, environment-dependent configuration).

### Turborepo

```bash
npx turbo run build --filter=...[origin/main]   # changed packages + dependents
npx turbo run build --filter=@acme/ui...        # that package and its dependencies
npx turbo run test --dry=json                  # print the task graph as JSON without running
npx turbo run build --graph=                   # emit the task graph in Mermaid format
```

`turbo.json`. Note that the top-level task key was named `pipeline` in early versions and
`tasks` in later ones; check the schema URL for your installed version and match it:

```json
{
  "$schema": "https://turbo.build/schema.json",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "outputs": ["dist/**"]
    },
    "test": {
      "dependsOn": ["build"],
      "outputs": []
    },
    "lint": {
      "dependsOn": ["^build"]
    }
  }
}
```

`dependsOn: ["^build"]` means "build my dependencies first"; `"build"` means "build myself
first". Forgetting `^` is a top cause of flaky CI in Turborepo, because the cache key no longer
reflects the inputs of dependency packages.

### Bazel

Bazel models the graph explicitly as targets. Modern Bazel uses `MODULE.bazel` (bzlmod) for
dependency management; `WORKSPACE` is the older mechanism. Verify which your version expects
before writing module definitions.

```bash
bazel build //apps/web:web
bazel test //packages/...
bazel query 'kind("go_binary|nodejs_binary", //...)'
bazel clean --expunge
```

Bazel gives the strongest hermeticity and cross-language support in this list, at the cost of
the steepest learning curve.

### Rush

Rush (Microsoft) is a stricter orchestrator on top of pnpm: a single Rush-managed lockfile,
centralized policy through a Rush configuration file, and a two-step install that enforces
consistent dependency versions.

```bash
rush build            # build everything in dependency order
rush change           # record a change file, like a Changeset
rush publish          # publish changed packages
rush update           # re-resolidate the lockfile
```

Choose Rush when a large team needs enforced policies more than they need flexibility.

## Affected-only builds

The concept: given a base ref, compute the set of changed projects plus the transitive closure
of their dependents, and run tasks only there.

```bash
# Turborepo: three dots after the filter means "changed since this ref, plus dependents"
npx turbo run test --filter=...[origin/main]

# Nx: base ref from config or explicit
npx nx affected -t test --base=origin/main

# What would run, without running it (debugging a wrong affected set)
npx turbo run test --filter=...[origin/main] --dry=json
npx nx show projects --affected --base=origin/main
```

Common causes of a wrong affected set:

- A package imported but not declared as a dependency, so the graph edge is missing. Fix by
  adding the dependency; do not hand-patch the graph.
- A tool that reads build outputs rather than sources, so a stale `dist/` looks like a change.
  Declare precise `inputs`.
- `--base` pointing at a ref that does not exist in a shallow CI clone. Fetch the full history
  or set an explicit depth that includes the base.
- A `dependsOn` chain without `^`, so dependent packages are not rebuilt.

## Versioning internal packages

Two questions must be answered explicitly: who bumps internal versions, and do consumers get
exact or range dependencies?

### Independent versioning with Changesets

Each package gets its own version. A changeset declares the bump:

```md
---
"@acme/ui": minor
---

Add a `Toggle` component and export it from the package entrypoint.
```

```bash
npx changeset status             # what is pending
npx changeset version            # apply bumps, update CHANGELOG.md, update dependent ranges
npx changeset publish            # publish the changed packages
```

When `@acme/core` bumps and `@acme/ui` depends on it, `changeset version` rewrites the range in
`@acme/ui`'s `package.json` according to `updateInternalDependencies` in `.changeset/config.json`.

### Fixed and linked groups

```json
{
  "changelog": "@changesets/cli/changelog",
  "commit": false,
  "fixed": [["@acme/core", "@acme/ui"]],
  "linked": [],
  "access": "public",
  "baseBranch": "main",
  "updateInternalDependencies": "patch"
}
```

- `fixed`: the packages always move to the same version together and are always published
  together. Use when they are released as a unit or share a breaking-change surface.
- `linked`: packages share a version *when they are released*, but an unaffected package is
  not force-bumped. Use when most move together but some lag.

Independent versioning is the default because it produces the least churn. Fixed versioning is
sometimes required by consumers who install both packages and assume aligned versions.

### Lerna

```bash
npx lerna version --conventional-commits   # compute bumps from commits, write CHANGELOG.md
npx lerna publish from-package             # publish packages whose version changed
npx lerna changed                          # print packages changed since the last release
```

Lerna's fixed versus independent mode is configured in `lerna.json` (`"version": "independent"`).

### Private internal packages

Decide explicitly whether internal packages are published:

- **Never published** (`"private": true`): version stays `0.0.0` or `1.0.0` and never changes;
  consumers resolve them through the workspace. Simplest.
- **Published but unstable**: version changes without a changelog entry.
- **Published and supported**: they are real products with release notes and support windows.

Mixing these is where monorepo release automation gets confusing. Write the policy down.

## Ownership and CODEOWNERS

`.github/CODEOWNERS`:

```text
# Default owners for everything
*                       @acme/engineering

# Directories
/apps/web/              @acme/web-team
/apps/api/              @acme/backend-team @acme/security
/packages/ui/           @acme/design-systems

# Specific files
/packages/config-lint/  @acme/platform
/docs/runbooks/         @acme/sre

# Ignoring files (reviewers are still requested, but not mandatory)
**/testdata/**          @acme/qa
**/*.generated.ts       @acme/platform
```

Rules, in order of precedence: later patterns override earlier ones, the last matching pattern
wins, and `*` at the start matches every file. CODEOWNERS is looked for in
`.github/CODEOWNERS`, `docs/CODEOWNERS`, then `CODEOWNERS` at the repository root.

CODEOWNERS requests reviewers; it does not by itself require them. Pair it with branch
protection rules so that required reviews actually block a merge.

## Path-based CI

```yaml
name: ci
on:
  pull_request:
    branches: [main]

jobs:
  affected:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0    # the affected computation needs history to find the base
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'pnpm'
      - run: pnpm install --frozen-lockfile
      - run: pnpm exec turbo run build test --filter=...[origin/main]
```

For workflows that should not run at all on irrelevant changes, filter by path and always
remember that order matters — later patterns are ANDed with earlier ones, so you may need
`!**/docs/**` to re-include files a broad exclusion excluded:

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'apps/**'
      - 'packages/**'
      - '!packages/legacy-fixture/**'
```

`fetch-depth: 0` is the most common fix for "affected detection says nothing changed" — a
shallow clone has no merge base.

## Migrating in and out

### Several repos into one

1. Inventory: list every package's build, test, lint, and publish commands. If a repo cannot
   build standalone, fix that first — it will not get easier inside the monorepo.
2. Establish a shared `tsconfig.base.json`, formatter, and linter config.
3. Move code with history preserved:

```bash
git remote add other-repo <url>
git fetch other-repo
git rebase --onto main $(git merge-base main other-repo/main) other-repo/main
```

4. Rewrite import paths to workspace package names and add the dependencies to each manifest.
5. Convert CI configs into per-package scripts; delete the old CI.
6. Add CODEOWNERS and path filters last, once the build is green.

### One monorepo out to several

Split with `git filter-repo` (a separate tool; see its docs) or `git subtree split`:

```bash
git subtree split --prefix=packages/ui -b extract/ui
```

`git filter-repo --path packages/ui --path-ignore packages/ui/dist` is the more surgical option
but requires installing it. Neither preserves the monorepo's CI or publishing setup — that is
rework, not a free operation.

### The tell that a split is right

A package that nobody else depends on, changes on a different cadence, and has a different
release audience. Everything else probably wants to stay together.

## Checklist

- [ ] Root manifest is `"private": true` for a source-only monorepo.
- [ ] Workspace globs cover every package, and no package sits outside them.
- [ ] Shared compiler/lint/format config is extended, not copied.
- [ ] Every inter-package dependency is declared in a manifest, not just imported.
- [ ] CI runs affected-only with a full-enough history depth.
- [ ] Cache hits are real: change one package and confirm dependents rebuild.
- [ ] Versioning policy (independent, fixed, or private) is written down and configured.
- [ ] CODEOWNERS maps every top-level directory to a team.
- [ ] Lockfile changes are reviewed: an unexplained mass bump is a supply-chain event.

## Common mistakes

**`node_modules` committed at the root.** Symptoms: stale imports that resolve to the built
output, and CI differences between machines. Never commit `node_modules`; gitignore it.

**A cycle between packages.** Symptom: Nx or Turborepo reports a circular dependency and either
errors or silently skips a task. Break the cycle by extracting the shared types into a
dependency-free package.

**Stale build outputs making the affected set wrong.** Clean before computing affected, or
declare `inputs` so outputs are excluded from the hash.

**`--filter` with no `--filter=...` dependents suffix.** Everything downstream silently skips
your change and tests the old code. Use `--filter=...[base]`.

**Independent versions with `^` ranges and no changeset.** A breaking change to `@acme/ui`
bumped as a patch; `@acme/web` resolved the old major. Fix the range, then bump the major.

**One giant `CODEOWNERS` team for everything.** Reviews queue behind one group. Map real
directories to real teams.

**Adding a repository-level dependency that individual packages import.** The graph has no edge,
so the affected calculation misses it. Declare it in each package that uses it.

## Anti-patterns

- **Modular monolith in name only**: every package depends on every other package "just in
  case". That is one package with extra steps. Enforce the graph in CI with
  `nx enforce-module-boundaries` (a real Nx lint rule) or an equivalent check.
- **A `utils` package that everything imports.** It becomes a junk drawer and a serialization
  point. Split by domain.
- **Running the full matrix on every push.** Use affected-only plus a nightly full run.
- **Version bumps by hand in a monorepo.** Merge conflicts in `package.json` and missed
  dependents. Let Changesets or Lerna do it.
- **Depending on `workspace:*` or `workspace:^` and publishing without replacing them.** Verify
  your package manager rewrites protocol ranges before publish; npm and pnpm do, but verify for
  your tool.
- **Treating the monorepo as a single deployable.** Independent deployability per app is the
  point of separate packages.
- **Bypassing the lockfile in CI.** `--frozen-lockfile` (pnpm) and `npm ci` are what make CI
  reproducible; without them your monorepo is not.

## References

- Nx documentation: https://nx.dev/
- Nx project graph and affected: https://nx.dev/ci/features/affected
- Nx cache configuration: https://nx.dev/ci/features/other/ci-configuration
- Turborepo documentation: https://turbo.build/repo/docs
- Turborepo filtering: https://turbo.build/repo/docs/core-concepts/filtering
- Turborepo `turbo.json` reference: https://turbo.build/repo/docs/reference/configuration
- Bazel concepts: https://bazel.build/concepts
- Rush documentation: https://rushjs.io/
- pnpm workspaces: https://pnpm.io/workspaces
- pnpm filtering: https://pnpm.io/filtering
- npm workspaces: https://docs.npmjs.com/cli/using-npm/workspaces
- Yarn workspaces: https://yarnpkg.com/features/workspaces
- Changesets: https://github.com/changesets/changesets
- Lerna: https://lerna.js.org/
- GitHub CODEOWNERS: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- GitHub Actions workflow syntax (including `paths` filters): https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions
- Git subtree split: https://git-scm.com/docs/git-subtree