# Skill Authoring Specification

You are writing production-grade Agent Skills for a public, open-source repository
used by three AI coding harnesses: **OpenCode**, **Claude Code**, and **Codex**.

All content must be written in **English** (technical terms, code, and prose).

## Non-negotiable accuracy rule

This repository is a public reference. **Fabricated APIs, invented CLI flags, made-up
config keys, and plausible-sounding-but-wrong commands are worse than a gap in
coverage.** A reader may paste your commands verbatim.

- Only document APIs, flags, config keys, and behaviors you are confident are real.
- When you are not certain whether an exact flag or key exists, describe the
  capability and say to verify with `--help` / the official docs. Never invent a
  specific string to fill the gap.
- Do not invent version numbers or "as of vX.Y" claims.
- Do not invent benchmarks, percentages, or performance numbers. Describe mechanisms
  and tradeoffs qualitatively, or tell the reader to measure.
- Prefer well-established, stable, widely-used APIs over bleeding-edge ones.
- If a skill covers a fast-moving area, add a short "Verify before relying on this"
  note pointing at the authoritative docs URL.

When in doubt, describe *why* and *how to verify* rather than inventing specifics.

## Layout

You write into an existing repo. Create directories as needed.

```
skills/<category>/<skill-id>/SKILL.md
```

- `skill-id` must be lowercase kebab-case, 1-64 chars, matching `^[a-z0-9]+(-[a-z0-9]+)*$`,
  and identical to its directory name.
- Use the **directory form** (`<skill-id>/SKILL.md`), not flat `<skill-id>.md`, so the
  skill gets a private base directory for its supporting files.
- Optional supporting files live beside `SKILL.md`:
  - `references/<topic>.md` — deeper docs loaded on demand
  - `scripts/<name>.<ext>` — runnable helper scripts
  - `assets/<name>` — templates, snippets, boilerplate

A skill with 200+ lines may split detail into `references/`. Keep `SKILL.md` itself
navigable and load the references only when needed.

## SKILL.md frontmatter

Exactly this shape, always:

```markdown
---
name: <Human Readable Title>
description: <One paragraph, third person, describing what it does AND when to use it>
metadata:
  opencode/autoinvoke: true
---

# <Title>

...
```

The `description` is the discovery signal: it is the only part the model sees before
loading. Write it as "Covers X, Y, Z. Use when the user asks about A, B, or C, when
working on D, or when debugging E." Include concrete trigger words the user might
type. 2-4 sentences. Do not use quotes that need escaping; avoid `:` followed by
space inside unquoted YAML scalars unless the value is quoted.

## Body structure

Aim for **200-450 lines** of dense, useful `SKILL.md` per skill (put the long tail in
`references/`). No filler. Every section should change what the reader does.

```markdown
# <Title>

<One or two sentences: what this skill is for and the core mental model.>

## When to use this
<Bullet list of concrete triggers.>

## Quick start
<Copy-pasteable minimal working example.>

## <Core topic 1>
## <Core topic 2>
...

## Checklist
<Verification steps, a review checklist, or a done-when list.>

## Common mistakes
<Real, specific failure modes with the fix.>

## Anti-patterns
<Things that look right but are wrong.>

## References
<Links to official docs.>
```

## Content standards

- **Runnable code.** Examples must be syntactically valid and internally consistent.
  Complete imports, complete file paths, no `...` placeholders inside code that is
  meant to run.
- **Explain the why.** State the tradeoff behind each recommendation.
- **Decision tables** for "which one should I use" questions.
- **Real failure modes**, not generic advice: error messages, symptoms, root cause, fix.
- **Version-aware hedging**: where an API has churned, note the stable core and tell
  the reader to check docs.
- No emoji. No exclamation marks. No "In today's fast-paced world". No marketing tone.
- Address the reader as "you". Use imperative instructions for procedures.
- Second person, present tense, active voice.

## Cross-harness compatibility

Skills must work in OpenCode, Claude Code, and Codex. Therefore:

- Never hardcode a harness-specific tool name as the only way to accomplish something.
  Describe the action ("read the file", "run the test command", "search the repo").
- Do not reference any harness's proprietary configuration schema by key name as if
  it were universal. If a harness detail matters, mark it clearly as harness-specific.
- Keep tooling recommendations portable: prefer commands available in a stock shell,
  and note Windows/POSIX differences where relevant.
- Frontmatter uses only `name`, `description`, `metadata.opencode/autoinvoke` — all
  optional to other harnesses and ignored gracefully by them.

## Definition of done

For each skill you write:

1. Valid YAML frontmatter with `name` and a discovery-rich `description`.
2. Directory name equals the skill ID equals the `name` in kebab-case form.
3. 200+ lines of substantive content, or a `SKILL.md` plus a `references/` file
   carrying the detail.
4. Every code block is syntactically valid for the language it claims.
5. Zero invented flags, keys, or APIs.
6. Ends with a References section of real, official documentation URLs.

Verify your work before finishing. Report per-skill: the ID, its line count, and the
file paths you created.