# Recipes: routine Git operations

## Starting work

```bash
git switch main
git fetch origin
git rebase origin/main              # or: git pull --ff-only
git switch -c feat/short-description
```

Keep the branch name describing the change, not the ticket alone. A name that says what
changed survives being read six months later.

## Preparing a pull request

```bash
# Update on the latest main without a merge commit.
git fetch origin
git rebase origin/main

# Resolve conflicts, then continue.
git status
git add <files>
git rebase --continue

# Squash the "address review comments" commits while you remember them.
git commit --fixup <sha-of-the-commit-being-reviewed>
git rebase -i --autosquash origin/main

# Push safely.
git push --force-with-lease
```

If the branch was never pushed, a plain `git push -u origin HEAD` is enough and carries
no risk.

## Reviewing someone else's work

```bash
# Fetch without touching the working tree.
git fetch origin
git log --oneline origin/main..origin/their-branch

# A throwaway worktree so you keep your own changes.
git worktree add ../review origin/their-branch
cd ../review
git rebase origin/main     # see conflicts they will see
```

The rebase in the worktree is the useful part: it tells you whether the branch merges
cleanly without disturbing anything you were doing.

## Squashing a branch into one commit

```bash
git switch -c squashed origin/their-branch
git reset --soft origin/main
git commit -m "The change, described as one commit"
```

For your own unpushed branch:

```bash
git rebase -i origin/main     # set every commit to squash or fixup, keep the first as pick
```

## Fixing the last commit's message

```bash
git commit --amend              # edit the message
git commit --amend --no-edit    # keep the message, e.g. after staging a forgotten file
```

Amending after pushing requires a force push. Use `--force-with-lease`, and only on a
branch nobody else has pulled.

## Finding when a line was introduced

```bash
# Log for a single line, following renames.
git log -L 42,48:src/payments.ts

# Which commit added or removed text matching a pattern.
git log -S "retry_policy" --oneline

# Which commit changed a path, following renames.
git log --follow --oneline -- src/legacy/checkout.ts

# Show the file at a commit.
git show <sha>:src/payments.ts
```

`-S` counts occurrences, so it finds commits that added or removed the string. `-L`
follows a line range through history.

## Comparing two revisions

```bash
# What changed between two commits.
git diff <sha-a> <sha-b>

# Only file names and change counts.
git diff --stat <sha-a> <sha-b>

# Only what one branch adds relative to another.
git log --oneline origin/main..origin/feature

# Ignore whitespace when deciding whether two versions differ.
git diff -w <sha-a> <sha-b>

# What is on this branch that is not on main.
git log --oneline --left-right origin/main...HEAD
```

## Working with tags and releases

```bash
# Tag the current commit for a release.
git tag -a v2.1.0 -m "Release 2.1.0"
git push origin v2.1.0

# What is in this release.
git log --oneline v2.0.0..v2.1.0

# Which release contains a commit.
git tag --contains <sha>

# Generate release notes from the log.
git log --pretty=format:'* %s (%h)' v2.0.0..v2.1.0

# Move to the tag for a hotfix investigation.
git switch --detach v2.1.0
```

## Cherry-picking a fix onto a release branch

```bash
git switch release/2.1
git fetch origin
git rebase origin/release/2.1

git cherry-pick -x <fix-sha>

# Update the tag to include the fix. Only if the tag has not been published widely.
# Better: cut a new patch tag.
git tag -a v2.1.1 -m "Release 2.1.1"
git push origin v2.1.1
```

`-x` records the original commit hash, which is what makes the backport traceable six
months later.

## Emergency: undo a pushed commit

```bash
# If nobody has pulled yet and it is your own branch.
git reset --hard HEAD~1
git push --force-with-lease

# If it is shared. Always correct for published history.
git revert <sha>
git push
```

## Parallel work with worktrees

```bash
git worktree add ../hotfix -b hotfix/login-crash main
git worktree add ../review origin/feature/new-checkout

# Work in ../hotfix without disturbing the main directory.
cd ../hotfix
# ...

# Clean up.
git worktree list
git worktree remove ../hotfix
git worktree prune
```

A useful pattern: one worktree per long-running branch, so switching branches in the
main directory is never necessary and the working tree is never stashed.

## Repository configuration worth setting

```bash
# Sensible defaults for a new clone.
git config --global init.defaultBranch main
git config --global pull.ff only           # no surprise merge commits on pull
git config --global fetch.prune true        # delete refs for deleted remote branches
git config --global diff.colorMoved z       # colour moved lines differently
git config --global rebase.autosquash true  # --autosquash without typing it

# Signing, if your forge verifies it.
git config commit.gpgsign true
git config user.signingkey <your-key-id>
git config tag.gpgsign true
```

`core.autocrlf` and `core.fileMode` are per repository, not global, because correct values
depend on the filesystem. Set them once per clone after reading the repository's
`.gitattributes`.

## Keeping history readable

- One logical change per commit. If the message needs "and", split it.
- Write the subject as an imperative sentence under about 50 characters.
- Reference the ticket in the body, and explain why, not what. The diff already says
  what.
- Rebase before review so the reviewer reads forty meaningful commits, not three hundred.
- Land a dependency bump on its own so a regression can be bisected to it.