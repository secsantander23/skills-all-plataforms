---
name: Conventional Commits and Release Automation
description: Covers the Conventional Commits specification, the mapping from commit type and breaking-change markers to semantic version bumps, issue linking, squash-merge PR titles, and automation with commitlint, semantic-release, and Changesets. Use when the user asks how to format a commit message, enforce commit conventions in CI, set up automatic versioning or changelog generation, or debug why a release was versioned incorrectly.
metadata:
  opencode/autoinvoke: true
---

# Conventional Commits and Release Automation

A Conventional Commit encodes *intent* in a structured prefix: `type(scope)!: subject`.
Automation tools read that prefix to decide whether the next release is a patch, minor, or
major bump, and to generate changelog entries. If the prefix is wrong or missing, the release
is wrong — so this is a machine-parsed format, not a style preference.

## When to use this

- Writing or fixing a commit message, and wondering what goes after the colon.
- Deciding whether a change is a `fix`, a `feat`, or a breaking change.
- Setting up commitlint, husky, semantic-release, or Changesets.
- Debugging a bad release: wrong version bump, missing changelog entry, wrong bump severity.
- Reviewing a PR title that will become the squash-merge commit message.
- Automating a "closes #123" link, or asking why the issue did not close.

## Quick start

Install and enforce the format locally before automating releases.

```bash
npm install --save-dev @commitlint/cli @commitlint/config-conventional

# Lint the most recent commit message
npx commitlint --edit HEAD~1

# Lint every commit on this branch that is not on main
npx commitlint --from origin/main --to HEAD
```

`commitlint.config.mjs` in the repo root:

```js
import configConventional from '@commitlint/config-conventional';

export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'subject-case': [0, 'never'],
    'body-max-line-length': [0, 'always'],
  },
};
```

Write a message:

```bash
git add -A
git commit -m "fix(auth): reject refresh tokens that were already rotated"
```

## Commit message structure

```text
<type>[optional scope][!]: <description>

[optional body: why the change was needed, wrapped at 72 columns]

[optional footers: BREAKING CHANGE, Refs, Co-authored-by]
```

Rules that matter:

- The header is `type`, an optional parenthesised `scope`, an optional `!`, a colon, a space,
  and a description.
- The description is a single imperative sentence: "add", not "added" or "adds". No trailing
  period.
- A blank line separates the header from the body, and the body from the footers.
- The `!` marker is a shorthand for a `BREAKING CHANGE:` footer. Use one or both; tooling
  understands either. Use both when a tool (or a human skimming `git log --oneline`) may
  miss the footer.

Correct:

```text
feat(api): add pagination to GET /v1/orders

The endpoint previously returned the full result set, which exhausted
memory once a tenant exceeded a few thousand orders. Cursor pagination
keeps the response bounded.

BREAKING CHANGE: `GET /v1/orders` now returns `{ items, next_cursor }`
instead of a bare array. Clients reading the array directly must read
`items` first.
```

Incorrect:

```text
Updated the orders endpoint (see #4821) - fixed the bug
```

## Types, scopes, and the SemVer mapping

The specification's **normative** types are only `feat` (new capability) and `fix` (bug fix).
Every other type is allowed as long as the project is consistent — and automation that keys
off types needs to know which of them you treat as release-worthy.

| Type | Meaning | Default bump | Notes |
| --- | --- | --- | --- |
| `feat` | New user-visible capability | MINOR | Normative type |
| `fix` | Bug fix | PATCH | Normative type |
| `perf` | Change for performance, no API change | PATCH | Not normative; tune per tool |
| `refactor` | No behavior change | none | Must not alter behavior |
| `docs` | Documentation only | none | |
| `test` | Adding or correcting tests | none | |
| `build` | Build system, bundler, dependencies | none | |
| `ci` | CI configuration and scripts | none | |
| `chore` | Maintenance that fits nothing else | none | |

`BREAKING CHANGE` always forces a MAJOR bump, whatever the type is. `refactor!:` is a real
breaking change.

Scopes are free-form labels for the area touched: `auth`, `api`, `storage`, `ui`, `deps`.
Pick a small fixed vocabulary and document it in `CONTRIBUTING.md`; the useful property is
that `git log --grep` can answer "who changed the auth area last month".

The rule that trips people up: **the bump is driven by the commit, not by intent.** A
`feat` that only touches internal comments still produces a MINOR bump unless your release
tool filters it. Configure the analyzer's rules explicitly instead of relying on defaults.

## Linking issues

GitHub closes an issue when the PR body or a commit message on the default branch contains
`close #123`, `fixes #123`, `resolves #123` (and the `closed`/`fixed`/`resolved` variants).
This only works when the commit reaches the default branch and the issue lives in the same
repository.

```text
fix(parser): handle CRLF line endings in imported config files

Users on Windows reported a parse failure with the message
"unexpected token at line 1". The lexer assumed LF-only input.

Fixes #1284
Refs #1290
```

Rules:

- Put the keyword at the start of a footer line, on its own line.
- `Fixes #1284` accepts a comma-separated list: `Fixes #1284, #1290, #1301`.
- `Refs #1290` is a GitHub convention for cross-referencing without closing.
- Do not use closing keywords in a commit on a topic branch — they do not close anything
  until the commit lands on the base branch, which surprises people when the branch stalls.

## Breaking changes

Mark a change as breaking when **any** consumer must act:

- Removing or renaming a function, endpoint, field, event, or config key.
- Changing a type in a way that fails compilation for callers.
- Changing a default value that alters observable behavior.
- Narrowing accepted input (a stricter validator, a smaller enum).

Do not mark as breaking: internal refactors, bug fixes that restore documented behavior,
performance work, dependency bumps with no surface change.

Write the footer as what the *consumer must do*, not what you did internally:

```text
feat(config)!: require explicit unit for temperature values

Un-suffixed values were previously read as Celsius. They are now a
hard error and must carry `C` or `F`.

BREAKING CHANGE: config values `maxTemp: 30` now fail validation.
Write `maxTemp: 30C`.
```

## PR titles and squash merges

With squash or rebase merging, the PR title becomes (or seeds) the commit message. That makes
the PR title the only thing automation sees for a five-commit PR.

- Write the PR title as one complete Conventional Commit header, including scope and any
  `!`.
- In GitHub, set the repository's merge options so squash uses the PR title rather than
  title-plus-commit-list. Leaving the default produces a multi-line message that many tools
  still parse, but the trailing lines look like noise in the log.
- In the PR body, add `Fixes #123`. Squash-merge re-uses the commit messages, so the keyword
  survives the merge.

For long branch histories, use fixup commits and autosquash instead of hand-merging:

```bash
git commit --fixup=<sha-of-the-commit-to-fix>
git commit --squash=<sha-of-the-commit-to-combine-message-with>

git rebase -i --autosquash origin/main
```

`fixup!` discards the fixup message and keeps the target's; `squash!` keeps both messages.
Both work only if the *target* commit is already conventional — the resulting squashed commit
inherits the target's header. Confirm the final result with `git log --oneline -5` before
pushing.

## Release automation

Two common stacks. Both read commits or a changelog *directory* — they never guess intent.

**semantic-release** reads commits since the last release tag, computes the next version, and
publishes. `.releaserc.json`:

```json
{
  "branches": ["main"],
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    "@semantic-release/npm",
    "@semantic-release/github"
  ]
}
```

**Changesets** is file-based: a developer writes a changeset file describing the bump, and the
version step consumes those files. `.changeset/config.json`:

```json
{
  "changelog": "@changesets/cli/changelog",
  "commit": false,
  "fixed": [],
  "linked": [],
  "access": "public",
  "baseBranch": "main",
  "updateInternalDependencies": "patch"
}
```

```bash
npx changeset          # interactive: select packages, pick bump, write summary
npx changeset status   # print pending bumps without writing anything
npx changeset version  # bump versions, rewrite CHANGELOG.md files
npx changeset publish  # publish to npm and create release/tag
```

Changeset file format (`.changeset/tidy-pandas-smile.md`):

```md
---
"@acme/ui": minor
"@acme/core": patch
---

Add a Toggle component and expose it from the package entrypoint.
```

Choose Changesets when you ship a monorepo with interdependent packages, or when humans
should decide the bump. Choose semantic-release when you ship a single package and trust the
commit stream. See `references/release-automation.md` for the full setup, including tag-based
flows and CI wiring.

## Commit hygiene

- One logical change per commit. A commit that needs the word "and" in its description is
  usually two commits.
- Every commit should leave the tree green. A commit that breaks the build forces reviewers to
  mentally separate the working change from the broken state.
- Do not commit generated artifacts, secrets, or `.env` files. Add them to `.gitignore` the
  first time you notice them.
- Do not amend or force-push commits that are already on a shared branch. Rebase your own
  unpushed branch freely.
- Sign commits (`git config --global commit.gpgsign true`) if your project requires
  verifiable authorship; CI that verifies signatures will reject unsigned commits.
- Use `git commit -F file` or `-m` with multiple `-m` flags for long messages instead of
  typing a subject line that wraps badly.
- Never use `--no-verify` to get past a failing hook in CI. Fix the message.

## Checklist

Done when a change can ship:

- [ ] Header matches `<type>[scope][!]: <imperative subject>`.
- [ ] Type is one your release tool recognizes; unknown types are configured, not accidental.
- [ ] A consumer-visible behavior removal carries `!` **and** a `BREAKING CHANGE:` footer.
- [ ] The issue is linked with `Fixes #N` (closing) or `Refs #N` (cross-reference).
- [ ] Body explains *why*, wrapped at 72 columns; headers stay under ~72 characters.
- [ ] PR title is a full conventional header when the repo squash-merges.
- [ ] No secrets, no generated output, no unrelated drive-by edits.
- [ ] `npx commitlint --from origin/main --to HEAD` passes locally.

## Common mistakes

**`chore: bump deps` producing a release.** Many analyzers treat unknown or chore types as no
release. Bump the type (`fix` for a real bug fix in a dependency) or configure the analyzer's
rule set explicitly. Check your tool's `releaseRules` documentation and run
`npx semantic-release --dry-run` to see the computed version before publishing.

**A breaking change shipped without the `!`.** The release publishes as a patch, downstream
users upgrade with a breaking change and file the bug against you. Cross-reference: if the PR
description says "this will break", the header must say it too.

**Squash merges producing multi-message commits.** The GitHub default appends every squashed
commit message. The result still parses for the header, but the body becomes a wall of stale
"intermediate fix" prose that lands in the published changelog. Change the repository's
squash commit message setting to use the PR title.

**`fixup!` used for a change that needs explanation.** Autosquash throws the fixup message
away. If the fixup needs a why, use `--squash=` instead.

**Running `changeset version` on a dirty tree.** It edits every manifest and changelog; a
dirty tree turns that into an unreviewable commit. Run `git status` and commit or stash first.

**`npm version` bumping past your release tool.** `npm version` (major/minor/patch) creates a
version commit and a tag. If a release tool also tags, you get two sources of truth. Pick one
owner of the version field and disable the other.

## Anti-patterns

- **Types invented per commit** — `feature:`, `feature!:`, `Feat:`, `bugfix:`. Tools match
  exact lowercase strings. A near-miss type is silently treated as no release.
- **`Merge branch 'main' into feature/x` as the PR title.** It is not a valid header, and on
  squash it becomes the released commit subject.
- **Encoding the type in the branch name only**, then squashing to a message with no type.
- **Using `feat` for every change**, so patch-level fixes ship as minor and downstream
  `^1.0.0` consumers upgrade to a release they did not want.
- **Auto-generating `BREAKING CHANGE` footers from PR labels** with no human confirmation.
  Mislabelled majors are the most expensive failure mode in this pipeline.
- **Bypassing hooks with `--no-verify` on CI-protected branches.** The hook exists so the
  mistake is caught before the tag is created, not after.

## References

- Conventional Commits specification: https://www.conventionalcommits.org/en/v1.0.0/
- Semantic Versioning 2.0.0: https://semver.org/
- commitlint documentation: https://commitlint.js.org/
- semantic-release documentation: https://semantic-release.gitbook.io/semantic-release/
- Changesets documentation: https://github.com/changesets/changesets
- conventional-changelog: https://github.com/conventional-changelog/conventional-changelog
- Angular's commit message convention: https://github.com/angular/angular/blob/main/CONTRIBUTING.md
- `git commit` reference: https://git-scm.com/docs/git-commit
- Git commit templates: https://git-scm.com/docs/git-commit#_commit_templates
- Git trailers: https://git-scm.com/docs/git-interpret-trailers
- GitHub: about pull request merges: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/merging-pull-requests/about-pull-request-merges
- GitHub: linking a pull request to an issue: https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
- GitHub Actions `create-release` and the release workflow: https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows
- Go module release workflow (a strong real-world Conventional Commits consumer): https://go.dev/doc/modules/release-workflow