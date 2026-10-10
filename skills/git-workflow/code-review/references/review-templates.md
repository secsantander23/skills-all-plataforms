# Review templates and escalation paths

Supporting material for the `code-review` skill.

## Severity ladder

Use this when you need a consistent answer to "does this block the merge?".

| Level | Definition | Examples | Action |
| --- | --- | --- | --- |
| Blocker | Data loss, security hole, or guaranteed incorrect behavior on a realistic input | Unbounded delete without a filter, missing authorization check, wrong unit | Block; fix before merge |
| Major | Incorrect on an edge case that will occur in production | Off-by-one at the last page, unhandled null from a new API field, no rollback path for a migration | Block, or fix in the same PR |
| Minor | Real problem, narrower trigger | Slow path that times out only on the largest tenants, missing log on a failure branch | Fix now or file a linked issue |
| Nit | Style or preference | Formatting, naming taste, comment wording | Author's call |

The ladder is about *consequence*, not about how many lines the fix touches. A one-character
fix can be a blocker (a missing authorization check); a two-hundred-line refactor can be a nit.

## Review comment templates

### Correctness

```text
issue: <what breaks> (<file>:<line>)

Trigger: <concrete input or sequence>
Expected: <what should happen>
Actual: <what the code does>
```

### Security

```text
issue: authorization missing on <route/handler> (<file>:<line>)

Any authenticated user can <action> by sending <request>. The handler
checks authentication (<file>:<other line>) but not ownership of
<record id>. Compare with <handler that does check it>.
```

### Performance

```text
suggestion: N+1 query in <loop> (<file>:<line>)

The loop calls <function>, which issues one query per item. For a
tenant with 5,000 <records> that is 5,000 round trips. Fetching with
`whereIn` and indexing the result set would be one query. Not
blocking for the current data volume; flagging because it will not
recover on its own.
```

State the volume assumption. A performance comment without a workload is a guess.

### Test gap

```text
issue: no test covers the failure path (<file>:<line>)

This branch is reachable when <condition>, but the suite only exercises
the success path. A test that asserts <observable> when <condition>
holds would pin the behavior.
```

## Self-review script

Adapt paths to the project. The goal is a repeatable pass, not a specific toolchain.

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE="${1:-origin/main}"

echo "== commits =="
git log --oneline "${BASE}"..HEAD

echo
echo "== diff stat =="
git diff --stat "${BASE}"...HEAD

echo
echo "== whitespace errors =="
git diff --check "${BASE}"...HEAD || true

echo
echo "== files changed =="
git diff --name-only "${BASE}"...HEAD

echo
echo "== search for debug leftovers =="
git diff "${BASE}"...HEAD | grep -nE '^\+.*(console\.log|debugger|fmt\.Print|TODO|FIXME|XXX)' || true

echo
echo "== search for likely secrets in added lines =="
git diff "${BASE}"...HEAD | grep -nEi '^\+.*(api[_-]?key|secret|password|token|private[_-]?key)' || true

echo
echo "== tests =="
<project test command>
```

Run it, read its output, then read the diff line by line. The script finds the shallow
problems; only you find the real ones.

## Splitting an oversized change

A decomposition that usually works:

1. **Pure refactor** (no behavior change) — renames, moves, dead code removal. Lands alone so
   the diff can be skimmed with `git diff -w`.
2. **Add the new path, unused.** New function, new column, new route — no callers yet. Merge
   is safe because nothing depends on it.
3. **Wire it up behind a flag.** Default off. Merged, observable, reversible.
4. **Enable.** Flip the default; delete the old path separately.

At every step the tree builds, tests pass, and the diff is reviewable. Step 4 can be reverted
without reverting steps 1 through 3.

## Anti-pattern in review comments

- "Why?" alone, with no stated assumption. The author answers, you both lose a round trip.
- A wall of feedback as one comment. Split it.
- Tone that makes the author want to close the tab. Sarcasm in a permanent, searchable thread
  has an outsized cost.
- Reviewing a diff you have not read end to end and approving anyway.