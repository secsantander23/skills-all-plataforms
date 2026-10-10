---
name: authoring-agent-skills
description: Covers writing, structuring, and debugging Agent Skills that load across OpenCode, Claude Code, and Codex. Use when the user asks to create or fix a SKILL.md, when a skill does not get discovered or triggered, when choosing descriptions or progressive disclosure for an agent skill, or when reviewing a skill folder with references, scripts, or assets.
metadata:
  opencode/autoinvoke: true
---

# Authoring Agent Skills

An Agent Skill is a folder whose `SKILL.md` tells an agent how to do one job well. The
model only sees the skill's `name` and `description` until it decides to load the body,
so the description is the entire discovery surface and the body is the entire execution
contract. Write both as if the reader has no other context.

## When to use this

- The user asks you to create, restructure, or debug a `SKILL.md`.
- A skill exists but the agent never loads it.
- A skill loads but the agent follows the instructions wrong.
- You are deciding whether a procedure belongs in a skill or in an always-loaded
  project instructions file such as `CLAUDE.md` or `AGENTS.md`.
- You are porting a skill between harnesses and need to know which fields are portable.

## Quick start

```markdown
---
name: db-migration-review
description: Reviews SQL schema migrations for lock risk, destructive DDL, and rollback safety. Use when the user mentions a migration, an ALTER TABLE, adding or dropping a column, or asks whether a schema change is safe to run in production.
metadata:
  opencode/autoinvoke: true
---

# Database Migration Review

Check every migration for production safety before it is merged.

## When to use this

- A diff adds a migration file under a `migrations/` directory.
- The user asks whether a schema change is safe to deploy.

## Procedure

1. Read the migration file and the schema version it targets.
2. Classify each statement by lock level. See
   [references/lock-matrix.md](references/lock-matrix.md) for the table.
3. Flag anything that is destructive without a guard.
4. Report findings as a numbered list. For each item give the file, the line, the
   risk, and the rewrite.

## Output format

- `BLOCKER` - will corrupt data or lock a large table.
- `WARN` - will lock or scan a large table without a timeout.
- `NOTE` - style or maintainability only.

## Checklist

- Migration is reversible or has a documented forward-only plan.
- Concurrent index creation is used where supported.
- `NOT NULL` additions have a default or are added in two steps.
```

Directory layout that supports the above:

```text
db-migration-review/
  SKILL.md
  references/
    lock-matrix.md
  scripts/
    check_migrations.py
```

## The core mental model

Three tiers load at three different times.

| Tier | Loaded | Budget | Content |
| --- | --- | --- | --- |
| Metadata | Always, for every installed skill | Small | `name`, `description` |
| Body | When the skill is selected | Under roughly 500 lines | Procedure, decision rules, output format |
| Resources | Only when the agent opens them | Unlimited | `references/`, `scripts/`, `assets/` |

Everything you write belongs in exactly one tier. Putting a 200-line lookup table in
the body costs tokens on every activation even when the task never needs the table.

## Anatomy of a SKILL.md

Required fields, per the Agent Skills specification at agentskills.io:

| Field | Required | Rules |
| --- | --- | --- |
| `name` | Yes | 1 to 64 characters, lowercase letters, digits, and single hyphens. Must match the containing directory name. |
| `description` | Yes | 1 to 1024 characters. Describes what the skill does and when to use it. |
| `license` | No | SPDX identifier or a filename in the skill folder. |
| `compatibility` | No | Up to 500 characters. Environment requirements. |
| `metadata` | No | Map of string keys to string values. |

The harness-independent shape is deliberately tiny. Anything else you add is a
harness extension, so keep it in `metadata` and label it.

## Writing the description

The description is the only text the model sees before loading. It has one job: make the
model choose this skill instead of a sibling, or instead of nothing.

Use this shape:

```text
Covers <capability list>. Use when the user asks about <trigger A>, <trigger B>, or
<trigger C>, when working on <situation D>, or when debugging <symptom E>.
```

Rules that survive testing:

- Front-load the distinguishing capability. Hosts with large skill sets may shorten
  descriptions, and a shortened description keeps the front of the string.
- Include the words a user would actually type. If people say "flake8" in chat, the
  description should contain "flake8" even if the body calls it "the Python linter".
- State boundaries. "Use for HTTP servers. Do not use for WebSocket or gRPC." prevents
  wrong triggers more effectively than a longer feature list.
- Write third person and present tense. "Reviews migrations" beats "This skill will
  help you review migrations".
- Keep it under 1024 characters. Longer descriptions get truncated or ignored.

Compare:

```yaml
# Weak: no trigger surface, no boundary.
description: Helps with PDFs.
```

```yaml
# Strong: capability, triggers, boundary, all in two sentences.
description: Extracts text and tables from PDF files, fills PDF forms, and merges or splits PDFs. Use when working with PDF documents, when the user mentions PDF, forms, or document extraction, or when a pipeline needs scanned pages turned into text. Not for OCR of images or for PDF generation from HTML.
```

Full technique, with before and after rewrites, is in
[references/description-writing.md](references/description-writing.md).

## Writing the body

State the procedure. A model follows instructions more reliably than it reconstructs
one from prose.

- Lead with a one-line statement of what the skill is for and the core mental model.
- Use a `## When to use this` section with concrete triggers, not categories.
- Give an explicit procedure as numbered steps with named inputs and outputs.
- Define an output shape. If the agent must produce a report, show the report.
- Add a `## Checklist` with verifiable conditions, not aspirations.
- Add `## Common mistakes` with real error text, symptom, root cause, and fix.
- Add `## Anti-patterns` for things that look right and are wrong.
- End with `## References` containing official documentation URLs.

Delete anything that does not change a decision. Background essays about the domain
belong in `references/` and are loaded only when the reader asks for them.

## Progressive disclosure

Move detail out of the body deliberately. The test is not line count, it is
conditionality: would a reader on a different task need this paragraph?

```markdown
Body:        "Look up the lock level for each statement. See
             [references/lock-matrix.md](references/lock-matrix.md)."
Reference:   The 40-row PostgreSQL lock matrix, only read when reviewing DDL.
```

Use relative paths from the skill root and keep references one level deep. Avoid chains
like `references/a.md` linking to `references/b.md` linking to `c.md`, because the agent
has to spend a tool call at every hop.

Scripts belong in `scripts/` when the procedure is deterministic. A validator, a
generator, or a bulk rewriter is a script. Judgement is not. Reference
`scripts/check_migrations.py` by relative path and say what it prints and what a
non-zero exit means.

```python
#!/usr/bin/env python3
"""Print migration files that contain statements requiring a lock-level review.

Exit codes: 0 clean, 1 risky statements found, 2 bad invocation.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RISKY = re.compile(
    r"\b(CREATE\s+INDEX|ALTER\s+TABLE|DROP\s+(TABLE|COLUMN|INDEX))\b",
    re.IGNORECASE,
)


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.sql")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = RISKY.search(line)
            if match:
                findings.append((path, lineno, match.group(0).upper()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="migrations", type=Path)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    findings = scan(args.root)
    for path, lineno, statement in findings:
        print(f"{path}:{lineno}: review {statement}")
    if findings:
        print(f"{len(findings)} statement(s) need lock review")
        return 1
    print("no risky statements found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## Portability across harnesses

Harnesses share the Agent Skills layout and disagree on discovery paths and extra
frontmatter keys. Ship the portable core and label everything else.

| Harness | Project discovery | Global discovery |
| --- | --- | --- |
| OpenCode | `.opencode/skills/<name>/SKILL.md`, plus `.claude/skills/` and `.agents/skills/` while walking up to the git worktree root | `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` |
| Claude Code | `.claude/skills/<name>/SKILL.md` in the start directory and every parent up to the repo root, plus nested `.claude/skills/` loaded when the agent touches files there | `~/.claude/skills/<name>/SKILL.md` |
| Codex | `.agents/skills/<name>/SKILL.md` scanned from the working directory up to the repo root | `$HOME/.agents/skills`, plus admin and system locations |

Portable core, safe everywhere:

```yaml
---
name: my-skill
description: What it does and when to use it, with trigger words.
license: MIT
metadata:
  opencode/autoinvoke: true
---
```

Harness-specific keys and where each one is honoured:

- OpenCode reads `metadata` keys `opencode/slash` and `opencode/autoinvoke`. Setting
  `opencode/autoinvoke` to `"false"` removes the skill from the model-facing
  `<available_skills>` list while leaving it selectable by name. Both are
  OpenCode-specific and ignored by other harnesses.
- Claude Code adds frontmatter fields such as `allowed-tools`, `disable-model-invocation`,
  `context: fork`, and `model`. These are Claude Code extensions, not part of the shared
  specification. Note in the body that they are harness-specific rather than assuming
  every host honours them.
- Codex adds `agents/openai.yaml` for display metadata and invocation policy. That file
  is ignored by the other two.

Because extra top-level frontmatter keys are Claude Code extensions, some packaging and
upload paths reject unknown fields. If a skill must be distributed broadly, restrict
frontmatter to the six specification fields and express anything else in the body.

See [references/harness-discovery.md](references/harness-discovery.md) for the full
matrix, precedence rules, and how to verify discovery in each harness.

## Naming the folder

`name` must equal the directory name, must match `^[a-z0-9]+(-[a-z0-9]+)*$`, must not
start or end with a hyphen, and must not contain `--`. Put the skill in its own directory
rather than as a flat `<id>.md`, because the directory is what carries `references/`,
`scripts/`, and `assets/`.

Use a verb-noun name that describes the job, not the tool. `db-migration-review` beats
`postgres-helper`. `ci-failure-triage` beats `github-actions`.

## Testing a skill

Test discovery and test execution separately.

Discovery test:

1. Put the skill where the target harness reads it.
2. Restart the session, or use the harness reload command if it has one.
3. Ask a question that matches the description using only words from the description.
4. Confirm the skill is offered. If it is not, work through the loading checklist below.

Execution test:

1. Invoke the skill explicitly by name.
2. Give it a real task from the repo, not a toy.
3. Compare the output against the output format you specified.
4. Cut every instruction the agent ignored and rewrite it as a checkable step.

Validate frontmatter mechanically before committing:

```bash
# Requires the reference validator; see scripts/validate_skill.py in this skill.
python scripts/validate_skill.py path/to/my-skill
```

It exits 0 when frontmatter parses, `name` matches the directory, and the name is a
valid slug. It exits 1 on any violation and prints the offending field.

## Checklist

- `name` matches the directory name and the `^[a-z0-9]+(-[a-z0-9]+)*$` pattern.
- `description` is third person, under 1024 characters, front-loads the capability, and
  contains words a user would type.
- `description` states at least one boundary where the skill should not trigger.
- Body opens with what the skill is for, then `When to use this`.
- Procedure is numbered steps with named inputs and outputs.
- Output format is shown, not described.
- Body is under roughly 500 lines and detail that is conditional lives in `references/`.
- Reference paths are relative and one level deep.
- Any script is deterministic, self-contained, and documented with its exit codes.
- Harness-specific frontmatter and files are labeled as such.
- Skill was tested for both discovery and execution in at least one harness.

## Common mistakes

- **Skill never loads.** Symptom: the agent answers without ever using it. Causes, in
  order of likelihood: the file is named `skill.md` instead of `SKILL.md`; the folder
  does not match `name`; the skill lives in a directory the harness does not scan; the
  harness config denies the skill by permission pattern; `opencode/autoinvoke` is set to
  `"false"` in OpenCode so it is hidden from model-facing selection. Fix the path first,
  then the name, then the permissions.
- **Frontmatter silently ignored.** Symptom: no error, but no skill name or description
  either. Cause: the opening `---` is not the first line of the file, or a blank line or
  a stray byte precedes it. Some harnesses then treat the whole file, delimiters
  included, as skill body text.
- **Wrong YAML error.** Symptom: a parse error mentioning line 3 or a mapping value.
  Cause: an unquoted `description` containing a colon followed by a space, or a value
  that starts with a character YAML reads as a type indicator. Quote the whole value.
- **Everything triggers the skill.** Symptom: the agent loads the skill for unrelated
  requests. Cause: a description that lists domains without boundaries. Add an explicit
  "not for" clause and name the neighbouring skill it should defer to.
- **Agent ignores half the body.** Symptom: it follows the first three steps and stops.
  Cause: a 900-line body where the operative rules are near the end. Move the rules to
  the top and the detail to `references/`.
- **Agent repeats the skill every turn.** Cause: instructions phrased as background
  knowledge rather than as an action. Write "Run X, then report Y" instead of "X is a
  useful approach".

## Anti-patterns

- Writing the skill as a tutorial. Tutorials explain; skills decide.
- Hiding the actual procedure under a section titled "Background" or "Rationale".
- One mega-skill that covers "everything about the backend". Split it. Two skills with
  narrow descriptions route better than one with a vague one.
- Embedding reference material in the body "just in case". The cost is paid on every
  activation.
- Depending on a specific harness's tool names as the only way to do something. Say
  "run the test command" or "search the repository", not "call the bash tool".
- Copying a prompt into the body that instructs the model to ignore its own rules.
  Instructions inside a skill have the same trust level as everything else in context.
- Adding undocumented frontmatter keys to make one harness behave better and shipping it
  without noting that other harnesses ignore them.

## References

- Agent Skills specification: https://agentskills.io/specification
- OpenCode Agent Skills: https://opencode.ai/docs/skills/
- Claude Code skills: https://docs.claude.com/en/docs/claude-code/skills
- Codex skills: https://developers.openai.com/codex/build-skills