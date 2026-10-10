---
name: git-expert-workflow
description: Covers advanced Git workflow for real repositories, including rebasing versus merging, interactive rebase, cherry-pick, reflog recovery, git bisect for regressions, worktrees for parallel work, submodules, shallow clones, tags and releases, hooks, cleaning untracked files, and CI-friendly practices such as signed commits and protected branches. Use when the user mentions rebase, merge conflict, cherry-pick, recovering lost commits, bisecting a regression, a git worktree, a submodule, a shallow clone, or how to undo a Git operation.
metadata:
  opencode/autoinvoke: true
---

# Advanced Git Workflow

Git stores history as a directed acyclic graph of commits. Every technique here is a
consequence of that model: a rebase rewrites commits by creating new ones, a merge
records two parents, and the reflog is the safety net that makes rewriting recoverable.
Understand the model and the commands stop being tricks.

Verify before relying on this: Git's behaviour is stable and version-independent for
almost everything here. Confirm flag availability with `git <command> --help` for the
version you have, particularly for porcelain commands added in recent releases.

## When to use this

- A pull request has 40 commits and 12 fixups and needs to be reviewable.
- A merge conflict stopped a rebase and nobody knows how to continue.
- Someone force-pushed over commits and needs them back.
- A regression appeared two weeks ago and the first bad commit is unknown.
- You need two branches checked out at once.
- A monorepo has shared libraries as separate repositories.

## Quick start

The daily loop for a clean history.

```bash
# Before starting work, always.
git switch main
git pull --ff-only
git switch -c feat/add-csv-export

# Work. Then, before opening a pull request, tidy locally.
git fetch origin
git rebase origin/main

# Push a rewritten branch safely.
git push --force-with-lease

# When someone else has pushed to your branch.
git fetch origin
git rebase origin/your-branch
git push --force-with-lease
```

`--force-with-lease` instead of `--force` refuses to overwrite if the remote moved since
you last fetched. It turns a destructive operation into a safe one that fails loudly.

## Rebase versus merge

| | Rebase | Merge |
| --- | --- | --- |
| History | Linear | Branching |
| Commit identities | New commits with new hashes | Original commits preserved |
| Conflict resolution | Once, per replayed commit | Once, at the merge point |
| Best for | Feature branches and pull requests | Long-lived branches, shared history |
| Auditing provenance | Loses who authored the intermediate commits | Preserves it |

Rule of thumb: rebase anything nobody else has pulled; merge long-running shared
branches. Rebasing a branch that is public rewrites other people's history and forces
them to recover, which is a coordination cost you impose on others.

### Interactive rebase

```bash
# Last 5 commits, interactive.
git rebase -i HEAD~5

# Everything since a branch point.
git rebase -i main

# Autosquash picks up fixup and squash commits from commit messages.
git commit --fixup <target-sha>
git rebase -i --autosquash main
```

The todo list the editor opens accepts these commands:

| Command | Effect |
| --- | --- |
| `pick <sha>` | Keep the commit as is |
| `reword <sha>` | Keep the change, edit the message |
| `edit <sha>` | Stop so you can amend |
| `squash <sha>` | Merge into the previous commit and combine messages |
| `fixup <sha>` | Merge into the previous commit, discard the message |
| `drop <sha>` | Remove the commit |
| `exec <cmd>` | Run a shell command at this point |
| `label`, `reset`, `merge` | Advanced scripting, used to split a commit |

Useful patterns:

```text
# Split one commit into two, with the editor stopping before each change.
pick  a1b2c3d Add CSV export
edit  e4f5g6h Add tests
```

```bash
# After stopping at a commit, stage part of the change and continue.
git reset HEAD^ -- path/to/file   # unstage everything, keep the working tree
git add -p                         # stage only the parts for the first commit
git commit -m "Add CSV export core"
git commit -am "Add CSV export tests"   # the rest
git rebase --continue
```

Resuming after a conflict:

```bash
git status                 # shows what stopped the rebase
# fix the files, then
git add <files>
git rebase --continue

# Give up and restore the original state.
git rebase --abort
```

## Cherry-pick

```bash
git cherry-pick <sha>                     # one commit
git cherry-pick <start-sha>^..<end-sha>    # a range, oldest first
git cherry-pick --no-commit <sha>          # stage without committing, to adapt or split
git cherry-pick -x <sha>                   # record the original hash in the message
```

Use `-x` for anything picked from an unmerged pull request or another repository. It
records the original hash, which is the difference between a traceable backport and a
mystery commit.

`--no-commit` is the right tool for splitting a change across commits, or for adapting a
fix to a codebase that has moved.

Conflicts during a cherry-pick: resolve, `git add`, `git cherry-pick --continue`, or
`git cherry-pick --abort`.

## Recovery

The reflog records every position `HEAD` has held, for about 90 days by default. Almost
everything that feels lost is in it.

```bash
git reflog                                 # where HEAD has been
git reset --hard HEAD@{5}                  # return to a previous position
git reflog --date=iso | grep "message"     # find a lost commit
git switch -c recover <sha>                # bring it back on a branch
```

Distinguish the operations, because they lose different things:

| Command | Working tree | Index | `HEAD` | Use for |
| --- | --- | --- | --- | --- |
| `git reset --soft <sha>` | kept | kept | moved | Rename commits, combine before recommitting |
| `git reset --mixed <sha>` | kept | reset | moved | Unstage, uncommit, keep the code |
| `git reset --hard <sha>` | **destroyed** | reset | moved | Undo everything, rarely necessary |
| `git revert <sha>` | kept | kept | new commit | Undo a published change safely |
| `git restore <path>` | restored from `HEAD` | unchanged | unchanged | Discard changes to a file |

Rules:

- Never `reset --hard` when `reset --soft` or `reset --mixed` does the job. `--hard`
  destroys work that reflog cannot bring back if it was never committed.
- Never `reset` or `rebase` a branch that has been pushed and shared. Use `revert`.
- `git revert` on a merge commit needs the parent selector, `git revert -m 1 <merge-sha>`.
  Check `git log --merges` and verify which parent you mean before reverting a merge.

Splitting commits, recovering deleted files, reverting a merge, and unsticking a rebase
are in [references/recovery.md](references/recovery.md).

## Bisect

Bisect finds the commit that introduced a regression by halving the search space.

```bash
git bisect start
git bisect bad              # the current checkout is broken
git bisect good v2.4.0       # a known-good commit
# Git checks out a midpoint. Test it, then:
git bisect good
# or
git bisect bad

git bisect reset             # return to the original branch
```

Automate the test so bisect runs unattended:

```bash
# Script exits 0 for good, non-zero for bad.
git bisect run npm test
```

Use the test suite rather than a build as the command. A build failure and a behaviour
failure are different signals, and bisecting on the wrong one wastes the run.

A shallow clone cannot bisect past its boundary. `git fetch --unshallow` first.

## Bisect

Bisect finds the commit that introduced a regression by halving the search space.

```bash
git bisect start
git bisect bad              # the current checkout is broken
git bisect good v2.4.0       # a known-good commit
# Git checks out a midpoint. Test it, then:
git bisect good
# or
git bisect bad

git bisect reset             # return to the original branch
```

Automate the test so bisect runs unattended:

```bash
# Script exits 0 for good, non-zero for bad.
git bisect start
git bisect bad
git bisect good v2.4.0
git bisect run npm test
```

`git bisect run` needs a command that exits non-zero for a bad commit. That is why the
command should be the test suite, not a build, since a build failure and a behaviour
failure are different signals.

For a regression in a file rather than in behaviour, bisect the file:

```bash
git bisect start
git bisect bad
git bisect good v2.4.0
git log --oneline v2.4.0..HEAD -- src/payments.ts
# Then test only whether the file changed.
```

## Worktrees

A worktree is a second checkout of the repository at a different path, sharing the same
object database. Use it for a long-running branch, a review, or a bisect, without
stashing your working tree.

```bash
git worktree add ../hotfix-fix fix/urgent        # existing branch
git worktree add -b feat/new-parser ../new-parser # new branch
git worktree list
git worktree remove ../hotfix-fix
git worktree prune                                # clean up removed directories
```

Worktrees are how to run two agents, two branches, or a bisect and a feature in the same
repository without stashing. They share objects, so disk cost is small.

Limitations: a branch can only be checked out in one worktree at a time, and some tooling
assumes a single working directory. Check the `git worktree` documentation for the current
behaviour of locking and pruning.

## Submodules

```bash
git submodule add https://github.com/org/lib.git vendor/lib
git clone --recurse-submodules <url>
git submodule update --init --recursive     # existing clone
git -C vendor/lib pull && git add vendor/lib && git commit -m "Update lib"
git submodule status                        # diagnose
git submodule sync --recursive              # after a repository move
```

Submodules are widely disliked for reasons worth weighing: contributors forget to update
them, CI configuration is fiddly, and a forgotten pointer update produces a build that
silently uses the old version. Consider alternatives: publish it as a real dependency, or
copy the small shared module into the repository.

CI that checks out submodules needs both options set:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
    submodules: recursive
```

## Shallow and partial clones

```bash
git clone --depth 1 <url>                              # last commit only
git clone --depth 1 --single-branch --branch main <url>
git clone --filter=blob:none <url>                     # partial, blobs on demand
git fetch --unshallow                                  # get the full history
git fetch --depth 50 origin main                       # deepen
```

Shallow clones break commands that assume full history: `git bisect` across the shallow
boundary, `git log --follow`, and some release tooling.

## Tags and releases

```bash
# Lightweight tag.
git tag v1.2.3

# Annotated tag, the one you actually want.
git tag -a v1.2.3 -m "Release 1.2.3"

# Sign the tag if signing keys are configured.
git tag -s v1.2.3 -m "Release 1.2.3"

# Push one tag.
git push origin v1.2.3

# Push all tags.
git push origin --tags

# List, sorted by version, only tags matching a pattern.
git tag --list 'v*' --sort=-v:refname | head

# Show what a tag points at.
git show v1.2.3 --no-patch
```

Conventions:

- Annotated tags for releases. A lightweight tag has no message, no tagger, and no
  signature, so nothing records who released it.
- Sign releases so consumers can verify provenance.
- Never move a published tag. Cut a new one.
- Use a pre-release suffix while iterating: `v1.2.3-rc.1`, then `v1.2.3`.

Release note generation and hotfix backports are in
[references/recipes.md](references/recipes.md).

## Hooks

Hooks run at client-side events. There are two locations: a `.git/hooks` directory in the
local clone, and a `core.hooksPath` configuration that points at a tracked directory.

```bash
git config core.hooksPath            # what is configured
git config core.hooksPath .githooks  # point at a tracked directory
```

```bash
#!/bin/sh
# .githooks/pre-commit
forbidden=$(git diff --cached --name-only | grep -E '(\.env$|\.pem$|id_rsa$)' || true)
if [ -n "$forbidden" ]; then
  echo "Refusing to commit:" >&2
  echo "$forbidden" >&2
  exit 1
fi
```

Server-side hooks run on the remote and are the only ones that cannot be bypassed. On most
forges, that means a branch protection rule or a required status check rather than a
server hook. Client-side hooks are a developer convenience and nothing more; say so when
you rely on one.

## Cleaning

```bash
git clean -nd      # dry run. Always do this first.
git clean -ndx     # including ignored files
git clean -fd      # actually remove
```

`-x` includes ignored files, which will delete build output and, if you keep local
settings as ignored files, those too. Verify the dry-run output before the destructive form.

Prefer `.git/info/exclude` for machine-local ignores over `.gitignore`, so local settings
do not leak into the committed ignore rules.

## CI-friendly practices

**Protected branches.** Require review, require status checks, disallow force pushes, and
require linear history if that is your convention. Configure this on the forge; a local
pre-push hook is bypassable.

**Signed commits.** Sign so authorship is verifiable:

```bash
git config commit.gpgsign true
git config user.signingkey <your-key-id>
git config gpg.format openpgp   # or ssh
git config gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

Use SSH signing when that is simpler to set up; verify the exact configuration name for
your Git version with `git config --list` and the Git documentation.

**Reproducible CI checkouts.** Set the fetch depth and submodules explicitly. Set
`core.autocrlf` deliberately; the default differs by platform and produces line-ending
diffs nobody wants. Set `core.fileMode=false` on filesystems that do not preserve the
executable bit, or every file shows as modified.

**Deterministic builds.** Pin the base image by digest, install dependencies from a
lockfile, and pin the Node or language runtime version. A build that depends on a tag is
a build you cannot reproduce.

**No secrets in the repository.** History rewriting does not remove a secret: the blob is
still in the object store and still fetchable by anyone who knows the hash. Rotate the
credential first, then rewrite history if rewriting is worth it, then force-push
coordinated with every clone.

## Checklist

- Branches are rebased before review, not merged, unless they are long-lived.
- `--force-with-lease` is the default for pushing rewritten branches.
- Rebases are resumed with `--continue` and abandoned with `--abort`, never by guessing.
- Reflog is checked before any destructive reset.
- Shared branches are undone with `revert`, not `reset`.
- Bisect uses `git bisect run` with an exit-code-bearing command.
- Worktrees are used for parallel work and pruned regularly.
- Release tags are annotated, signed where the forge supports it, and never moved.
- CI checks out with explicit `fetch-depth` and `submodules`.
- `core.hooksPath` points at a tracked hooks directory.
- `git clean` always runs with `-n` first.
- Secrets are rotated, not just deleted.

## Common mistakes

- **Force-push over a shared branch.** Symptom: colleagues lose commits. Cause: `--force`
  without lease, or a rebase on a branch others pulled. Fix: `--force-with-lease`; recover
  the lost commits with `git reflog`.
- **Stuck in a rebase.** Symptom: `git status` reports a rebase in progress and the tree
  is a mess. Fix: `git rebase --abort` to return to the original state. Never commit
  manually to "get past it".
- **A merge commit reverted with the wrong parent.** Symptom: most of the branch's changes
  come back. Cause: `-m 1` when `-m 2` was correct. Fix: check the parent list with
  `git log --merges` before reverting, and inspect the resulting diff.
- **CI "changes not staged for commit".** Symptom: the checkout looks dirty right after
  cloning. Cause: a submodule pointer or line-ending difference. Fix: `submodules:
  recursive` and explicit `core.autocrlf` settings.
- **A secret deleted and committed.** Symptom: still fetchable from history. Fix: rotate
  the credential immediately, then decide whether to rewrite history.
- **`git bisect` says "some good revs are not ancestors"**. Symptom: the good commit is on
  a side branch. Fix: pick a good commit on the same line of history.
- **`git stash pop` conflicts.** Symptom: files present twice. Cause: pop restores but
  does not drop on conflict. Fix: resolve, commit or discard, then `git stash drop`.

## Anti-patterns

- `--force` instead of `--force-with-lease`.
- `reset --hard` when `reset --soft` or `restore` would do.
- Rebasing shared branches as a way to "clean them up".
- A history of forty commits on a pull request because nobody rebased before review.
- Commit messages that say "fixes" or "updates".
- Relying on client-side hooks for anything that must be enforced.
- Merging release branches repeatedly and never using a tag.
- Skipping `git clean -n` before `git clean -fd`.

## References

- Git reference: https://git-scm.com/docs
- Rebase: https://git-scm.com/docs/git-rebase
- Cherry-pick: https://git-scm.com/docs/git-cherry-pick
- Reset: https://git-scm.com/docs/git-reset
- Reflog: https://git-scm.com/docs/git-reflog
- Bisect: https://git-scm.com/docs/git-bisect
- Worktrees: https://git-scm.com/docs/git-worktree
- Submodules: https://git-scm.com/docs/git-submodule
- Signing commits: https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work