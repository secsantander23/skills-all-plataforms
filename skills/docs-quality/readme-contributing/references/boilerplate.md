# Boilerplate files

Copy-paste starting points for `readme-contributing`. Replace placeholders, delete sections you
cannot fill, and check the current GitHub documentation for any file you rely on, because the
supported frontmatter keys and lookup paths change.

## config.yml (issue template chooser)

`.github/ISSUE_TEMPLATE/config.yml`:

```yaml
blank_issues_enabled: false
contact_links:
  - name: Report a security vulnerability
    url: https://example.com/security/report
    about: Report privately. Public reports cannot be guaranteed to be handled safely.
  - name: Ask a usage question
    url: https://github.com/acme/tool/discussions
    about: Questions and answers about using tool-name.
```

## Feature request template

`.github/ISSUE_TEMPLATE/feature_request.md`:

```markdown
---
name: Feature request
about: Suggest a capability or improvement
title: "[Feature] "
labels: enhancement
assignees: ''
---

## Problem

<!-- What are you trying to do that is currently hard or impossible? -->

## Proposed solution

<!-- What you would like to happen. -->

## Alternatives

<!-- Workarounds you have used. -->

## Scope

- [ ] Breaking change
- [ ] New configuration option
- [ ] New dependency
```

## SECURITY.md

```markdown
# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 3.x | Yes |
| 2.x | Security fixes only |
| < 2.0 | No |

## Reporting a vulnerability

Report privately through GitHub's security advisory form
(Security tab -> Report a vulnerability), or by email to
security@example.com.

Please include:

- Type of issue and the component affected
- Steps to reproduce
- Impact and any known mitigations

Do not open a public issue for an unpatched vulnerability.

## What to expect

- Acknowledgement within 3 business days
- Initial assessment within 10 business days
- A fix or a mitigation plan, with a disclosure timeline agreed before release

## Disclosure

We follow a coordinated disclosure policy. We ask that you give us a reasonable
period to release a fix before public disclosure.
```

The response-time numbers are commitments you must actually meet; adjust them to what your team
can support rather than copying a standard.

## Multiple PR templates

GitHub supports a directory of pull request templates, so contributors can choose one. Verify
the exact directory name and file extensions against the GitHub documentation for pull request
templates; the single-file form is `.github/pull_request_template.md`.

## FUNDING.yml

```yaml
# .github/FUNDING.yml
github: [octocat]
custom: ["https://example.com/donate"]
```

Only add this for people or projects that have agreed to be listed.

## Changelog skeleton

Following Keep a Changelog, with SemVer ordering:

```markdown
# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Nothing yet.

## [3.2.0] - 2026-04-18

### Added

- `--strict` flag rejects unrecognized input instead of ignoring it.

### Fixed

- CRLF input no longer produces `unexpected token at line 1`.

### Deprecated

- `--legacy-parse` is deprecated and will be removed in 4.0. Use `--strict`.

[Unreleased]: https://github.com/acme/tool/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/acme/tool/compare/v3.1.0...v3.2.0
```

The `Keep a Changelog` link definitions at the bottom are what make the compare links work;
GitHub renders them as real links.

## Minimal README for an internal package

```markdown
# @acme/utils

Shared helpers for Acme services. Not published publicly.

## Usage

```javascript
import { formatCurrency } from '@acme/utils';

formatCurrency(1234.5, 'USD'); // "$1,234.50"
```

## Development

    npm install
    npm test
```

Keep internal packages short. The long-form documentation belongs in the service that uses
it.