---
name: READMEs and Contribution Docs
description: Covers what belongs in a README, a quickstart-first structure, project structure diagrams, CONTRIBUTING guides, codes of conduct, issue and pull request templates, licensing, badges, and on-boarding a new contributor. Includes a complete copy-pasteable README template. Use when the user asks to write or review a README, set up contribution docs, create issue or PR templates, add badges, or choose a license.
metadata:
  opencode/autoinvoke: true
---

# READMEs and Contribution Docs

A README is a landing page that must answer "what is this, is it for me, how do I run it" in
under a minute. Contribution docs are a contract: they say how a stranger becomes a
contributor and what you will ask of them. Both are read by people who are not yet invested.

## When to use this

- Writing or reviewing a README for a new or public repository.
- Deciding what belongs in the README and what belongs in `docs/`.
- Adding `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue templates, or a PR template.
- Choosing a license and presenting it clearly.
- Adding or removing badges, and deciding whether they help or add noise.
- Diagnosing a repository with a high drop-off rate in its first issue.

## Quick start

The four files every public repository should have, in the locations GitHub looks for them:

```text
repo-root/
  README.md
  LICENSE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  .github/
    CODEOWNERS
    ISSUE_TEMPLATE/
      bug_report.md
      feature_request.md
      config.yml
    pull_request_template.md
    workflows/
      ci.yml
```

GitHub reads community health files from the repository root, from `docs/`, or from
`.github/`. The exact lookup rules for each file type are in the GitHub documentation linked
below; check them for the file you are adding rather than guessing the directory.

## What belongs in a README

| Include | Because |
| --- | --- |
| One-sentence description | The reader decides whether to keep reading in five seconds |
| Badges (two or three) | Build and license state, at a glance |
| Quickstart that actually runs | Converts interest into a working install |
| What it is for / not for | Saves the reader from the wrong project |
| Usage examples | Shows the real shape of the API |
| Configuration reference or a link | The most-read section |
| Project layout | Lets contributors orient themselves |
| Links to docs, changelog, license, contributing | Next actions |
| Support and security contact | Where to go instead of opening a public issue |

| Leave out | Because |
| --- | --- |
| Feature lists of 60 bullets | Nobody reads them; lead with 3 |
| Marketing copy | This is a technical document |
| The entire API reference | It belongs in `docs/`, linked |
| Installation steps for 6 platforms you do not test | Untested instructions rot |
| Your internal architecture diagram | Link it instead |
| Contribution details | `CONTRIBUTING.md` exists for this |

## README structure

Quickstart first. Reasonably and universally the top of the page should be:

1. Name and one-line description.
2. Badges.
3. Install/run in under five commands.
4. A minimal working example that produces visible output.
5. Pointer to documentation.

Then: what it does, usage, configuration, project layout, development, contributing, license,
acknowledgements.

Details that make the quickstart reliable:

- Pin the version in install commands when the tool has releases, or state "latest".
- Show the exact command and the expected output. A reader who sees the expected line stop
  worrying.
- State prerequisites explicitly, with versions if they matter.
- Use copy-pasteable blocks with no placeholders in the quickstart.

```bash
# Clone and run
git clone https://github.com/acme/tool.git
cd tool
npm install
npm test
```

```bash
$ npm test
# tests 42
# tests 42
# pass 42
# fail 0
```

## README template

```markdown
# tool-name

One sentence: what it does and who it is for.

[![CI](https://github.com/acme/tool/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/acme/tool/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/tool-name.svg)](https://www.npmjs.com/package/tool-name)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Quickstart

Requires Node.js 20 or later.

    git clone https://github.com/acme/tool.git
    cd tool
    npm install
    npm test

## Usage

```javascript
import { parse } from 'tool-name';

const result = parse('a=1;b=2');
console.log(result); // { a: 1, b: 2 }
```

## Configuration

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--strict` | boolean | `false` | Fail on unrecognized input instead of ignoring it. |
| `--timeout` | integer | `30000` | Milliseconds before a remote read is abandoned. |

Configuration can also be supplied through `tool.config.json`; command-line flags win.
See [the configuration reference](docs/configuration.md) for every option.

## What this is not

- Not a general-purpose workflow engine.
- Not a drop-in replacement for `legacy-tool`; the parsing differs.

## Project layout

```text
tool/
  src/            library code
  bin/            CLI entrypoints
  test/           test suite
  docs/           long-form documentation
  examples/       runnable examples
```

## Development

    npm install
    npm test
    npm run lint

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
```

Adapt it; do not paste the placeholder values. Delete sections you cannot fill honestly.

## Badges

Badges answer "is this maintained and safe to depend on?" and should be few.

- Build or test status, linked to the actual run.
- Package version from the registry, if published.
- License.
- Coverage, if the number is real and you enforce it.

GitHub Actions workflow badges use a fixed URL shape:

```markdown
[![CI](https://github.com/acme/tool/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/acme/tool/actions/workflows/ci.yml)
```

The `workflows/<file>` segment must match the workflow filename exactly, including its
extension. A mismatch renders a "badge not found" image silently.

For a badge that adapts to the user's color scheme, use `<picture>`:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/license-MIT-333333?style=flat-square">
  <source media="(prefers-color-scheme: light)" srcset="https://img.shields.io/badge/license-MIT-555555?style=flat-square">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-007EC6?style=flat-square">
</picture>
```

Avoid: vanity download counts you do not control, more than three badges, badges that are
always green because they cover nothing.

## CONTRIBUTING.md

The goal is that a first-time contributor can go from clone to merged PR without asking a
question in an issue tracker.

```markdown
# Contributing to tool-name

Thanks for your interest. This document covers how to get a change merged.

## Getting set up

    git clone https://github.com/acme/tool.git
    cd tool
    npm install
    npm test

## Making a change

1. Branch from `main`: `git switch -c fix/short-description`.
2. Make the change and add tests that fail without it.
3. Run `npm run lint && npm test`.
4. Open a pull request against `main` and describe what changed and why.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
`type(scope): subject`, for example `fix(parser): handle CRLF input`.
A `!` or a `BREAKING CHANGE:` footer marks a breaking change.

## Pull requests

- One logical change per PR.
- Update documentation in the same PR.
- Fill in the PR template.
- CI must be green. Required reviews are enforced on `main`.

## Reporting bugs

Open an issue with the template. Include the version, the command, and a minimal
reproduction.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contributions are licensed under the
[project license](LICENSE).
```

Keep it concrete: real commands, real branch names, real review expectations.

## Codes of conduct

A code of conduct sets behavior expectations and names who handles reports.

```markdown
# Code of Conduct

## Our pledge

We pledge to make participation in this project a harassment-free experience for
everyone, regardless of age, body size, visible or invisible disability,
ethnicity, sex characteristics, gender identity and expression, level of
experience, education, socio-economic status, nationality, personal
appearance, race, religion, or sexual identity and orientation.

## Our standards

Examples of behavior that contributes to a positive environment:

- Being respectful and welcoming of differing opinions.
- Giving and gracefully accepting constructive feedback.
- Focusing on what is best for the community.

Examples of unacceptable behavior:

- Sexualized language or imagery, and sexual attention or advances of any kind.
- Trolling, insulting or derogatory comments, and personal or political attacks.
- Public or private harassment.
- Publishing others' private information without their explicit permission.

## Enforcement

Report unacceptable behavior to the maintainers at <conduct@example.com>.
All complaints will be reviewed and investigated promptly and fairly. Maintainers
are obligated to respect the privacy and security of the reporter of any
incident.

## Attribution

Adapted from the [Contributor Covenant](https://www.contributor-covenant.org),
version 2.1.
```

The Contributor Covenant is a real, widely adopted source document; adapt it rather than
inventing a policy from scratch.

## Issue and PR templates

### Bug report template

`.github/ISSUE_TEMPLATE/bug_report.md`:

```markdown
---
name: Bug report
about: Something behaves incorrectly
title: "[Bug] "
labels: bug
assignees: ''
---

## What happened

<!-- What you observed. -->

## What you expected

<!-- What should have happened instead. -->

## Reproduction

<!-- The smallest command or steps that show the problem. -->

## Environment

- tool-name version:
- OS:
- Runtime and version:

## Logs

<!-- Paste relevant output. Remove secrets first. -->
```

The `name`, `about`, `title`, `labels`, and `assignees` frontmatter keys are read by GitHub's
issue template form; keep the file with a `.md` extension in `.github/ISSUE_TEMPLATE/`.

### PR template

`.github/pull_request_template.md`:

```markdown
## What

<!-- What this change does, in one or two sentences. -->

## Why

<!-- The problem it solves, or a link to the issue. Closes #123 -->

## How

<!-- The approach, and anything a reviewer should know before reading the diff. -->

## Checklist

- [ ] Tests added or updated; the suite passes
- [ ] Documentation updated in this PR
- [ ] No unrelated changes included
- [ ] Commit messages follow Conventional Commits
```

### Config routing

`.github/ISSUE_TEMPLATE/config.yml` can present a chooser and turn off blank issues:

```yaml
blank_issues_enabled: false
contact_links:
  - name: Security vulnerability
    url: https://example.com/security/report
    about: Report privately. Please do not open a public issue.
  - name: Usage question
    url: https://github.com/acme/tool/discussions
    about: Ask and answer questions in Discussions.
```

Verify the exact supported keys against GitHub's issue template configuration documentation
before relying on them.

## Licensing

- Pick a license deliberately. MIT and Apache-2.0 are permissive; the GPL family is copyleft.
  Choose with counsel if the project is commercial.
- Include the full license text in `LICENSE`.
- Add the SPDX identifier in the README badge and, where supported, in package metadata
  (`"license": "MIT"` or `"license": "Apache-2.0"`).
- State the copyright holder and year in `LICENSE`.
- For dual licensing, document the components covered by each license.
- `choosealicense.com` has plain-language comparisons and is a reasonable starting point, not
  legal advice.

## Checklist

README:

- [ ] One-sentence description that names the audience.
- [ ] Quickstart runs from a clean clone with no edits.
- [ ] Expected output shown.
- [ ] Prerequisites stated with versions.
- [ ] Project layout present.
- [ ] Links to docs, contributing, license, security all resolve.
- [ ] Two or three badges maximum, all meaningful and green.

Contribution surface:

- [ ] `CONTRIBUTING.md` has real commands and a real branch target.
- [ ] Issue templates exist for the two most common report types.
- [ ] PR template asks for the what, the why, and a checklist.
- [ ] `CODE_OF_CONDUCT.md` names an actual contact.
- [ ] `SECURITY.md` gives a private reporting channel.
- [ ] `LICENSE` is the chosen license, complete, with the right holder.
- [ ] `CODEOWNERS` maps directories to teams.

## Common mistakes

**A quickstart that requires reading another document first.** Symptom: three issues titled
"how do I install". Fix: make step one a complete command.

**Screenshots for installation.** They go stale, they are not searchable, and they are
inaccessible. Use text.

**Badges that always pass.** A coverage badge nobody enforces and a download badge for a
package that has no downloads train readers to ignore badges.

**Templates with instructions nobody reads.** Symptom: issues with all fields empty. Fix: keep
templates short and mark each section with an HTML comment that says what goes there.

**`CONTRIBUTING.md` describing a process the repository does not follow.** Symptom: contributors
open PRs against the wrong branch. Fix: generate the content from the actual CI configuration
and branch protection rules.

**No `LICENSE` file.** Symptom: legal review blocks adoption, or a corporate user cannot use
it. This is a real blocker for internal adoption, not a formality.

**Badges pointing at a workflow filename that does not exist.** Symptom: a broken image that
renders as alt text. Verify the badge URL resolves.

**Screenshots of terminal output without the command.** The reader cannot reproduce. Show
both.

## Anti-patterns

- **A README that is a changelog.** It stops being a landing page the day it is written.
- **Documentation duplicated between README and `docs/`.** Pick one; link from the other.
- **Contributing instructions that assume institutional knowledge.** "Run the normal dev
  setup" is not an instruction.
- **Templates that enforce length instead of content.** A 12-field bug form produces empty
  fields, not better reports.
- **A code of conduct with no enforcement contact.** It is a decoration.
- **Adding a sponsor or maintainer badge for someone who did not agree.** Ask.
- **Fork-and-reset README rewrites without keeping the quickstart working.** The most-read
  section is the most damaging one to break.

## References

- GitHub, "About READMEs": https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- GitHub, repository files and directories (where each community file is looked up):
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-how-your-repository-works-with-github-community-health-files
- GitHub, "Setting up your project for healthy contributions":
  https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions
- GitHub issue templates and form syntax:
  https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository
- GitHub pull request templates:
  https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository
- GitHub, adding a license: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/adding-a-license-to-a-repository
- GitHub, `SECURITY.md` and private reporting:
  https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository
- Contributor Covenant: https://www.contributor-covenant.org/
- Keep a Changelog format: https://keepachangelog.com/en/1.1.0/
- Choose a License (comparison tool): https://choosealicense.com/
- Conventional Commits: https://www.conventionalcommits.org/en/v1.0.0/
- OpenSSF Best Practices badge criteria: https://www.bestpractices.coreinfrastructure.org/en/criteria/0