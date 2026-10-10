# Harness discovery, precedence, and extension fields

Everything here is harness-specific. The portable core is the Agent Skills directory
layout plus `name`, `description`, `license`, `compatibility`, and `metadata`. Verify
against the current docs before relying on a path; these products move.

Verify before relying on this:

- OpenCode: https://opencode.ai/docs/skills/
- Claude Code: https://docs.claude.com/en/docs/claude-code/skills
- Codex: https://developers.openai.com/codex/build-skills
- Shared spec: https://agentskills.io/specification

## Discovery matrix

| Harness | Project paths | Global paths | Walks up to |
| --- | --- | --- | --- |
| OpenCode | `.opencode/skills/*/SKILL.md`, `.claude/skills/*/SKILL.md`, `.agents/skills/*/SKILL.md` | `~/.config/opencode/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | git worktree root |
| Claude Code | `.claude/skills/*/SKILL.md` at the start directory and every parent | `~/.claude/skills/*/SKILL.md` | repository root |
| Codex | `.agents/skills/*/SKILL.md` | `$HOME/.agents/skills`, `/etc/codex/skills`, bundled system skills | repository root |

The single directory that all three read is `.agents/skills/`. If you need one folder
that works everywhere with no duplication, use that.

```text
repo/
  .agents/
    skills/
      db-migration-review/
        SKILL.md
        references/
          lock-matrix.md
```

Commit it. Uncommitted skills exist only on your machine.

## Name resolution and precedence

OpenCode reads `name` and requires it to match the directory name. Claude Code uses the
frontmatter `name` when present and otherwise falls back to the directory name for the
slash command name. Codex matches by directory name.

Consequences:

- Keep `name` identical to the directory name. Do not rely on a fallback.
- If you deliberately want two frontends to read the same skill, give them the same
  folder and let each harness discover it in its own location, or symlink the directory.

## Duplicate names across locations

| Situation | Behaviour differs by harness | Advice |
| --- | --- | --- |
| Same name in project and global | Later session wins in some harnesses, both load in others | Avoid duplicates; scope names by area, for example `frontend-deploy` |
| Skill name equals a built-in command | The local skill usually shadows the built-in in that harness only | Pick a distinct name unless shadowing is intended |
| Nested `.claude/skills/` below the start directory | Claude Code loads it only after touching files in that subtree | Name the directory after the area and mention the subtree in the description |

Codex explicitly does not merge same-named skills found in several scanned directories.
Both appear in the selector. Distinct names avoid the ambiguity entirely.

## OpenCode extensions

Recognised keys inside `metadata`:

| Key | Effect |
| --- | --- |
| `opencode/slash` | Set to `"true"` to expose the skill as a slash command in the TUI |
| `opencode/autoinvoke` | Set to `"false"` to keep the skill out of model-facing auto-selection while leaving it selectable by name |

The specification defines `metadata` values as strings, so quoted values are the
portable form. OpenCode also accepts YAML booleans.

Skills appear to the model as an `available_skills` block containing name and
description. A skill with `opencode/autoinvoke: "false"` is omitted from that block but
can still be loaded by exact name. Use it for skills that are useful on demand but would
otherwise fire on unrelated work, for example a deployment runbook.

Access control is separate from auto-invocation. OpenCode also supports skill
permission patterns:

```json
{
  "permission": {
    "skill": {
      "*": "allow",
      "internal-*": "deny",
      "experimental-*": "ask"
    }
  }
}
```

A skill denied by permission is hidden from the agent entirely. When auditing why a
skill does not load, check permissions before suspecting the frontmatter.

## Claude Code extensions

Frontmatter fields Claude Code understands beyond the shared specification:

| Field | Purpose |
| --- | --- |
| `allowed-tools` | Space- or comma-separated tools pre-approved for the turn that invoked the skill |
| `disallowed-tools` | Tools removed from the pool while the skill is active |
| `disable-model-invocation` | Set `true` so only a human can invoke it by slash command |
| `user-invocable` | Set `false` to hide it from the slash menu |
| `context: fork` | Run the skill in a forked subagent context |
| `agent` | Which subagent type to use with `context: fork` |
| `model`, `effort` | Model or effort override while the skill is active |
| `paths` | Glob patterns that limit automatic activation |
| `when_to_use` | Extra trigger text appended to the description in the listing |
| `hooks` | Hooks registered when the skill is invoked |

Because these are extensions, some upload and packaging paths that validate strictly
against the Agent Skills specification reject unknown top-level keys. A skill intended
for wide distribution should carry only the specification fields and put extension use
in documentation. Keep any extra keys under `metadata` if you need them recorded.

Claude Code also supports two body features:

- Dynamic context injection with an exclamation mark followed by backticks, for example
  `` !`git diff HEAD` ``. Claude Code runs the command and substitutes the output before
  the model sees the body. This is not portable; other harnesses see the literal line.
- File references with an `@` prefix. Also not portable.

Keep these out of a portable skill, or call them out explicitly as Claude Code only.

## Codex extensions

Codex adds an optional `agents/openai.yaml` next to `SKILL.md` for UI metadata,
invocation policy, and tool dependencies. It is ignored by the other harnesses.

Codex also caps the size of the initial skill list it shows the model and shortens
descriptions first, then omits skills. This is the concrete reason to front-load the
distinguishing words in `description`.

## Portability checklist

- Frontmatter limited to `name`, `description`, `license`, `compatibility`, `metadata`.
- No `!` command substitution or `@` file references in the body.
- Any harness-specific key documented in the body as harness-specific.
- Skill located in `.agents/skills/` if a single shared location matters more than
  OpenCode-native configuration.
- Relative references only. No absolute paths to one machine.
- Scripts use a shebang and document their exit codes.