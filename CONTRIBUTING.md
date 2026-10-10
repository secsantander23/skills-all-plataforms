# Contributing

Thanks for adding to the collection. The bar is accuracy and usefulness, not volume.

## Read the spec first

Read [AUTHORING-SPEC.md](AUTHORING-SPEC.md). It defines the layout, frontmatter, structure,
accuracy rules, and definition of done. Mirror the structure of an existing skill.

## Add a skill

1. Pick the right category under `skills/`. If none fits, add one and update the README table.
2. Create `skills/<category>/<skill-id>/SKILL.md` where `<skill-id>` is lowercase kebab-case
   (`^[a-z0-9]+(-[a-z0-9]+)*$`) and matches its directory name.
3. Frontmatter:

   ```markdown
   ---
   name: <Human Readable Title>
   description: <what it does and when to use it, with trigger words>
   metadata:
     opencode/autoinvoke: true
   ---
   ```

4. Body: `When to use this`, `Quick start`, topic sections, `Common mistakes`,
   `Anti-patterns`, `References`. Aim for 200-450 lines in `SKILL.md` and push deeper detail
   into `references/*.md`.
5. Add the skill to the README category listing.

## Accuracy rules (not negotiable)

These skills get pasted verbatim by developers. A wrong flag is a real bug in this repo.

- Never invent an API, CLI flag, config key, header name, or environment variable.
- Never invent version numbers, pricing, benchmarks, or SLA figures.
- If you are not certain an exact string exists, describe the capability and point the reader
  at the official documentation or the tool's `--help`.
- Code examples must be syntactically valid and internally consistent, with real imports and
  no `...` inside code meant to run.
- Verify flags and config keys against the official docs before writing them down.

## Validate before opening a PR

```bash
node tools/validate-skills.mjs
```

The validator checks frontmatter presence, kebab-case IDs, unclosed code fences, CJK
characters that slipped in, missing References sections, and line counts. Fix every error;
warnings should be addressed or justified.

## Cross-harness compatibility

Skills must work in OpenCode, Claude Code, and Codex.

- Do not require a harness-specific tool name as the only way to do something.
- Do not present one harness's config schema as if it were universal.
- Keep shell examples portable; note Windows/POSIX differences where it matters.

## Commit and PR

Use [Conventional Commits](https://www.conventionalcommits.org/) style, for example:

```
feat(security): add application-crypto skill
docs(readme): list new observability skills
fix(networking): correct TLS chain section in tls-and-pki
```

Open a PR describing which skills you added and which official docs you verified against.