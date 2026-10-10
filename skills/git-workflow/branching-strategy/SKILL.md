---
name: Branching Strategies
description: Covers trunk-based development, GitHub Flow, and gitflow, including the pain of long-lived branches, how feature flags change branching decisions, merge conflict resolution strategies, release and hotfix branch handling, and a decision table for choosing a strategy. Use when the user asks how to structure branches, why merges are getting harder, how to handle a release branch or hotfix, or whether they should adopt trunk-based development.
metadata:
  opencode/autoinvoke: true
---

# Branching Strategies

A branching strategy is a policy about how long a change lives away from the main line and who
is allowed to integrate it. Git does not care which one you pick; it cares how long branches
diverge. Every problem below follows from divergence time.

## When to use this

- Choosing a branching model for a new repository or a team.
- Merges are getting slower, or conflicts are appearing in files nobody touched.
- Deciding whether release branches or hotfix branches are worth the cost.
- Introducing feature flags and working out what that means for branch lifetime.
- Resolving merge conflicts and choosing conflict strategy per file.
- Cutting a release or shipping an out-of-band fix.

## Quick start

The default that works for most continuously deployed services: one long-lived branch
(`main`), protected, always releasable.

```bash
git switch main
git pull --ff-only

git switch -c feat/invoice-pdf
# ... work ...
git fetch origin
git rebase origin/main       # keep the branch current; rebase frequently
git push --force-with-lease  # safe force push: refuses if someone else pushed
```

Open a PR. CI must be green. Merge with a **merge commit** (preserve the branch context) or
**squash** (one clean commit on main), decided per repository, not per PR.

## Comparing the three common strategies

| | Trunk-based | GitHub Flow | gitflow |
| --- | --- | --- | --- |
| Branches | `main` + short-lived feature branches | `main` + one feature branch | `main`, `develop`, `release/*`, `hotfix/*`, `feature/*` |
| Main branch state | Always releasable | Always releasable | Only releasable at tag time; receives merges, not deploys |
| Merge policy | Direct to `main` after CI, or PR | PR + review required | Feature into `develop`, `develop` cherry-picked to `main` at release |
| Release | Every merge to `main` deploys | Every merge to `main` deploys | Deploy from `main` at release time only |
| Branch lifetime | Hours to days | Days | Weeks to months |
| Merge queue | Required | Required | Not used |
| Fits | Most web services, most teams | OSS libraries, small teams | Versioned software with many supported releases |

### Trunk-based development

Developers branch, work briefly, and integrate to `main` many times a day. `main` is always
deployable. Integration is done with a **merge queue** so a batch of PRs is tested together
against the same `main` head — the branch you tested is the branch that merges.

```bash
# Merge queues are a hosting-platform feature, not a git command.
# Configure under repository settings -> Rules -> Merge queue (exact UI varies by host).
```

The critical property: the interval between branching and merging is measured in hours. If it
is measured in weeks, you are running gitflow without the release discipline.

### GitHub Flow

Trunk-based plus mandatory PR review. One feature branch per piece of work, merged into
`main` after review, deployed on merge. Best for open-source libraries and small teams where
review is the main control.

### gitflow

Originally designed for software distributed as versioned artifacts that must support many
simultaneously installed versions on client machines (desktop apps, libraries with a long
support window). The model:

- `main` — receives only commits tagged as releases.
- `develop` — the integration branch for the next release.
- `feature/*` — off `develop`.
- `release/*` — off `develop`; only version bumps and release-blocking fixes. Merged into
  `main` (tagged) and back into `develop`.
- `hotfix/*` — off `main`; merged into `main` (tagged) and into `develop`.

The cost is that `main` and `develop` accumulate divergence, so the final release merge is a
large, risky integration rather than a series of small ones. For a web service that deploys
continuously, `develop` buys you nothing: you can simply not deploy.

## Long-lived branch pain

Symptoms, root causes, and fixes:

- **Conflicts in unrelated files.** Two branches changed a shared config or lockfile for weeks.
  Fix: shorten branch life; rebase onto the main line daily.
- **"Works on my branch."** Test data or migrations that only exist on the branch.
  Fix: run the branch's code against the main line's database and config.
- **Stale assumptions.** The branch was written against an API that changed after it started.
  Fix: rebase early and often; a branch that is 2000 lines behind is effectively a rewrite.
- **Untestable integration.** The release is the first time the two halves run together.
  Fix: merge more often, behind flags if necessary.
- **Merge conflicts in lockfiles.** Regenerate the lockfile on the *newer* main line, then
  re-apply the branch's dependency changes, and let the package manager resolve:

```bash
git fetch origin
git rebase origin/main
# if the lockfile conflicts:
git checkout --ours package-lock.json   # hmm: prefer explicit regeneration instead
```

Do not hand-merge lockfiles. Take the main line's version, re-run the install, and let the
package manager recompute. On Windows PowerShell the checkout syntax is identical; only the
shell differs.

## Feature flags change the math

A feature flag decouples *deployment* (code on every machine) from *release* (code active for
users). That collapses the reason long-lived branches exist, which is why flags and branching
strategies are the same conversation.

```text
1. Merge the new code path to main behind a flag, default off.
2. It deploys and runs in production, dark.
3. Turn it on for 1% of traffic, then 100%.
4. Delete the flag, the old path, and the branch.
```

With flags:

- Branches shorten to "one flag's worth of code", typically days.
- Risky changes merge *dark* — reviewable because they are not active, safe because flipping
  back is instant.
- Release branches become optional: you release by flipping flags, not by merging a branch.
- The cost moves: flag debt, two code paths to test, and a flag someone forgot to remove.

Rules that keep flags from becoming a liability:

- Give every flag an owner and a removal date at creation time. A flag with no removal plan
  becomes a permanent second implementation.
- Default new flags to off. A flag defaulting to on for new instances still needs an on-path
  test.
- Never leave an emergency kill switch out of the design review for a risky change.

## Merge conflict resolution

```bash
git fetch origin
git rebase origin/main        # replays your commits; conflicts resolved one commit at a time
# or
git merge origin/main          # a single merge commit; conflicts resolved once
```

```bash
# During a conflict, list the conflicted paths
git diff --name-only --diff-filter=U

# Inspect the three stages: base (stage 1), ours (stage 2), theirs (stage 3)
git checkout --ours  path/to/file
git checkout --theirs path/to/file
git show :1:path/to/file > /tmp/base

git add path/to/file
git rebase --continue        # or: git rebase --skip, git rebase --abort
```

Merge strategies by file type:

| File | Strategy | Why |
| --- | --- | --- |
| Source code | Manual, take both changes, re-read the result | Intent matters more than text |
| Tests | Manual, and update the test to match the merged behavior | Conflicts here hide regressions |
| Generated code | Discard one side, regenerate | Never merge generated diffs |
| Lockfiles | Take the main line's, reinstall, re-resolve | The tool is the merge driver |
| Documentation | Take both, then dedupe headings | Cheap to re-read |

Techniques for hard conflicts:

- **Extract, not merge.** Both branches changed the same region. Move the shared code into its
  own function first, then merge the callers.
- **Base merge.** Merge the shared ancestor into both branches first
  (`git merge <merge-base>` in each), then merge the branches; conflicts get smaller.
- **Start clean.** If the conflict is too tangled to read, `git rebase --abort`, rebase onto the
  newest main line, and re-apply your work as smaller commits.

## Release branches and hotfixes

**Do you need a release branch?** Only if you must run two or more incompatible versions of the
application simultaneously and cannot do it with a feature flag.

Cutting one:

```bash
git switch main
git pull --ff-only
git switch -c release/2.4 main
# cherry-pick only release-blocking fixes onto this branch
git tag -a v2.4.0 -m "Release 2.4.0"
git push origin release/2.4.0 --tags
```

Rules that keep it viable:

- Freeze feature work on the release branch at a set date; everything after goes to the next
  release.
- Fix the release branch forward only — never merge the release branch back into `main`.
- Keep the number of supported release branches small. Each one is a full test matrix, a
  security patch obligation, and a merge-forward target.

**Hotfix** (fix needed in production now):

```bash
git switch -c hotfix/2.4.1 v2.4.0
# fix, add a regression test, verify locally
git commit -m "fix(api): reject expired tokens in the v1 introspection path"
git tag -a v2.4.1 -m "Release 2.4.1"
git push origin hotfix/2.4.1 v2.4.1
```

Then merge forward into `main` (and `develop`, if using gitflow) so the fix is not lost:

```bash
git switch main
git merge --no-ff hotfix/2.4.1 -m "Merge hotfix/2.4.1 into main"
```

The common failure: hotfixing from a tag and never merging forward, so the next release
reintroduces the bug. Add a CI check that fails if a tag's commit is not an ancestor of the
main line.

## Choosing a strategy

| Situation | Choose | Because |
| --- | --- | --- |
| Web service, continuous deploy | Trunk-based + merge queue | Short branches, fast integration |
| Open-source library, few maintainers | GitHub Flow | Review is the control; no deploy step |
| Desktop app or on-prem software supporting several installed versions | gitflow | Multiple simultaneous releases |
| Mobile app with staged rollout and App Store review | Trunk-based + feature flags | Store review is the long lead time, not the branch |
| Regulated change with an audit trail | Trunk-based + signed commits + protected `main` | Auditability comes from history, not from branches |
| Embedded or hardware-coupled software | gitflow | Release trains and long-lived support |

## Checklist

- [ ] One long-lived integration branch; everyone knows which.
- [ ] That branch is always deployable and always green, or you are deliberately not trunk-based.
- [ ] Branch lifetime target: under two days. Measure it.
- [ ] The branch is protected: required reviews, required CI, no force pushes, linear history if
      you rely on `git bisect`.
- [ ] A merge queue is configured if the team takes CI result latency seriously.
- [ ] `main` is configured as the default branch on the host, and new contributors land on it.
- [ ] Release branches are countable on one hand, with an end date.
- [ ] Hotfixes are merged forward into the integration branch the same day.

## Common mistakes

**Adopting trunk-based while keeping three-week branches.** The name is right, the practice is
not. You get continuous conflicts with all of trunk-based's overhead. Fix the branch lifetime,
not the label.

**Merging `develop` into `main` as a release.** A large integration commit is the moment most
production incidents happen. If you use gitflow, deploy `main` on every merge from `develop` so
the integration is continuous and the tag is a label, not a cutover.

**Forgetting to merge hotfixes forward.** The bug returns in the next release. Enforce
ancestry in CI.

**Rebasing a branch someone else is working on.** Their next `git pull --rebase` silently
discards your commits. Communicate, or use a merge.

**Force pushing with `--force`.** Use `--force-with-lease`; it refuses when the remote moved
since your last fetch. Reserve `--force` for repositories you are certain nobody else fetches.

**Deleting `main` and renaming branches.** Deploy systems, CI, and host settings all default to
`main` or `master`. Change the default branch in repository settings, verify CI, then delete.

## Anti-patterns

- **A `develop` branch in a continuously deployed service.** It is a long-lived branch with none
  of gitflow's benefits.
- **Branches named after people** (`alice-feature`). Ownership in branch names hides real
  ownership; use code owners.
- **One branch per ticket when tickets are a day of work.** Creates ten merges a day and ten
  integration points. Group by deployable unit instead.
- **Branching as a substitute for feature flags.** Flags make merges reversible; branches do
  not. Keeping both doubles your merge burden.
- **Merging the default branch into a feature branch and committing the merge.** Use rebase, or
  merge locally without committing, so history stays linear.
- **Running release branches "just in case".** An unmaintained release branch is a security
  hole: it accumulates unpatched dependencies.
- **Removing a default branch protection rule to unblock a hotfix.** Ship the hotfix through
  the protected path; bypass rules should be time-boxed and audited.

## References

- Trunk Based Development: https://trunkbaseddevelopment.com/
- GitHub Flow: https://docs.github.com/en/get-started/using-github/github-flow
- Gitflow (the original model description and diagram):
  https://nvie.com/posts/a-successful-git-branching-model/
- Atlassian branching strategies overview:
  https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow
- Git branching documentation: https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell
- `git rebase` reference: https://git-scm.com/docs/git-rebase
- `git merge` reference: https://git-scm.com/docs/git-merge
- `git merge-file` (three-way merge driver): https://git-scm.com/docs/git-merge-file
- GitHub on protected branches: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- GitHub merge queue: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- Git tags: https://git-scm.com/book/en/v2/Git-Basics-Tagging
- Martin Fowler on feature toggles: https://martinfowler.com/articles/feature-toggles.html