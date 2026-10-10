---
name: Code Review as a Discipline
description: Covers reviewing changes for correctness, design, and risk including security, data loss, and concurrency, how to rank findings by severity, how to write actionable and kind review comments with praise, question, suggestion, and nit conventions, review checklists, self-review before requesting review, and when a diff is too large to review properly. Use when the user asks how to review code or a pull request, is preparing a PR for review, is acting as a reviewer, or wants to speed up a stalled review queue.
metadata:
  opencode/autoinvoke: true
---

# Code Review as a Discipline

Review is a risk-management activity, not a taste contest. The output is a ranked list of
defects the author would have shipped, each with enough context that the fix is obvious. Style
opinions belong in linters; your attention is expensive, so spend it on things that break.

## When to use this

- Reviewing a pull request or a diff before merge.
- Preparing your own change for someone else to review.
- Deciding whether to approve, request changes, or ask for a smaller PR.
- Working out why a review queue is slow or why reviews are being ignored.
- Deciding whether a change needs more design discussion instead of line comments.

## Quick start

A repeatable review pass, in order:

```bash
# 1. Scope: what changed, and how much
git fetch origin
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD

# 2. Read the description and the plan before the code
git diff origin/main...HEAD -- '*.md' 'docs/**'

# 3. Read the code in small slices, oldest commit first
git show --stat HEAD~1
git show HEAD~1

# 4. Verify the change actually does what it claims
git checkout HEAD~1 -- . && <run the test suite>   # if you test intermediate commits
```

Order matters: reading the diff before the PR description means reviewing the code for what it
appears to do instead of what it was supposed to do.

## What to review, in priority order

### 1. Correctness

Does it do what the description says, on the inputs that actually occur?

- Off-by-one and boundary conditions: empty input, single element, maximum size, null, zero,
  negative, unicode, extremely large numbers.
- Error paths: is the failure handled, and is the error surfaced with enough context to fix it?
  A swallowed exception that leaves a half-written record is worse than a crash.
- State: is the new state reachable from every entry point, or only from the path the author
  tested?
- Concurrency: two callers writing the same row, a check-then-act race, a read-modify-write on
  a shared cache, a TOCTOU gap between validation and use.
- Time and ordering: timezones, clock skew, leap seconds are optional, but "expires at
  epoch seconds vs milliseconds" is a real and common bug. Never store durations and absolute
  times in the same unit.

### 2. Risk surface

Ask what the worst realistic outcome is, then check whether the change can cause it.

- **Data loss**: destructive migrations, `TRUNCATE`, `DROP`, `rm -rf`, unbounded `DELETE`
  without a `WHERE`, overwriting a file the process also holds open, a queue consumer that
  acknowledges before persisting.
- **Security**: authentication and authorization on every new route; object-level checks (is
  *this* record owned by *this* user, not merely "is the user logged in"); injection sinks
  (shell, SQL, template, deserialization); secrets in code, logs, or fixtures; unbounded
  input reaching a regex or a parser.
- **Blast radius**: what breaks if this is deployed twice, rolled back, or runs concurrently
  with the previous version during a rolling deploy?
- **Data loss on deploy**: a migration that is not backward-compatible with the still-running
  old code. If old code writes a shape the new code cannot read, the deploy is not reversible.

### 3. Design

- Does the abstraction sit where the volatility is, or has it been pushed one layer too far?
  Premature generalization is a maintenance cost with no current benefit.
- Does the change widen a public surface (exported function, HTTP route, config key, DB
  column) that you now have to support forever?
- Is the new dependency pulling its weight, and is it licensed compatibly?
- Will this be testable in six months, or has it coupled logic to an ambient singleton, the
  clock, or the network?

### 4. Fit with the codebase

- Does it follow existing patterns, or invent a parallel one? New style in one file is a
  second style for the next person to learn.
- Is the naming honest about types and lifetimes? Does the comment explain *why*, not *what*?
- Is there dead code, a commented-out block, or a debug `print` left in?

## Ranking findings

Mark each finding so the author knows whether it blocks the merge.

| Prefix | Meaning | Blocks merge |
| --- | --- | --- |
| `nit:` | Style, taste, optional. Author may decline. | No |
| `question:` | You do not understand something and it might be wrong. | Depends on answer |
| `suggestion:` | A concrete improvement you believe is correct. | Usually no |
| `issue:` | A defect: correctness, security, data loss, or design that will hurt. | Yes |

Rules that make this work:

- One issue per comment. A comment listing four problems gets partially addressed.
- Every `issue:` must be grounded: name the input, the state, or the sequence that breaks. If
  you cannot state how to trigger it, it is a `question:`.
- Quote the line and reference it as `file:line` so the author can jump to it.
- Put comments that apply to many lines in one top-level comment, not on every line.

## Writing the comment

Structure that works: **observation → consequence → concrete fix.**

```text
issue: `refreshToken` is rotated before the new one is persisted
The rotation at line 84 invalidates the old token in the store, but the
write of the new token at line 91 happens after. If the process dies
between them, the user is locked out with no valid token.
Move the write inside the same transaction as the invalidation, or
invalidate on first use of the new token instead.
```

```text
question: why is the retry count read from config here but hardcoded
to 3 in `sendEmail`?
If this is a different limit it should be a named constant; if it is the
same one, the config value is being ignored.
```

```text
suggestion: extract the three-line "truncate to 200 + append ellipsis"
into a helper
It appears in `formatPreview` and `renderCard`. Not blocking.
```

```text
nit: trailing comma in the last element
```

Kindness is not softness, it is precision. "This is wrong" with a reproduction is kinder than
"this looks off". "Why did you do it this way?" alone is not — add your assumption: "I read
this as X. If you meant Y, ignore this."

Praise public, critique specific. "Nice catch on the transaction ordering" lands; "good job"
does not.

## Review checklist

Correctness:

- [ ] Happy path traced end to end, including the response written back.
- [ ] Empty, single, boundary, and very large inputs handled.
- [ ] Error and cancellation paths return something meaningful.
- [ ] No check-then-act races or shared mutable state without synchronization.
- [ ] Time, units, and timezones consistent.

Risk:

- [ ] Auth and ownership checks on every new or changed entry point.
- [ ] No user input reaching a shell, SQL, template, or deserializer without parameterization.
- [ ] No secrets in code, fixtures, snapshots, or log lines.
- [ ] Migrations are backward-compatible with the currently deployed code.
- [ ] Destructive operations are scoped and reversible, or explicitly justified.

Quality:

- [ ] Tests cover the new behavior and would fail without the change.
- [ ] Public API changes are intentional and documented.
- [ ] Comments explain why; no commented-out code.
- [ ] Linter and formatter clean, no disabled rule left behind.

## Self-review before requesting review

Run your own diff first — it is the cheapest possible review.

```bash
git diff origin/main...HEAD            # read every line you changed
git diff --check                       # whitespace errors
npx prettier --check .                # or the project's formatter
npx eslint .                           # or the project's linter
<run the unit and integration tests>
```

Then ask yourself the reviewer's questions: is the failing case covered by a test? Does the PR
description say what it does *not* change? Would I approve this from someone I do not know?

Self-review is not a formality. Most review comments you would receive are things you can find
yourself in ten minutes.

## Review bottlenecks

Symptoms and causes:

- **Everything is "LGTM".** Reviewers are skimming because PRs are too big. Fix the PR size,
  not the reviewers.
- **Review is serialized behind one person.** Make code owners a group, not an individual, in
  `CODEOWNERS`.
- **Review and CI race.** Reviewers re-read files CI is about to comment on. Run CI first,
  then request review.
- **No timebox.** An unreviewed PR blocks the author's whole branch. Set an expectation
  (a day) and a deadline policy.
- **Relitigating settled design.** Record design decisions in an ADR or issue and link it in
  the PR so the review does not reopen it.
- **Rubber-stamp reviewers rotate through 200-line diffs.** Weight review by risk: small,
  isolated, well-tested changes get light review; auth, data model, and money paths get deep
  review from a named owner.

## When a change is too large to review

Signs a review has failed: the reviewer asks you to explain half the diff, comments are
single-line typos on a 3000-line change, or the author says "you will have to trust me on the
database part."

What to do:

- **Split by concern.** Separate the refactor from the behavior change. A rename should land
  first, alone, so the behavior diff reads cleanly.
- **Separate mechanism from policy.** Land the plumbing behind a flag, then flip the flag in a
  second PR.
- **Stage migrations.** Schema first (backward compatible), then application code, then the
  cleanup drop in a later PR.
- **Ship the risky part dark.** Merge behind a feature flag with no callers, validate in
  production, then enable.
- **Request design review, not line review.** For a change that genuinely cannot be split,
  write the design first and get it agreed before the code exists.

## Common mistakes

**Reviewing the diff you wish you had received.** If the description and the code disagree,
that is a finding: stop and ask before reading further.

**Leaving a comment with no actionable fix.** "Consider extracting this" adds a round trip
where the author guesses. Show the two-line version.

**Treating a nit as a blocker.** Mixed severity trains authors to ignore all of your comments.
Keep `nit:` genuinely optional.

**Approving a change you did not run.** "Tests pass in CI" is evidence; running the failing
case yourself is better. If you cannot run it, say so in the review rather than implying you
did.

**Letting the diff dictate the review.** Read the description first, write down what you expect,
then compare against the diff.

## Anti-patterns

- **Rewriting the author's code in comments instead of raising a design issue.** Large
  suggested rewrites in a review thread are unreadable; open an issue and describe the shape.
- **Approving with unaddressed `issue:` comments.** Approve when the blocking findings are
  resolved, and say which ones you consider closed.
- **Reviewing as a search-and-replace linter.** Line-level typos are automated; your attention
  belongs on logic.
- **"Drive-by approving"** to unblock someone you have not actually read. It converts a review
  queue into a no-op.
- **Commenting on things that will not change.** If you will not fight for it, drop it.
- **Using review to negotiate scope after the code exists.** Scope changes are cheaper as an
  issue before the branch is 2000 lines.

## References

- Git book, "Git Tools - Bisecting, Branching and Rebasing" and reviewing workflow:
  https://git-scm.com/book/en/v2/Git-Tools
- `git diff` reference: https://git-scm.com/docs/git-diff
- GitHub documentation on pull requests and reviews:
  https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/getting-started/about-pull-requests
- GitHub on protected branches and required reviews:
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- GitHub CODEOWNERS documentation: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- Google Engineering Practices, "The CL Author's Guide" and code review guidance:
  https://google.github.io/eng-practices/review/
- OWASP Code Review Guide: https://owasp.org/www-project-code-review-guide/
- Conventional Commits (for PR titles that survive squash merges):
  https://www.conventionalcommits.org/en/v1.0.0/