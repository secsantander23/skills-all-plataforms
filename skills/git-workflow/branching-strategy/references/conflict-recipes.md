# Branch maintenance recipes

Copy-pasteable command sequences for `branching-strategy`. Git subcommands and flags here are
stable across platforms; only shell syntax differs between POSIX shells and PowerShell.

## Inspect divergence before you rebase

```bash
git fetch origin

# How many commits on each side
git rev-list --left-right --count origin/main...HEAD

# Which files changed on main since the branch started (these are the conflict candidates)
BASE=$(git merge-base HEAD origin/main)
git diff --name-only "$BASE" origin/main

# How large is the drift
git diff --stat HEAD origin/main
```

`git rev-list --left-right --count A...B` prints `left right`, where left counts commits only
in `A` and right counts commits only in `B`. If left is large, rebase before writing more code.

## Rebase cleanly

```bash
git switch my-branch
git fetch origin
git rebase origin/main

# Test each replayed commit to find which one actually broke the build
git rebase --interactive origin/main
#   ... set `exec` lines if you want the suite to run per commit

# Abort if it goes wrong: the branch is restored exactly as it was
git rebase --abort

# Force push safely: refuses if the remote branch moved since the fetch
git push --force-with-lease
```

A minimal `.gitconfig` alias that is useful on every machine:

```text
[alias]
    lg = log --graph --decorate --oneline --all
    last = log -1 HEAD --stat
    unmerged = diff --name-only --diff-filter=U
```

## Conflict walkthrough

```bash
# 1. See what conflicted
git status --short
git diff --name-only --diff-filter=U

# 2. For one file, inspect the three sides
git ls-files -u -- path/to/file
#   stage 1 = merge base, stage 2 = ours, stage 3 = theirs

git checkout --ours  path/to/file   # take your branch's side wholesale
git checkout --theirs path/to/file  # take the incoming side wholesale

# 3. Or resolve by hand, then
git add path/to/file
git rebase --continue
```

If the conflict is unreadable rather than wrong, take theirs and re-apply your change on top:

```bash
git checkout --theirs path/to/file
git add path/to/file
git rebase --continue
```

`--ours` during a **rebase** is counterintuitive: Git swaps the labels so that "ours" is the
branch you were on (the commit being replayed) and "theirs" is `origin/main`. Verify with
`git status` before using either.

## Reduce a tangled conflict

Base merge: pull the merge base into both sides before merging.

```bash
BASE=$(git merge-base HEAD origin/main)

git switch my-branch
git merge "$BASE" -m "merge base into my-branch"
# resolve; now your branch shares ancestry with current main

git switch main
git merge "$BASE" -m "merge base into main"
# resolve; now main has the base-side fixes

git switch my-branch
git rebase main     # conflicts are now much smaller
```

## Multi-commit rebase

```bash
git rebase -i origin/main
```

In the editor that opens, each line is a command:

```text
pick  a1b2c3d fix(auth): reject rotated refresh tokens
pick  e4f5g6h feat(api): add pagination to GET /v1/orders
pick  i7j8k9l chore: bump deps
squash a1b2c3m WIP
```

Commands: `pick`, `reword`, `edit`, `squash`, `fixup`, `drop`, plus `exec`. To stop at a commit
and try something:

```text
edit  a1b2c3d fix(auth): reject rotated refresh tokens
```

then, after editing:

```bash
git add -A
git rebase --continue
```

To run the suite after every replayed commit, add a line after each pick:

```text
pick a1b2c3d fix(auth): reject rotated refresh tokens
exec npm test
```

Non-interactive form (scriptable, and the only form that works reliably in CI):

```bash
GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash origin/main
```

## Autosquash

```bash
git commit --fixup=<target-sha>    # message is discarded, target's message is kept
git commit --squash=<target-sha>   # both messages are kept
git rebase -i --autosquash origin/main
```

## Merge queue simulation

A merge queue is a hosting feature, but the underlying sequence is reproducible locally and
useful in tests:

```bash
git switch -c queue-test origin/main
git cherry-pick <sha-1> <sha-2> <sha-3>
<run the suite against the batch>
git push origin queue-test
```

The point: the batch is verified as a batch. Testing each PR against a different `main` head
hides integration failures between two PRs that are individually green.

## Release branch and hotfix

```bash
# Cut a release branch from a tag
git switch main
git pull --ff-only
git switch -c release/2.4 v2.3.1
git tag -a v2.4.0-rc.1 -m "2.4.0 release candidate 1"
git push origin release/2.4 --tags

# Hotfix from the released tag
git switch -c hotfix/2.3.2 v2.3.1
<fix and test>
git commit -m "fix(session): expire sessions after logout"
git tag -a v2.3.2 -m "Release 2.3.2"
git push origin hotfix/2.3.2 --tags

# Merge forward so the next release keeps the fix
git switch main
git merge --no-ff hotfix/2.3.2 -m "Merge hotfix/2.3.2 into main"
git push origin main

# CI guard: every release tag must be an ancestor of the integration branch
git merge-base --is-ancestor v2.3.2 origin/main && echo "ok"
```

## Clean up

```bash
git branch --merged main            # local branches fully merged into main
git branch --no-merged main         # the rest
git branch -d <branch>              # delete, refuses if unmerged
git push origin --delete <branch>   # delete the remote branch
git fetch --prune                   # drop refs for branches deleted on the remote
```

`--prune` should run on a schedule or in CI; otherwise `git branch -a` accumulates ghosts.

## Find who is on which branch

```bash
git branch --contains <sha>              # branches containing a commit
git log --oneline --author="Ada" --since="2 weeks ago"
git for-each-ref --format='%(committerdate:iso) %(authorname) %(refname:short)' refs/remotes/origin --sort=-committerdate | head -30
```