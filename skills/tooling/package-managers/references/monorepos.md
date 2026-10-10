# Monorepo layouts and production images

## Layout

```text
repo/
  package.json            root, private, scripts that fan out
  pnpm-workspace.yaml     workspace definition, catalogues, overrides
  pnpm-lock.yaml          one lockfile for the whole workspace
  apps/
    web/
      package.json
      src/
    api/
      package.json
  packages/
    ui/
      package.json
      src/
    config-typescript/
      package.json
```

Conventions that keep a monorepo workable:

- Internal packages are `private: true` unless they are published.
- Package names are scoped, for example `@acme/ui`.
- Internal dependencies use the workspace protocol so a published package is never
  accidentally published with a `workspace:` range, which consumers cannot resolve.
- Each package owns its own `package.json`, its own tests, and its own version. Do not
  centralise scripts into the root.
- The root holds only cross-cutting scripts and configuration that must be shared.

## pnpm catalogue

A catalogue pins one version of a shared dependency across every package:

```yaml
# pnpm-workspace.yaml
packages:
  - "packages/*"
  - "apps/*"

catalog:
  react: 18.3.1
  react-dom: 18.3.1
  zod: 3.23.8
```

```json
{
  "dependencies": {
    "react": "catalog:",
    "zod": "catalog:"
  }
}
```

A version bump becomes one edit in `pnpm-workspace.yaml` plus one `pnpm install`. Without
a catalogue the same bump is an edit in every manifest and a chance for the versions to
diverge.

## pnpm overrides

Force a resolution across the whole graph, for example to take a transitive fix without
waiting for the direct parent to release:

```yaml
# pnpm-workspace.yaml
overrides:
  "some-package>left-pad": 1.3.0
```

Two rules:

- Add a comment or an adjacent note saying why the override exists and when to remove it.
- Remove it when the parent releases. Overrides outlive their reason otherwise.

Check the settings reference for the current override syntax; the key location has moved
between pnpm versions and the scoped form differs from the flat form.

## Private dependencies inside a published package

When a package depends on another workspace package that is not published:

```json
{
  "dependencies": {
    "@acme/config-typescript": "workspace:*"
  }
}
```

Some managers require a pack step before publish so `workspace:` ranges are rewritten to
real versions. Verify the exact behaviour for the manager and the package type you are
publishing; library packages generally need it, and private packages generally do not.

## Running tasks

```bash
# One package.
pnpm --filter @acme/web run build

# Every package, in topological order so dependencies build first.
pnpm -r run build

# Only packages that depend on a changed package.
pnpm --filter "...@acme/ui" run test

# Add a dependency to a specific package without touching the others.
pnpm --filter @acme/web add zod
```

Topological ordering is the part people miss. Without it, a build script that consumes
another workspace package's `dist` fails nondeterministically depending on directory
iteration order.

## Enforcing boundaries

A package should not be able to import from a package that does not depend on it. Two
ways to enforce:

1. The package manager's own layout. With pnpm's isolated tree, an undeclared import
   fails to resolve. This is the cheapest enforcement available and it is real.
2. An explicit lint rule, for libraries that must also work under a hoisted layout.

```json
{
  "dependencies": { "eslint-plugin-boundaries": "5.2.3" },
  "devDependencies": { "eslint": "9.14.0" }
}
```

```javascript
// eslint.config.js
import boundaries from "eslint-plugin-boundaries";

export default [
  {
    files: ["packages/*/src/**"],
    plugins: { boundaries },
    settings: {
      boundaries: [
        {
          from: "package",
          to: "package",
          disallowed: true,
          message:
            "Import across package boundaries. Declare the dependency in package.json.",
        },
      ],
    },
  },
];
```

Rule names and option keys change; verify against the plugin's current documentation
before relying on the exact shape above.

## Task runner configuration

Turborepo, as an example of the shape:

```json
{
  "$schema": "https://turbo.build/schema.json",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "outputs": ["dist/**", ".next/**"]
    },
    "test": {
      "dependsOn": ["build"],
      "outputs": ["coverage/**"]
    },
    "lint": {
      "outputs": []
    }
  }
}
```

Correctness rules:

- `dependsOn` with a leading caret means "dependencies of this package first".
- `outputs` lists every path the task writes. An incomplete list makes the cache serve a
  package with missing artifacts. When in doubt, list everything the task writes.
- Test tasks that depend on build make local runs slower but CI failures rarer. Choose
  deliberately.

## Production image for a monorepo

The naive approach, copying the whole `node_modules`, ships every package's dev
dependencies and every workspace link. Instead:

```dockerfile
# syntax=docker/dockerfile:1

FROM node:20-bookworm-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ui/package.json packages/ui/
COPY apps/api/package.json apps/api/
# Install the whole workspace with dev dependencies for the build stage.
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
RUN pnpm -r run build

FROM base AS prod-deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ui/package.json packages/ui/
COPY apps/api/package.json apps/api/
# Re-resolve with dev dependencies omitted.
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --prod

FROM base AS runtime
ENV NODE_ENV=production
# Copy only the manifests for the service being run, then its build output.
COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=prod-deps /app/packages/ui/node_modules ./packages/ui/node_modules
COPY --from=build /app/apps/api/dist ./dist
COPY --from=build /app/packages/ui/dist ./packages/ui/dist
USER node
EXPOSE 3000
CMD ["node", "dist/main.js"]
```

Why each step exists:

- **Copying only manifests before install** keeps the layer cache usable. Changing source
  files does not invalidate the dependency layer.
- **A separate production install** rather than pruning the build tree, so the final
  image cannot accidentally contain dev dependencies.
- **Per package `node_modules`** because a workspace has one directory per package under
  pnpm's layout.
- **Copying build output from the build stage** because transpiled code is not in the
  source tree.
- **`USER node`** so a container escape starts from an unprivileged user.
- **A cache mount for the package store** keeps repeated builds fast without baking the
  store into the image.

Verify the production install command and its workspace behaviour against the manager's
documentation for your version; the flags are stable but the layout assumptions are not.

## Deployment ordering

Independent deploys in a tight dependency graph require compatible versions on both
sides. Three workable strategies:

- **Versioned interfaces.** Require a new field to be optional and added before it is
  read. Deploy readers first.
- **Expand and contract.** Add alongside, backfill, switch readers, then remove.
- **Big-bang deploy.** Accepts a maintenance window, simplest, and increasingly rare.

Pick one per interface and write it down where the implementer will read it.