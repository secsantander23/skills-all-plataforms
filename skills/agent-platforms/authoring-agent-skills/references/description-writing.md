# Writing descriptions that actually trigger

The `description` is the entire pre-load context. Harnesses list skills by name and
description, then the model picks. If the description does not contain the words the
user used, the skill cannot be selected.

## The shape

```text
Covers <capability 1>, <capability 2>, and <capability 3>. Use when the user asks about
<trigger A>, <trigger B>, or <trigger C>, when working on <situation D>, or when
debugging <symptom E>. Not for <adjacent thing>.
```

Three sentences or fewer. Front-load the capability because some hosts shorten
descriptions before matching.

## Vocabulary sources

Pull trigger words from four places, in this order of reliability:

1. Words the user already types. Search your issue tracker, chat logs, or review
   comments for how people describe the failure.
2. Error strings the tool emits. If a skill fixes `ERESOLVE unable to resolve dependency
   tree`, put that string in the description.
3. File and directory names. People say "the migration", not "the DDL statement".
4. Tool and library names, used verbatim, including capitalisation and version-free
   spelling.

Do not paraphrase. "Dependency resolution" will not match "ERESOLVE".

## Failure catalogue

### Too vague

```yaml
description: Helps with database work.
```

Problems: no capability list, no triggers, no boundary. The model has nothing to match
against, so the skill fires for schema design, query tuning, and connection issues
alike, or never fires at all.

Fix:

```yaml
description: Reviews and writes SQL migrations for PostgreSQL and MySQL, covering lock levels, backfills, and rollback safety. Use when the user mentions a migration, an ALTER TABLE, adding or dropping a column, creating an index, or asks whether a schema change is safe to deploy.
```

### Capability without triggers

```yaml
description: Provides expert guidance on dependency management across package managers, including installation, version resolution, and lockfiles.
```

Problem: reads like an abstract. A user asking "why does pnpm install fail with
ERESOLVE" produces no overlap with the sentence.

Fix: add the literal tokens. "npm", "pnpm", "yarn", "lockfile", "peer dependency",
"ERESOLVE", "workspaces".

### No boundary

```yaml
description: Debugs React components, including rendering, state, and performance issues.
```

Problem: overlaps with any other React skill and with generic debugging advice. Two
skills with overlapping descriptions produce coin-flip selection.

Fix: name what the skill does not do and which sibling owns that case.

```yaml
description: Debugs React component rendering and hook state problems in function components. Use when a component renders the wrong value, an effect runs twice, or state updates are lost. Not for routing, data fetching libraries, or server components.
```

### Description duplicates the body title

If the body already opens with a clear statement of scope, the description does not need
to repeat it. It needs the words a user types.

### First-person or marketing voice

Write in third person and present tense. "Handles X" not "This skill will help you
handle X" and never "Powerful X handling". The description competes with other skills
in a list; it should read like a catalogue entry.

## YAML safety

Unquoted scalars break when the value contains a colon followed by a space, or when a
value looks like a type indicator. Quote the whole description.

```yaml
# Risky: ": " appears mid-value and the value starts with a colon-free token.
description: Covers X: the new pipeline. Use when...

# Safe.
description: "Covers X: the new pipeline. Use when..."
```

Avoid `: ` entirely when you can. "Covers the new pipeline" reads the same and cannot
break parsing.

## Length

The specification caps `description` at 1024 characters. Hosts also budget the total
list, and at least one host shortens individual descriptions before dropping skills
entirely. Treat 300 to 500 characters as the practical working range.

To fit:

- Cut adjectives. "Comprehensive", "powerful", and "in-depth" cost characters and
  signal nothing.
- Cut restatements of the skill title. The name is already listed next to it.
- Cut background. Why the skill exists belongs in the body.

## Per-harness notes

- OpenCode omits skills whose `metadata` sets `opencode/autoinvoke` to `"false"` from
  the model-facing list, so the description does not help those skills get selected.
  Choose deliberately rather than by accident.
- Claude Code appends `when_to_use` frontmatter to the description in the skill
  listing, and truncates the combined text. Put the key use case in `description`
  itself rather than in `when_to_use`.
- Codex shortens descriptions before omitting skills when many are installed, so the
  first sentence carries the most weight.

## Before and after

```yaml
# Before
name: ci-fix
description: Helps with continuous integration problems.
```

```yaml
# After
name: ci-fix
description: Diagnoses failing GitHub Actions and GitLab CI pipelines, including flaky tests, dependency install failures, and job timeouts. Use when the user mentions CI, a failing workflow, a red pipeline, a flaky test, or asks why a build passes locally but fails in CI. Not for writing new deployment pipelines.
```