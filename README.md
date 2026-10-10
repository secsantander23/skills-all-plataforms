# skills-all-platforms

A large, curated collection of **Agent Skills** for **OpenCode**, **Claude Code**, and **Codex**.

Every skill is a `SKILL.md` file (with optional `references/` and `scripts/`) that teaches an
AI coding agent how to handle a specific domain with real, correct guidance — not vague advice.
Skills follow the shared Agent Skills layout, so a skill written once works across all three harnesses.

## What is a Skill

A skill is a markdown file an agent loads on demand. It carries instructions, examples, and
checklists for a specific task so the agent doesn't have to rediscover patterns each session.

- **OpenCode** discovers skills at `~/.config/opencode/skills/` and `.opencode/skills/`.
- **Claude Code** discovers skills at `~/.claude/skills/` and `.claude/skills/`.
- **Codex** and other harnesses that follow the standard read the same layout.

A skill is just markdown with optional frontmatter, so you can copy any skill into whichever
directory your agent uses. See [agent-platforms/authoring-agent-skills](skills/agent-platforms/authoring-agent-skills/SKILL.md)
for the full format and how to write your own.

## Accuracy policy

This repository is a reference, not a dumping ground. To keep it trustworthy:

- No invented APIs, flags, or config keys. If a tool's exact flag matters, the skill points at
  the official docs instead of guessing.
- Fast-moving areas (LLMs, cloud catalogs, tooling) state stable concepts and link to official
  documentation rather than asserting current version numbers or prices.
- Where something is genuinely uncertain, the skill says how to verify it.

## Install a single skill

Copy the skill folder into your agent's skills directory.

```powershell
# OpenCode (global)
Copy-Item -Recurse skills/backend/api-design-rest "$env:USERPROFILE\.config\opencode\skills\"

# Claude Code (global)
Copy-Item -Recurse skills/backend/api-design-rest "$env:USERPROFILE\.claude\skills\"
```

On macOS/Linux, replace `%USERPROFILE%` with `~`.

## Use the whole collection (OpenCode)

Point OpenCode at this directory as an extra skills source in `opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": ["path/to/skills-all-platforms/skills"]
}
```

Add the path to `~/.config/opencode/opencode.jsonc` for global availability. The `skills` array
accepts a directory, `~/` paths, absolute paths, or an HTTP catalog.

## Categories

| Category | Focus |
| --- | --- |
| [agent-platforms](skills/agent-platforms) | Writing skills, MCP servers, agent evals, cost, prompt-injection defense, LLM engineering |
| [api-protocols](skills/api-protocols) | REST, GraphQL, gRPC, auth, queues, webhooks, contracts |
| [architecture](skills/architecture) | System design, DDD, clean architecture, event-driven |
| [backend](skills/backend) | API design, auth, background jobs, webhooks, validation |
| [cloud](skills/cloud) | AWS, GCP, Azure deployment, cost and reliability |
| [data-ai-ml](skills/data-ai-ml) | Prompting, RAG, agents, data pipelines |
| [database](skills/database) | Postgres, schema design, migrations, transactions, Redis, MongoDB, query tuning |
| [devops](skills/devops) | Docker, Kubernetes, CI/CD, IaC, Linux ops |
| [docs-quality](skills/docs-quality) | Technical writing, API docs, READMEs, docs-as-code |
| [frontend](skills/frontend) | React, Vue, Svelte, CSS architecture, design systems, a11y, forms |
| [git-workflow](skills/git-workflow) | Conventional commits, code review, branching, monorepos |
| [languages](skills/languages) | TypeScript, JavaScript, Python, Go, Rust, Java, C#, SQL |
| [mobile](skills/mobile) | React Native, Flutter, iOS, Android, offline sync, app lifecycle |
| [networking](skills/networking) | DNS, TLS, reverse proxies, WebSockets |
| [observability](skills/observability) | Logging, metrics, tracing, incident response |
| [performance](skills/performance) | Profiling, caching, concurrency, scalability |
| [security](skills/security) | OWASP Top 10, secure review, secrets, crypto |
| [testing](skills/testing) | Unit, integration, E2E, TDD, load testing |
| [tooling](skills/tooling) | Package managers, bundlers, linting, Git |
| [web-platform](skills/web-platform) | HTTP, security headers, browser runtime, Web Vitals |

## Validate

After editing skills, run the validator to check frontmatter, IDs, fences, and line counts:

```bash
node tools/validate-skills.mjs
```

## Contributing

Add a skill as `skills/<category>/<skill-id>/SKILL.md` where `<skill-id>` is lowercase kebab-case.
Include a discovery-rich `description`, real runnable examples, a "Common mistakes" and
"Anti-patterns" section, and a "References" section of official documentation links.
Read [the authoring spec](AUTHORING-SPEC.md) and mirror an existing skill's structure.

## License

Released under the MIT License. See [LICENSE](LICENSE).

Skills may reference external documentation and tools that carry their own licenses.