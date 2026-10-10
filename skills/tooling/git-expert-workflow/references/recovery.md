# Recovery procedures

Order of operations when something went wrong: stop, look, then act. Committing
something speculative on top of a mistake makes the mistake harder to find.

## First moves

```bash
git status                 # what state is the repository in
git reflog --date=iso      # where HEAD has been, with timestamps
git log --oneline -20      # what the branch looks like now
```

If a rebase, merge, cherry-pick, or bisect is in progress, `git status` names it and
gives the command to abort it:

```bash
git rebase --abort
git merge --abort
git cherry-pick --abort
git bisect reset
```

Aborting returns you to the state before the operation started. It is always safe and
always the right first move when you are unsure.

## Recovering a lost commit

```text
14a3f9c  HEAD@{0}: reset: moving to HEAD~1
d77b21e  HEAD@{1}: commit: Add CSV export
```

```bash
# Find it.
git reflog | grep "CSV export"

# Bring it back on a new branch, so nothing is overwritten.
git switch -c recover-csv d77b21e

# Or apply it to the current branch.
git cherry-pick d77b21e
```

Create the recovery branch before doing anything else. Every new command can move `HEAD`
again, and you do not want to lose the sha you just found.

## Undoing different kinds of change

| You want to | Command | Notes |
| --- | --- | --- |
| Discard changes to one file | `git restore path/to/file` | Working tree only |
| Unstage everything | `git restore --staged .` | Keeps the code |
| Uncommit, keep changes staged | `git reset --soft HEAD~1` | Safest form of reset |
| Uncommit, keep changes unstaged | `git reset --mixed HEAD~1` | Also resets the index |
| Undo everything for one commit | `git revert <sha>` | Correct for pushed commits |
| Take a file from another commit | `git restore --source <sha> -- path` | Useful for partial reverts |
| Undo a merge on the mainline | `git revert -m 1 <merge-sha>` | Verify which parent is mainline |

### Reverting a merge

A merge commit has two parents. `-m` selects which one is treated as the mainline, and
getting it wrong reverts the wrong side.

```bash
# See the parents.
git log --merges --oneline -5
git show <merge-sha>            # shows the parents in the header

# Preview the effect before applying.
git diff <merge-sha>^1 <merge-sha>
git diff <merge-sha>^2 <merge-sha>

# Revert, keeping the first parent's line.
git revert -m 1 <merge-sha>
```

Preview the diff against both parents and confirm the result is what you expect before
committing the revert.

### Undoing a bad merge without a revert commit

Only safe on a branch nobody has pulled:

```bash
git reset --hard <sha-before-the-merge>
```

The merge commit is still in the reflog if you need it again.

## Recovering files deleted without being committed

```bash
# Unstaged deletion.
git restore path/to/file

# Staged deletion.
git restore --source HEAD --staged --worktree -- path/to/file

# Deleted in a commit that has not been pushed.
git revert <sha>

# Deleted in a pushed commit. Revert, and say so in the message.
git revert <sha>
```

## Splitting a commit after the fact

```bash
# Stop the rebase at this commit.
git rebase -i HEAD~3
# Change `pick` to `edit` on the commit you want to split.
```

```bash
# After the rebase stops:
git reset HEAD^                 # uncommit, keep everything in the working tree
git add -p                      # stage the first logical part
git commit -m "First part"
git add -A                      # stage the rest
git commit -m "Second part"
git rebase --continue
```

## Merging a branch that was already rebased

You rebased your branch after someone opened a pull request from it, and now the pull
request shows duplicate commits. There is no clean automatic fix.

Options, in order of preference:

1. Ask the reviewer to close and reopen the pull request, which recalculates the diff.
2. Force-push with lease and tell collaborators to reset their local branch.
3. Close the pull request, create a new branch at the same content, and open a fresh one.

Never solve this by merging both the old and new versions. That guarantees conflict
resolution work on every file you touched.

## Diagnosing "it works locally, fails in CI"

```bash
# Line endings.
git diff --ignore-cr-at-eol --stat

# File mode changes.
git diff --summary | grep 'mode change'

# Actual file mode.
git ls-files --stage path/to/file
```

Fixes:

```yaml
# CI: normalise line endings on checkout.
- uses: actions/checkout@v4
  with:
    # Ask git to convert line endings to the platform's on checkout.
    # Combined with a committed .gitattributes this removes the whole class of problem.
```

```gitattributes
# .gitattributes, committed
* text=auto eol=lf
*.bat text eol=crlf
*.ps1 text eol=crlf
*.png binary
```

An executable bit that changes on every filesystem (common with mounted or shared
volumes) is solved with `git config core.fileMode false` on that machine, or a committed
`.gitattributes` for content normalisation.

## Diagnosing a rebase that will not finish

```bash
git status
git rebase --show-current-patch     # the commit being applied
```

The conflict markers in the file name the region. After resolving:

```bash
git add <resolved files>
git rebase --continue
```

If the commit should be dropped instead:

```bash
git rebase --skip
```

If the whole thing should be abandoned:

```bash
git rebase --abort
```

Do not run `git commit` manually during a rebase. The rebase owns the commit sequence and
your manual commit will not be wired into it correctly.

## Recovering from a corrupted or interrupted operation

```bash
# Refuse to start anything while a previous operation is unfinished.
# If the lock is stale (the process died), confirm no git is running, then:
rm .git/index.lock
rm .git/rebase-merge/lock 2>/dev/null
rm .git/rebase-apply/lock 2>/dev/null

# Verify repository integrity.
git fsck

# Object integrity only.
git fsck --no-dangling
```

Never delete a lock file while another Git process is running. Confirm with a process
listing first.

## When a commit was force-pushed away

For the person who still has the branch locally:

```bash
git fetch origin
git reflog                                     # local copy is intact
git switch -c recover <sha>
```

For someone who fetched after the force push, the commit may still be reachable from the
remote's reflog or from a fork. Contact the person who pushed and ask; do not assume it
is gone until you have checked `git reflog --all` across the refs you have.

## Prevention

The recovery is easier when these hold:

- Commit before experimenting.
- Use a branch for anything non-trivial, even for ten minutes of work.
- Know the difference between `reset` and `revert` before you need it.
- Never combine history rewrite with content change in the same step.
- Do not delete branches you might need until the work is merged and released.