---
name: package-managers
description: Covers JavaScript package managers and dependency management, including npm, pnpm, and yarn workspaces, lockfiles and reproducible installs, peer versus dev versus regular dependencies, version ranges and pinning, peer dependency conflicts, audit and supply chain basics, monorepo task runners such as Nx and Turborepo, and pruning development dependencies from production images. Use when the user mentions package.json, node_modules, lockfile, npm or pnpm or yarn, a dependency conflict, or a production image that is too large.
metadata:
  opencode/autoinvoke: true
---

# Package Managers and Dependency Management

A package manager decides three things: what version of a package to install, where it
lands on disk, and which of your scripts run. Most dependency pain is one of those three
going wrong, so diagnose which before changing anything.

Verify before relying on this: package managers move settings between files and change
default behaviour between major versions. pnpm now reads most settings from
`pnpm-workspace.yaml` rather than from the `pnpm` key in the root `package.json`. Confirm
with the manager's own settings reference for the version you use.

## When to use this

- `npm install` behaves differently from `pnpm install` on the same repository.
- A lockfile is out of date, missing, or produces different trees on two machines.
- A peer dependency conflict blocks install.
- A production image contains development dependencies.
- You are choosing between npm, pnpm, and yarn for a new repository.

## Quick start

A root manifest that is correct about dependency kinds and about what CI does.

```json
{
  "name": "@acme/web",
  "private": true,
  "type": "module",
  "engines": { "node": ">=20.11" },
  "packageManager": "pnpm@10.0.0",
  "scripts": {
    "build": "tsc --build",
    "test": "vitest run",
    "lint": "eslint .",
    "ci": "pnpm run lint && pnpm run test && pnpm run build"
  },
  "dependencies": {
    "react": "18.3.1",
    "zod": "3.23.8"
  },
  "devDependencies": {
    "typescript": "5.6.3",
    "vitest": "2.1.4",
    "eslint": "9.14.0"
  }
}
```

```bash
# Developer machine and CI both install exactly the locked tree.
pnpm install --frozen-lockfile
```

Pin the package manager with the `packageManager` field so corepack selects the version
your lockfile was written by. Replace the example version with the one your repository
actually uses; the field exists to be exact.

## Dependency kinds

| Kind | Installed for consumers | Installed for you | Use for |
| --- | --- | --- | --- |
| `dependencies` | Yes | Yes | Code your package imports at runtime |
| `devDependencies` | No | Yes | Build tools, test runners, linters, types |
| `peerDependencies` | Not installed | Not installed | A capability the consumer must supply |
| `optionalDependencies` | Yes, failure tolerated | Yes | Platform-specific enhancements |

The mistake teams make is putting libraries in `devDependencies` because the app installs
them. If a published package's runtime code imports `lodash`, `lodash` belongs in
`dependencies`. Missing from `dependencies` means consumers get a module-not-found error
at runtime, which no CI job on your machine catches.

Peer dependencies express a host requirement without taking control of the version. The
consumer decides which version satisfies the range, so a library that requires
`react@^18` declares it as a peer. Peer dependencies are not installed by the consumer
automatically in all setups, which is why some managers offer an auto-install-peers
setting. Turn it on deliberately and understand what it changes.

## Version ranges

| Range | Meaning | Use when |
| --- | --- | --- |
| `1.2.3` | Exactly that version | Applications, and anything you want reproducible without a lockfile |
| `^1.2.3` | Compatible-with, so `>=1.2.3 <2.0.0` | Libraries that are confident about minor and patch compatibility |
| `~1.2.3` | Patch only, so `>=1.2.3 <1.3.0` | A dependency you want pinned tighter than caret |
| `>=1.2.3` | Open-ended | Rarely; it makes future installs non-reproducible |
| `latest`, `*`, `""` | Anything | Never in a committed manifest |
| `workspace:*` | The local workspace package | Monorepo internal dependencies |

Guidance:

- **Applications**: exact pins plus a lockfile. You control the deploy, so you want
  reproducibility and you can review every update.
- **Libraries**: caret ranges for the versions you have tested, with a peer dependency
  when you care about the consumer's copy.
- **`latest` in a committed manifest** defeats the lockfile and makes two installs from
  the same commit produce different trees.

## Lockfiles and reproducible installs

The lockfile pins the full transitive tree, including integrity hashes.

```bash
# CI. Fails if the lockfile does not already satisfy package.json.
pnpm install --frozen-lockfile

# npm equivalent.
npm ci
```

Use the frozen or `ci` form in CI and automation. The install form that updates the
lockfile in CI turns a reproducible build into an unreviewed dependency change that
happens at build time.

Rules:

- Commit the lockfile, always, including for libraries that ship bundled dependencies.
- Never edit a lockfile by hand to force a resolution. Fix the manifest or use the
  manager's override mechanism.
- When a lockfile conflict blocks a merge, regenerate deliberately in a branch, review
  the diff, and land it as its own change so the resolution is visible.
- The lockfile format is manager-specific. `package-lock.json`, `pnpm-lock.yaml`, and
  `yarn.lock` are not interchangeable. Pick one manager and never mix.

## npm, pnpm, yarn, and the layout decision

| | npm | pnpm | yarn |
| --- | --- | --- | --- |
| Ships with Node | Yes | No | No |
| Lockfile | `package-lock.json` | `pnpm-lock.yaml` | `yarn.lock` |
| `node_modules` layout | Hoisted, flat | Symlinked, isolated store, hoisted patterns for compatibility | Varies by mode |
| Workspaces | `workspaces` in `package.json` | `pnpm-workspace.yaml` | `workspaces` in `package.json` |
| Strict peer resolution | No | Yes, by default | Configurable |

The meaningful difference is the on-disk layout. npm hoists everything to the top level,
so a transitive dependency of one package can satisfy an import in another, and the graph
you wrote is not the graph that runs. pnpm builds an isolated tree where each package sees
its declared dependencies only, with compatibility hoisting for the ecosystem's top-level
conventions. That strictness is the point: an undeclared import fails instead of silently
working on your machine.

Decision guidance:

| Situation | Choose |
| --- | --- |
| New application, want the fewest moving parts | npm |
| Monorepo with many packages | pnpm |
| Existing repository with a Yarn Berry setup | Stay on yarn |
| Publishable libraries with a strict graph | pnpm or yarn with strict peer resolution |
| Team already has a lockfile | Never switch without a reason and a migration plan |

### pnpm workspace

```yaml
# pnpm-workspace.yaml
packages:
  - "packages/*"
  - "apps/*"

# Catalogues let many packages agree on one version of a shared dependency.
catalog:
  react: ^18.3.1

# Overrides force a resolution across the whole graph.
overrides:
  minimist: "1.2.8"
```

```json
{
  "dependencies": {
    "react": "catalog:"
  }
}
```

```bash
pnpm install
pnpm --filter @acme/web run build   # run a script in one package
pnpm -r run build                  # run in every package, in topological order
pnpm list --depth 1 --filter @acme/web
```

Catalogues are worth adopting early in a monorepo: they make a version bump a one-line
change instead of a sweep across every manifest. Override mechanisms for forcing a single
resolution exist in all three managers, and the exact key is manager-specific, so check
the settings reference for the manager you use rather than assuming a shared name.

## Peer dependency conflicts

Typical message:

```text
npm ERR! Could not resolve dependency:
npm ERR! peer react@"^18.0.0" from @acme/ui@2.1.0
npm ERR! Found react@17.0.2 at node_modules/react
```

Diagnosis order:

1. Which versions are actually installed? `npm ls react` or `pnpm why react`.
2. Is the peer satisfied by a direct dependency you control, or only by a transitive one?
3. Is there more than one copy in the tree?

Fixes, best first:

| Fix | When to use |
| --- | --- |
| Upgrade the direct dependency to satisfy the range | The usual answer |
| Downgrade the package that needs a narrower range | Only if the narrow range is genuinely required |
| Move the constraint to a peer dependency on your own package | When your package is the one that should carry the requirement |
| Override the resolution | Last resort, with a written note about why it is safe |
| Disable strict peer checking | Temporarily, to unblock a team, never as a permanent setting |

`peerDependenciesMeta` marks a peer as optional when the dependency is genuinely optional:

```json
{
  "peerDependencies": { "react": ">=18" },
  "peerDependenciesMeta": { "react": { "optional": true } }
}
```

Disabling peer checking across a team hides real incompatibilities until runtime. If you
must disable it temporarily, put an issue with an owner and a date on it.

## Monorepo task orchestration

Running scripts by hand across packages gets expensive as the package count grows.
Task runners handle caching and dependency ordering.

| Tool | Approach | Best for |
| --- | --- | --- |
| `pnpm -r` | The package manager's own recursion and topological ordering | Small and medium monorepos, no extra install |
| Turborepo | Task graph with hashes, remote cache, npm-agnostic | Teams wanting fast incremental builds and a shared cache |
| Nx | Project graph with generators and plugin ecosystem | Large monorepos needing enforced boundaries and per-project tooling |

```json
{
  "$schema": "https://turbo.build/schema.json",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "outputs": ["dist/**"]
    },
    "test": {
      "dependsOn": ["build"]
    },
    "lint": {}
  }
}
```

`dependsOn: ["^build"]` means "build every package this one depends on first". `outputs`
declares what the task writes, which is what makes caching correct. Get `outputs` wrong
and the cache serves a package whose build output was never written.

Before adopting a runner in an existing repository, measure: if builds are already fast,
the runner is overhead. A small monorepo on `pnpm -r` with no extra dependency is often
the right answer.

## Supply chain basics

Minimum hygiene for any repository:

```bash
# npm
npm audit
npm audit fix

# pnpm
pnpm audit
```

In CI, fail the build on a high-severity advisory in production dependencies. Do not let
`--force` become the default fix; it applies a major-version bump you have not tested.

Beyond advisories:

- Prefer dependencies with a small transitive surface for anything in a hot path.
- Review the install scripts. A dependency that needs `postinstall` to compile or
  download a binary is executing arbitrary code during install. Modern managers can block
  or require explicit approval for build scripts; check the settings for the manager you
  use.
- Pin exact versions for anything in the build chain, and use a scheduled update tool
  that opens pull requests rather than a bulk upgrade command.
- Watch for typosquatting. `lodahs` exists. Read the repository URL in the package
  metadata, not just the name.
- Have a way to audit where a package came from. Lockfile integrity hashes plus a
  restricted registry scope do most of this work.

Do not treat `npm audit` as a complete supply chain defence. It matches known advisories;
it cannot tell you that a package's newest release is fine.

## Pruning dev dependencies in production

A production image should contain runtime dependencies and nothing else.

```bash
# Multi-stage build: the final stage installs production dependencies only.
# See package-managers/references/monorepos.md for a complete Dockerfile.
```

```dockerfile
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
USER node
CMD ["node", "dist/server.js"]
```

Points that matter:

- `npm ci --omit=dev` (or the manager's equivalent) installs from the lockfile and omits
  development dependencies. Do not copy `node_modules` from the build stage; it contains
  the dev tree and defeats the purpose.
- Build tooling that generates code must run in the build stage, not at container start.
  Anything at start is an opportunity to fail open.
- Run as a non-root user. `USER node` is one line and removes a class of container escape.
- Clean the package manager cache in the same layer as the install so it does not persist
  in the image.
- In a monorepo, prune per package rather than globally, since each package declares its
  own runtime dependencies. See [references/monorepos.md](references/monorepos.md).

## Checklist

- Every runtime import is in `dependencies`; build and test tooling is in
  `devDependencies`.
- No `latest` or `*` ranges in committed manifests.
- Exactly one package manager per repository, with the version pinned via
  `packageManager`.
- The lockfile is committed and CI installs with the frozen or `ci` form.
- Peer dependency conflicts are resolved by upgrading the direct dependency, not by
  disabling the check.
- Workspace-level versions are managed with a catalogue or an override mechanism, not by
  editing each manifest.
- A CI job runs the security audit and fails on production-dependency advisories.
- Production images install runtime dependencies only, as a non-root user, with no
  copied dev `node_modules`.
- Task runner `outputs` declarations match what the task actually writes.

## Common mistakes

- **Library ships with a runtime import in `devDependencies`.** Symptom: the package
  installs fine and throws module-not-found in the consumer's production build. Fix: move
  it to `dependencies` and re-publish.
- **`node_modules` copied between Docker stages.** Symptom: image is unexpectedly large
  and contains build tools. Fix: run a clean production-only install in the final stage.
- **CI uses the updating install form.** Symptom: two CI runs from the same commit
  install different versions. Fix: use the frozen or `ci` install in CI.
- **Three package managers' lockfiles in one repo.** Symptom: merges conflict and nobody
  knows which is authoritative. Fix: delete the extras and add them to `.gitignore`.
- **Peer conflict solved by turning off strict checking.** Symptom: a runtime crash in
  production that no local run reproduced. Fix: upgrade the dependency to satisfy the
  range.
- **A transitive dependency satisfies an undeclared import.** Symptom: works locally,
  fails for consumers. Symptom under pnpm is a loud failure, which is the correct
  outcome; add the missing declaration.
- **Cache config missing `outputs` in a task runner.** Symptom: builds pass locally and
  CI serves stale artifacts from a cache. Fix: declare the real output paths.

## Anti-patterns

- Committing `node_modules`.
- Two lockfiles coexisting while a migration is "in progress" with no end date.
- `npm audit fix --force` in a merge script.
- Depending on a large package for one function, without checking what it pulls in.
- Pinning a dependency to a URL or a git ref because a published version lags, without a
  note explaining when the pin can be removed.
- Accepting dependency updates in the same change as a feature, so a regression cannot be
  bisected.
- Treating the audit output as the whole supply chain story.

## References

- npm dependencies and workspaces: https://docs.npmjs.com/cli/using-npm/dependencies
- npm ci: https://docs.npmjs.com/cli/commands/npm-ci
- npm audit: https://docs.npmjs.com/cli/commands/npm-audit
- pnpm settings: https://pnpm.io/settings
- pnpm workspaces: https://pnpm.io/workspaces
- pnpm catalogues: https://pnpm.io/catalogs
- Yarn documentation: https://yarnpkg.com/getting-started
- Turborepo configuration: https://turborepo.com/docs/reference/configuration
- Nx documentation: https://nx.dev/documentation