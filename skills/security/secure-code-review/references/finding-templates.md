# Writing Actionable Security Findings

The test for a finding is simple: could a competent developer fix it, and convince their manager it matters, without asking you a follow-up question? If not, it is not a finding yet.

## Anatomy of a finding

```
[SEVERITY] Short defect statement with location and CWE

Location:     path/to/file.py, function_name(), lines 41-47
Severity:     High - <impact> - <attacker prerequisites>
Confidence:   Confirmed (reproduced locally) | Likely | Needs verification
Description:  What is wrong, in terms of the data flow.

Attack scenario:
  1. <concrete step the attacker takes>
  2. <what they observe or obtain>
  3. <why the current control does not stop them>

Evidence:
  <redacted code excerpt, request, or response>

Remediation:  <specific change, with code, or an authoritative reference>

References:   CWE-NNN - <name> - <url>
              OWASP <Cheat Sheet name> - <url>
```

## Severity phrasing

State the reasoning, not just the label.

```
Severity: High
  Impact:        Cross-tenant read of all invoices (confidentiality)
  Prerequisites: Any authenticated user account, free to register
  Exposure:      Internet-facing, ~40k users across ~600 tenants
  Assumption:    Invoice ids are sequential integers (confirmed in the data model)
```

```
Severity: Low
  Impact:        Internal hostname disclosed in an error body
  Prerequisites: Triggering a validation error
  Exposure:      Reduces the effort for A10 (SSRF), which is separately mitigated
```

Compare to the program's rubric if there is one, and say which rubric you used.

## Non-blocking suggestions

Keep these separate from findings. They are not defects, and mixing them buries the real ones.

```
Suggestion (non-blocking): the tenant filter could move into a repository
base class so new endpoints inherit it. This is a maintainability improvement,
not a vulnerability.
```

## Redaction

Never include a live secret in a finding, even in a private channel.

Replace with a fingerprint:

```
Location:    config/settings.py, line 12
Finding:     Hard-coded production database password committed to git history
             (commit 4a91c0e, file removed in 8f3d21a but still retrievable)
Fingerprint: "pgprod-****-****-9f2c"  (value not reproduced here)
Remediation: Rotate the credential now, then purge from history (git filter-repo).
             Note: rotation is required; history rewriting alone is not a fix.
```

Redact request and response bodies in attack scenarios too. Use the first and last three characters of identifiers, and never include a live token.

## Severity calibration examples

| Scenario | Rating | Why |
| --- | --- | --- |
| SQL injection in a public search endpoint | Critical | Unauthenticated, arbitrary data read |
| SQL injection in an admin-only report | High | Authenticated and privileged, but data impact is large |
| IDOR returning another user's display name | Medium | Real breach, but limited data |
| IDOR returning another user's invoices | High | Directly monetizable data |
| Self-XSS requiring the victim to paste a payload | Low | No plausible delivery path |
| Stored XSS on a page all users view, no CSRF needed | High | Executes in other users' sessions |
| SSRF to the metadata endpoint in a locked-down network | Medium | Impact depends on network; state your assumption |
| Verbose stack trace in a 500 response | Low | Assists other attacks; no direct impact |
| Missing `HttpOnly` on a cookie that holds a session id | High | Enables session theft via XSS |
| Rate limit absent on login | Medium | Enables credential stuffing; pair with other A07 findings |

## Phrasing that works

| Instead of | Write |
| --- | --- |
| "This is insecure." | "`get_invoice` looks the invoice up by client-supplied id with no tenant filter, so any authenticated user can read other tenants' invoices. Fix: scope the query to `user.tenant_id`." |
| "Add input validation." | "`quantity` is read from the request body and passed straight to the billing client, which accepts `-5`. Add a range check `1 <= quantity <= 100` in the request schema." |
| "This looks like a race condition." | "Two concurrent `withdraw` calls can both pass the `balance >= amount` check before either debit lands. Fix: perform the check inside a transaction with `SELECT ... FOR UPDATE`." |
| "Use a better hash." | "Passwords are stored with SHA-256, which is fast and therefore cheap to brute force. Fix: bcrypt, scrypt, or Argon2id with a per-user salt; see the OWASP Password Storage Cheat Sheet for parameters." |
| "There are several issues." | One finding per comment, each independently resolvable. |

## Review comment etiquette

- Comment on lines, not the whole file, so the author sees exactly what you mean.
- Separate blocking findings from suggestions, and say which are blocking.
- Do not demand a specific implementation when several are correct. State the property you need.
- If you are unsure, say so and say what would settle it. "I could not determine whether the middleware also applies to this route; is it registered before the route table?" is a better review comment than a confident wrong finding.
- Acknowledge what the change does well. Reviewers who only ever report defects get read carefully once and not again.
- Report severity as your assessment, and defer to the security owner for final categorization when your program requires it.

## Tools that consume findings

If findings are going anywhere automated, use a format they understand rather than a screenshot.

- SARIF is the interchange format for static analysis results. Tools including CodeQL, Semgrep (with its output options) and many scanners can emit it.
- GitHub code scanning ingests SARIF and maps CWE identifiers to its security surface.
- Keep titles stable so that a repeated finding is recognized as the same issue rather than a new one.

## Reviewing a scanner result

1. Read the rule. Understand the pattern and its assumptions.
2. Trace the data. Is the sink reachable from an untrusted input in this codebase?
3. Check for mitigations the rule cannot see: a validator upstream, an allowlist, a framework protection.
4. Decide: confirmed, likely, or false positive. Record why.
5. Fix the code or add a narrowly scoped suppression with a comment explaining the mitigation. Never blanket-suppress a rule path.

Tuning scanners to reduce noise is worthwhile. Silencing everything to make the pipeline green is not.

## References

- OWASP Code Review Guide - https://owasp.org/www-project-code-review-guide/
- OWASP Cheat Sheet Series - https://cheatsheetseries.owasp.org/
- CWE definitions and search - https://cwe.mitre.org/
- SARIF format specification (OASIS) - https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
- GitHub code scanning and SARIF upload - https://docs.github.com/en/code-security/code-scanning
- GitLab secret detection and push rules - https://docs.gitlab.com/ee/user/application_security/secret_detection.html
- OWASP Web Security Testing Guide - https://owasp.org/www-project-web-security-testing-guide/