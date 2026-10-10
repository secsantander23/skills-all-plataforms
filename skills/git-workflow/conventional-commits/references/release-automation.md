# Release automation setup

Deeper detail for the `conventional-commits` skill. Both stacks below are real; verify
option names against the current docs before copying into a new repository, because these
tools ship breaking config changes between major versions.

## semantic-release

semantic-release is a Node CLI. It runs in CI with `GITHUB_TOKEN` (and an npm token if it
publishes), reads commits since the last reachable tag, computes the highest bump, updates the
version, generates notes, publishes, and creates the GitHub release.

### package.json scripts

```json
{
  "scripts": {
    "release": "semantic-release"
  },
  "release": "node_modules/semantic-release/bin/semantic-release.js",
  "devDependencies": {
    "semantic-release": "^24.0.0"
  }
}
```

### The release plugin chain

The default chain is: verify conditions, analyze commits, verify the release, generate notes,
prepare the release (set version, update changelog), publish, then GitHub.

`.releaserc.json` with an explicit plugin list and a changelog writer:

```json
{
  "branches": ["main"],
  "tagFormat": "${version}",
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/changelog",
    ["@semantic-release/npm", { "npmPublish": false }],
    "@semantic-release/git",
    "@semantic-release/github"
  ]
}
```

`@semantic-release/changelog` writes the aggregated `CHANGELOG.md` using
`conventional-changelog`. The `tagFormat` option above is a real semantic-release option; the
default tag format is `v${version}`.

### Custom bump rules

The commit analyzer decides the bump from commit types. Override with `releaseRules`:

```json
{
  "branches": ["main"],
  "releaseRules": [
    { "type": "perf", "release": "patch" },
    { "type": "refactor", "release": false },
    { "scope": "deps", "release": "patch" },
    { "breaking": true, "release": "major" }
  ],
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/npm",
    "@semantic-release/github"
  ]
}
```

Rule properties are `type`, `scope`, `subject`, `breaking`, and `release`. When multiple
rules match, the highest release wins. Omitting `release` means "no release from this match",
which is how you exclude noise types.

### Dry run

```bash
npx semantic-release --dry-run
```

Prints the steps it would run, including the computed next version, without changing anything.
Always run this in a branch or a fork first — a dry run still performs a dry-run publish, so
you want the credentials pointed at a sandbox.

### Monorepos

semantic-release operates on one package per run. For many packages, run one instance per
package in CI with each package's own `package.json` as the working directory, or use
`@semantic-release/exec` to call another tool. Changesets is usually less work here.

## Changesets

Changesets is file-based: humans declare intent, the tool applies it.

### Install

```bash
npm install --save-dev @changesets/cli
npx changeset init
```

`changeset init` writes `.changeset/config.json` and `.changeset/README.md`.

### Daily use

```bash
npx changeset                    # interactive, creates a changeset file
npx changeset add "other-pkg"    # non-interactive-ish: bump a specific package (bump selection is still prompted)
npx changeset status             # show pending changesets and computed bumps; --output for JSON
npx changeset version            # apply bumps, rewrite CHANGELOG.md files, update dependents
npx changeset publish            # publish changed packages to npm, tag, create releases
```

`changeset version` consumes the changeset files and deletes them. `changeset publish`
publishes only packages whose version changed.

### Versioning modes

- **Independent** (default): each package versions on its own.
- **Fixed** (`"fixed": [["@acme/*"]]`): all packages in the array are versioned and published
  together at the same version. Use it for packages that must stay in lockstep.
- **Linked** (`"linked": [["@acme/*"]]`): packages share a version when they are released,
  but an unchanged package is not force-bumped. Use it when most packages move together but
  some can lag.

```json
{
  "changelog": "@changesets/cli/changelog",
  "commit": false,
  "fixed": [["@acme/core", "@acme/ui"]],
  "linked": [],
  "access": "public",
  "baseBranch": "main",
  "updateInternalDependencies": "patch",
  "ignore": ["@acme/legacy"]
}
```

`updateInternalDependencies` controls what happens when package A bumps and depends on package
B at a fixed range: `patch` or `minor` prompts an internal bump of B, `minor` follows semver
range widening. Bump it when your ranges are narrow (`~1.2.0`) and you want automatic widening.

### Changeset file format

```md
---
"@acme/ui": minor
"@acme/core": patch
---

Add a Toggle component and export it from the package entrypoint.
```

Bump values: `patch`, `minor`, `major`. The body is user-facing prose — it becomes the
changelog entry, so write it for the person upgrading, not the person reviewing.

### CI wiring

A typical pipeline: a `changeset status --since=origin/main` check that fails when a PR
touches packages without a changeset; a `changeset version` + `changeset publish` job gated on
the default branch.

```bash
npx changeset status --since=origin/main
```

This exits non-zero when no changesets are present. Use it as a CI gate with a clear failure
message telling the contributor to run `npx changeset`.

## Tag-based or manual releases

Some projects keep automation for notes only and version by hand. `conventional-changelog -p
angular -i CHANGELOG.md -s` generates a changelog from commits in the Angular format; the `-p`
preset, `-i` file, and `-s` flags are real. This is the standard fallback when a release tool
cannot own the version field because something else already does.

## Verify before relying on this

- semantic-release: https://semantic-release.gitbook.io/semantic-release/ — check the
  `shareable-config` and `release-rules` plugin options for your version.
- Changesets: https://github.com/changesets/changesets — the `.changeset/config.json` schema
  lives in the `@changesets/config` package; copy `$schema` from a freshly generated config.