---
name: Secure Code Review
description: Covers reviewing code for authorization gaps, injection, hardcoded secrets, unsafe deserialization, path traversal, SSRF, prototype pollution, race conditions, integer and logic errors, and dependency risk, plus how to phrase findings with severity, evidence, remediation and CWE or OWASP references. Use when the user asks for a security review, when preparing or responding to code review feedback, when triaging a security bug report, or when auditing a pull request for vulnerabilities.
metadata:
  opencode/autoinvoke: true
---

# Secure Code Review

Secure review is a reasoning process with a checklist attached, not a grep for bad patterns. The core mental model is: *understand the trust boundaries in the change, then ask what an attacker controls and what the code does with it.* A review that starts with the diff but not the boundary produces technically correct findings that miss the important ones.

## When to use this

- Reviewing a pull request that touches authentication, authorization, input handling, serialization, file access, or external calls.
- Auditing a service before a release or before an external assessment.
- Triaging a security bug report to judge severity and exploitability.
- Building a review standard for a team that does not have one.
- Deciding whether a scanner finding is a real vulnerability.

## Method

1. **Establish the trust boundaries.** Who is the attacker, what can they control, and what crosses a boundary in this change: HTTP, a message queue, a file, an environment variable, another service.
2. **Trace untrusted data.** For each input, follow it until it reaches a sink: a query, a shell, a template, a filesystem path, an outbound request, a deserializer, a log, a redirect.
3. **Check the control at each sink.** Is there an authorization decision? Is the value parameterized? Is the path confined? Is the host allowlisted? Is the comparison constant-time?
4. **Check what the code assumes.** Preconditions expressed only in a comment, a UI, or a client. Those are the findings.
5. **Check the negative cases.** Empty, huge, negative, duplicated, unicode-normalized, out-of-order.
6. **Check the failure behavior.** What happens when the dependency times out, the token is invalid, or the file is a symlink?

Work through `references/review-checklist.md` for a systematic pass.

## What to look for

### Authorization gaps

The highest-value finding, and the one scanners miss entirely.

- A handler that reads the resource by id without scoping to the principal. This is IDOR.
- Authorization enforced in the UI, in the client, or in a middleware that a new route can bypass.
- A list endpoint that returns objects across tenants.
- A batch operation where one authorized item lets you act on the rest.
- Field-level access: read-only users reaching a write endpoint; support roles reaching billing.
- Metadata leaks that confirm existence (403 versus 404, differing response bodies, differing timings).

```python
# Finding: any authenticated user can read any invoice.
@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, user=Depends(current_user)):
    invoice = invoices.get(invoice_id)          # no tenant check
    return invoice

# Fix: scope the lookup, and return 404 for both "missing" and "not yours".
@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, user=Depends(current_user)):
    invoice = invoices.find_owned(invoice_id, user_id=user.id)
    if invoice is None:
        raise HTTPException(status_code=404)
    return invoice
```

Also check: object identifiers that are guessable, and endpoints that expose a distinct response for "not found" and "forbidden".

### Injection

- String concatenation or interpolation into SQL, NoSQL, LDAP, XPath, shell, or a template.
- ORM escape hatches: `text()`, `raw()`, `$queryRawUnsafe`, native queries.
- Shell-outs. `system`, `exec` with a string, `child_process.exec`, backticks.
- Expression evaluation: `eval`, `Function`, `exec` in dynamic languages, Jinja `Template(user_input)`.
- Header injection through CR or LF in a value you set on a response.
- Second-order injection: data validated on write but not on read, or a stored value concatenated into a later query.

### Secrets in code

- API keys, tokens, passwords, private keys, connection strings, signing keys.
- In source, in tests, in fixtures, in comments, in a default config value, in a container image, in a build log.
- Encoded or "obfuscated" secrets are still secrets: base64 is encoding, not encryption.
- A secret that should be a reference to a secret store but is a literal.

Report the fingerprint and location, not the secret value. Never paste a live credential into a review thread or a ticket.

### Unsafe deserialization

- `pickle.loads`, `marshal`, Java `ObjectInputStream`, `XMLDecoder`, .NET `BinaryFormatter`, PHP `unserialize`, Node `node-serialize`.
- YAML loaders that can construct objects.
- `eval` on any value that came from outside the process boundary.
- Any deserialization where the schema is not validated after parsing.

### Path traversal

- User-controlled path segments joined to a base directory, without a containment check after resolution.
- Archive extraction without checking entries (`Zip Slip`): a tar or zip entry named `../../etc/cron.d/x`.
- Serving user files from a location reachable by the web server.
- Symlink following where the base directory is user-writable.

### SSRF

- Any outbound request whose URL, host, or path segment is user-controlled.
- Allowlists that check only the string hostname, not the resolved address.
- Redirects followed without re-validation.
- Cloud metadata endpoints and internal service names reachable from the server.
- Webhooks and "import from URL" features, which are SSRF by design and must be hardened.

### Prototype pollution

JavaScript-specific, and it sneaks in through merge utilities.

```javascript
// Unsafe: attacker-supplied JSON sets a property that later changes behavior.
const merged = Object.assign({}, defaults, JSON.parse(req.body.options));
if (merged.isAdmin === true) { ... }

// Safe: reject dangerous keys at every depth, and use a null-prototype target.
function isForbiddenKey(key) {
  return key === '__proto__' || key === 'constructor' || key === 'prototype';
}

function safeMerge(target, source) {
  for (const [key, value] of Object.entries(source)) {
    if (isForbiddenKey(key)) continue;
    target[key] =
      value && typeof value === 'object' && !Array.isArray(value)
        ? safeMerge(Object.create(null), value)
        : value;
  }
  return target;
}
```

Also check: recursive merge or clone of untrusted input without a depth limit, and query-string parsers that produce nested objects.

### Race conditions

Time-of-check to time-of-use is the classic. The check passes, the world changes, the action uses stale assumptions.

```python
# Finding: check then act. Two concurrent withdrawals can both pass the check.
balance = accounts.get_balance(account_id)
if balance >= amount:
    accounts.debit(account_id, amount)      # another request may have debited already

# Fix: do the check and the update inside one transaction, with the row locked.
with db.transaction():
    row = db.execute(
        "SELECT balance_cents FROM accounts WHERE id = %s FOR UPDATE",
        (account_id,),
    ).fetchone()
    if row["balance_cents"] < amount:
        raise InsufficientFunds()
    db.execute(
        "UPDATE accounts SET balance_cents = balance_cents - %s WHERE id = %s",
        (amount, account_id),
    )
```

Other races:

- Two requests creating the same record, where the only guard is "check then insert". Use a unique constraint and handle the violation.
- Non-atomic read-modify-write on a counter. Use a single `UPDATE ... SET x = x + 1`.
- Check-then-create on a filesystem path. Use exclusive creation (`O_CREAT | O_EXCL`, `os.O_EXCL`) or a database lock.
- Cached authorization decisions that outlive the change that revoked them. Include a version and invalidate.
- Unbalanced operations: increment without decrement, send money without recording it. Make the second part transactional.

### Integer and logic errors

- Fixed-width integers wrapping: `int` in C, `int32` in Java and Go, unsigned wraparound. Validate ranges at the boundary.
- Truncation when narrowing types, and float used for money. Use an integer minor unit or a decimal type.
- Negative or zero quantities accepted by a cart or refund endpoint.
- Rounding applied more than once through a calculation chain.
- Pagination with `OFFSET` on a large table, or an unbounded page size.
- Unit confusion: milliseconds versus seconds, bytes versus kilobytes, percent versus fraction.
- Operator precedence mistakes in security-relevant expressions, such as `not a in b or c`.
- Comparison of signed and unsigned values, or of a string where an integer was intended.

### Dependency risk

- New dependencies: what do they pull in transitively, how large is the surface, is it maintained, what is the license.
- Version ranges that can resolve to a compromised or broken version.
- Lock files not committed, or committed but bypassed by a `--no-lockfile` style flag in CI.
- Scripts with install-time hooks (npm `postinstall`, Python setup-time execution) in dependencies.
- Base images pinned to a floating tag.
- Abandoned packages no longer receiving security fixes.

```bash
# Confirm the exact subcommands and flags with your tool's docs before relying on them.
npm audit --production
pip-audit
osv-scanner scan source -r .        # uses the OSV database
trivy fs --scanners vuln .
```

## Severity

Severity is impact multiplied by exploitability, plus exposure. Use your program's rubric if you have one; if not, say plainly what you assumed.

| Rating | Typical shape |
| --- | --- |
| Critical | Unauthenticated remote code execution, or full data access across tenants |
| High | Authenticated privilege escalation, injection, or cross-tenant data disclosure |
| Medium | Stored XSS with a real victim path, information disclosure that aids another attack, a race condition requiring precise timing |
| Low | Defense in depth gaps with no direct exploitation path, missing headers, verbose errors |

Tie rating to context. A medium finding that lets any user read another user's email is a high finding in a product whose whole value is email. An SSRF against a network segment with no internal services is lower than the same SSRF in a cloud VPC full of metadata endpoints.

Say which of these you are reasoning about: confidentiality, integrity, availability, or the safety of the pipeline.

## Tooling

Use tools for coverage and yourself for judgment.

| Purpose | Tools | Notes |
| --- | --- | --- |
| SAST | Semgrep, CodeQL, CodeQL for JavaScript and Python, Bandit, gosec, Brakeman, SpotBugs with Find Security Bugs | Rules change; check each project's rule documentation |
| SCA | OSV-Scanner, Dependabot, `npm audit`, `pip-audit`, Trivy, Grype, Snyk | Reachability analysis reduces noise; treat results as leads |
| Secret scanning | gitleaks, TruffleHog, detect-secrets | Run in pre-commit and in CI, on the full history at least once |
| IaC and container | Checkov, Trivy, KICS, tfsec, your platform's image scanning | Findings here are often real misconfigurations |
| Dynamic | OWASP ZAP baseline scan, your own authorization integration tests | Great at finding what static analysis cannot infer |

Expect false positives. Verify a finding by reading the data flow, and mark it as confirmed, likely, or needs-verification. Reporting an unverified scanner finding as a confirmed vulnerability destroys trust in the whole channel.

## Writing a finding

A finding is only actionable if the author can fix it without reverse-engineering your reasoning.

Include:

1. **Title**: the vulnerability class and the location. "IDOR in `GET /invoices/{id}`: no tenant scope on lookup".
2. **Severity**, with the reasoning that produced it.
3. **Location**: file, function, and line range.
4. **Description**: what is wrong, in the language of the data flow.
5. **Attack scenario**: the concrete steps an attacker takes and what they get. This is what convinces a developer to care.
6. **Evidence**: the code excerpt or request and response, redacted.
7. **Remediation**: a code change or a specific reference, not "sanitize input".
8. **References**: CWE and OWASP links.
9. **Confidence**: confirmed, likely, or needs verification.

Good, in shape:

```
Title:     IDOR in GET /invoices/{invoice_id} (CWE-639)
Severity:  High - any authenticated user can read invoices for other tenants
Location:  src/api/invoices.py, get_invoice(), lines 41-47

Description:
  get_invoice() loads the invoice by client-supplied id with no check that it
  belongs to the requesting principal's tenant. invoices.get() filters only on id.

Attack scenario:
  As any authenticated user, iterate invoice ids: GET /invoices/1, /invoices/2, ...
  Each returns the full billing record (customer name, address, line items)
  for another tenant. Response is 200, so the ids are enumerable.

Remediation:
  Scope the lookup to the tenant derived from the verified session:
      invoice = invoices.find_owned(invoice_id, tenant_id=user.tenant_id)
  and return 404 (not 403) when it does not match.

References:
  CWE-639 Authorization Bypass Through User-Controlled Key
  OWASP Authorization Cheat Sheet
  OWASP API Security Top 10, Broken Object Level Authorization
```

Bad, and why it fails:

| Finding | Problem |
| --- | --- |
| "This looks like SQL injection." | Unverified. Says nothing about whether it is reachable |
| "Insecure code in the auth module." | No location, no defect, not actionable |
| "Please sanitize the input." | Not a remediation. Points at the wrong layer |
| "Use bcrypt." | Fine as a fix, but it needs the location, the current code, and the parameters to use |
| Twenty low-value style nits | Buries the one finding that mattered |

Balance: report what is genuinely wrong, note what is merely improvable as a non-blocking suggestion, and separate the two. A review where everything is critical teaches the team to ignore you.

## Checklist

A review is complete when:

- [ ] Trust boundaries and untrusted inputs in the change are identified and traced to sinks.
- [ ] Every new or changed endpoint authorizes server-side, scoped to the principal, with a non-enumerating response.
- [ ] Every query, shell command, template, and expression uses parameters or is not evaluated.
- [ ] No secret, key, or credential appears in code, tests, fixtures, images, or logs.
- [ ] Untrusted data is deserialized only with data-only formats, and schema-validated afterwards.
- [ ] File paths are confined to a resolved root after symlink and traversal handling.
- [ ] Outbound URLs are allowlisted by scheme and host, and validated at the resolved address.
- [ ] JavaScript merges reject `__proto__`, `constructor`, and `prototype`.
- [ ] Check-then-act sequences are atomic or locked; uniqueness has a database constraint.
- [ ] Integers, money, units, and boundaries are validated at the edge and never truncated silently.
- [ ] New dependencies were reviewed for surface, maintenance, and license; lock files are committed.
- [ ] Security-relevant failures are logged without secrets, and the failure path is not a silent success.
- [ ] Error responses do not leak internals.
- [ ] Tests cover the authorization denial, not just the happy path.

## Common mistakes

- **Reviewing the diff without the model.** Symptom: you miss that a new parameter is later used as an object id in another service. Fix: read the data flow, not just the changed lines.
- **Reporting a scanner finding as confirmed.** Symptom: the developer dismisses the channel. Fix: verify the path is reachable and state your confidence.
- **Severity by feel.** Symptom: a critical and a medium arrive with the same label. Fix: state the impact, the attacker prerequisites, and the exposure you assumed.
- **Omitting the attack scenario.** Symptom: a real bug ships because nobody felt it was reachable. Fix: give the concrete steps.
- **Blocking a PR on style nits.** Symptom: review latency grows and security findings get skimmed. Fix: separate blocking findings from suggestions.
- **Ignoring the negative cases.** Symptom: empty lists, negative quantities, and duplicate requests pass review. Fix: test the edges.
- **Assuming the ORM is safe.** Symptom: injection through a raw or text escape hatch. Fix: audit the escape hatches in the diff.

## Anti-patterns

- **"Looks secure to me."** With no articulated reasoning, the next reviewer learns nothing.
- **Reviewing only for what a scanner covers.** Business logic flaws are where the real breaches are.
- **A giant review comment.** Split findings into separate comments so each can be resolved and discussed on its own.
- **Fighting the framework.** Prefer a control the platform already enforces; do not add a bespoke crypto layer.
- **Recommending a library for a one-line fix.** Adding a dependency has its own risk; weigh it.
- **Treating a secret found in history as acceptable because it was never used.** Verify usage, then rotate.
- **Reviewing once at the end.** Security review that is separate from authoring finds defects late and cheaply missed.

## References

- OWASP Code Review Guide - https://owasp.org/www-project-code-review-guide/
- OWASP Developer Guide, security coding - https://cheatsheetseries.owasp.org/
- OWASP Application Security Verification Standard - https://owasp.org/www-project-application-security-verification-standard/
- OWASP API Security Top 10 - https://owasp.org/API-Security/
- CWE Top 25 - https://cwe.mitre.org/top25/
- CWE-639 Authorization Bypass Through User-Controlled Key - https://cwe.mitre.org/data/definitions/639.html
- CWE-89 SQL Injection - https://cwe.mitre.org/data/definitions/89.html
- CWE-78 OS Command Injection - https://cwe.mitre.org/data/definitions/78.html
- CWE-22 Path Traversal - https://cwe.mitre.org/data/definitions/22.html
- CWE-918 Server-Side Request Forgery - https://cwe.mitre.org/data/definitions/918.html
- CWE-502 Deserialization of Untrusted Data - https://cwe.mitre.org/data/definitions/502.html
- CWE-1321 Prototype Pollution - https://cwe.mitre.org/data/definitions/1321.html
- CWE-367 Time-of-check Time-of-use Race Condition - https://cwe.mitre.org/data/definitions/367.html
- CWE-190 Integer Overflow or Wraparound - https://cwe.mitre.org/data/definitions/190.html
- CWE-798 Hard-coded Credentials - https://cwe.mitre.org/data/definitions/798.html
- Semgrep rules documentation - https://semgrep.dev/docs/writing-rules/intro-to-rules/
- OWASP Web Security Testing Guide - https://owasp.org/www-project-web-security-testing-guide/