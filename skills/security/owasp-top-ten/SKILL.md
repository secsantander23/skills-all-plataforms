---
name: OWASP Top Ten Defenses
description: Covers the OWASP Top Ten web application security risks mapped to concrete application code: broken access control, cryptographic failures, injection, insecure design, security misconfiguration, vulnerable components, identification and authentication failures, software and data integrity failures, logging and monitoring failures, and server-side request forgery. Use when the user asks about web app security, when hardening an API, when writing authorization checks, when reviewing input handling, or when asking how a vulnerability happens, how to prevent it, and how to detect it.
metadata:
  opencode/autoinvoke: true
---

# OWASP Top Ten Defenses

The OWASP Top Ten is a catalogue of the risk categories that dominate real application breaches. This skill maps each category to *how it happens*, *how to prevent it*, *how to detect it*, and the defensive code that does the prevention. The core mental model is that every category reduces to a small number of engineering controls repeated at every trust boundary.

**The list is versioned.** The canonical, current list is published at https://owasp.org/Top10/. Confirm the edition your compliance program or audit refers to before mapping categories one-to-one in a document.

## When to use this

- Reviewing a new endpoint, service, or data flow for security defects.
- Writing authorization checks, input validation, or output encoding.
- Hardening a deployment: headers, TLS, defaults, error handling.
- Answering "is this a security bug?" during triage or code review.
- Building a threat model or a secure development checklist.
- Deciding what to test and what to monitor.

## The ten, at a glance

| ID | Category | One-line core control |
| --- | --- | --- |
| A01 | Broken access control | Authorize every request server-side against the authenticated principal |
| A02 | Cryptographic failures | TLS everywhere, vetted algorithms, no custom crypto |
| A03 | Injection | Parameterize data; encode data on output |
| A04 | Insecure design | Threat model; enforce invariants you can state |
| A05 | Security misconfiguration | Secure defaults, explicit configuration, no secrets in the repo |
| A06 | Vulnerable and outdated components | Inventory, scan, patch, verify |
| A07 | Identification and authentication failures | Strong password storage, MFA, rate limits, safe sessions |
| A08 | Software and data integrity failures | Verify updates and deserialized data; protect the build pipeline |
| A09 | Security logging and monitoring failures | Log security events with enough context, and alert on them |
| A10 | Server-side request forgery | Do not fetch user-supplied URLs; allowlist when you must |

---

## A01 Broken access control

**How it happens.** The server trusts a client-supplied identifier. Someone changes `?userId=101` to `?userId=102`, or calls another tenant's path directly. It covers missing checks, weak checks, privilege escalation, and metadata-only leaks such as a 403 versus a 404 distinction that enumerates valid ids.

**How to prevent.**

- Authorize on the server for every request, on the resource, not just on the route.
- Never trust a client-supplied identity field. Derive the principal from the verified session or token.
- Scope every query by the owning principal so a missing check becomes a missing row, not a leak.
- Enforce authorization in the data layer and at the service boundary, not only in the UI.
- Use centrally enforced policy (middleware, a policy layer) so new endpoints cannot forget it.
- Disable directory listing and framework debug routes in production.

```python
# Wrong: any authenticated user can read any order.
@app.get("/orders/{order_id}")
def get_order(order_id: str, user=Depends(current_user)):
    return orders.get(order_id)


# Right: the query itself is scoped to the principal.
@app.get("/orders/{order_id}")
def get_order(order_id: str, user=Depends(current_user)):
    order = orders.find_owned(order_id, user.id)
    if order is None:
        raise HTTPException(status_code=404)   # 404, not 403: do not confirm existence
    return order
```

**How to detect.** Log every authorization decision with the principal, the resource, and the outcome. Alert on any burst of 401 and 403 responses from a single principal or source. Add integration tests that attempt cross-tenant access and assert it fails. See `references/defensive-code.md`.

---

## A02 Cryptographic failures

**How it happens.** Data in transit or at rest is protected by nothing, by a homegrown scheme, or by an obsolete one. TLS verification disabled "to fix a certificate error". Passwords hashed with SHA-256. Tokens signed with a weak key. Sensitive values stored in plaintext logs.

**How to prevent.**

- TLS for everything, with certificate verification on and never disabled. Do not pin without understanding the rotation cost.
- Use the platform's vetted crypto library, never a hand-rolled cipher.
- Hash passwords with a memory-hard or salted password KDF (bcrypt, scrypt, Argon2). Never a general-purpose hash.
- Encrypt sensitive data at rest with authenticated encryption (AES-GCM or the platform's KMS envelope encryption).
- Keep secrets out of code, images, and logs. See the `secrets-management` skill.
- Classify data, and only then choose a protection mechanism.

```python
# Wrong: TLS verification disabled.
requests.get(url, verify=False)

# Right: verification on, and a timeout so a hung endpoint cannot exhaust the worker pool.
requests.get(url, timeout=5, verify=True)
```

```javascript
// Wrong: MD5 for passwords. Wrong: plain SHA for short-lived tokens, also common.
const hash = crypto.createHash('md5').update(password).digest('hex');

// Right: a password KDF with per-password salt, tuned to your hardware and verified with --help.
const hash = await argon2.hash(password, { type: argon2.argon2id });
```

**How to detect.** Scan for weak or disallowed algorithms in source and in dependencies. Scan traffic and configuration for disabled TLS verification. Search logs and stored data for plaintext secrets. See the `application-crypto` skill.

---

## A03 Injection

**How it happens.** Untrusted data is concatenated into a query, a shell command, a template, or an expression language. It is still one of the top three categories because string concatenation is the natural way to write code.

Injection subtypes and their fixes:

| Subtype | Where | Fix |
| --- | --- | --- |
| SQL | Query building | Parameterized queries or a query builder |
| NoSQL | Document filters | Validate types; reject objects where scalars are expected |
| OS command | Shell-outs, `exec` | Argument arrays, never a shell string |
| LDAP | Filter strings | Escaped attributes via the driver's API |
| Template / expression | `eval`, `Function`, server-side template injection | Do not evaluate untrusted input at all |
| Header / CRLF | Header and log injection | Strip CR and LF; validate names |
| Log | Log injection | Encode before logging |

```python
# Wrong
cursor.execute("SELECT * FROM users WHERE email = '" + email + "'")

# Right - the driver binds the parameter, so the value is never parsed as SQL.
cursor.execute("SELECT id, email FROM users WHERE email = %s", (email,))
```

```javascript
// Wrong: shell injection through a file name.
const { execSync } = require('node:child_process');
execSync('convert ' + userInput + ' out.png');

// Right: argument array, no shell.
const { execFileSync } = require('node:child_process');
execFileSync('convert', [userInput, 'out.png']);
```

ORMs help only if you use their parameterization. String-built filters do not:

```python
# Wrong: SQLAlchemy still executes this as raw SQL.
session.query(User).filter(text("email = '" + email + "'")).all()

# Right
session.query(User).filter(User.email == email).all()
```

**How to detect.** Static analysis (Semgrep, CodeQL, Bandit, gosec, Brakeman) on every pull request. Pre-commit hooks on changed files. Parameterized-query conventions enforced in review. Fuzz the input surfaces.

---

## A04 Insecure design

**How it happens.** The code is technically free of injection and access control is present, but the *feature* is abusable. No rate limit on password reset, so it becomes an email bomb. A coupon with no limit, so it becomes a pricing exploit. A file upload with no size or type constraint.

**How to prevent.**

- Threat model the design before building: trust boundaries, assets, abuse cases.
- Write down invariants ("one coupon per customer per order") and enforce them in code, not in the UI.
- Add limits everywhere: rate, size, count, value, time.
- Separate tenants at the data layer, not just by a filter.
- Use the security requirements from the OWASP Application Security Verification Standard as acceptance criteria.

```python
# Defensive: a rate limit applied before the handler runs, so the endpoint
# cannot be used as a mail bomb or a password oracle.
#
# The example below uses a per-process counter, which is enough to show the
# control. In production use a shared store (Redis, a database table, or your
# gateway) so the limit applies across instances.
from collections import defaultdict
from threading import Lock

_attempts = defaultdict(list)
_attempts_lock = Lock()


def rate_limit(max_attempts: int, window_seconds: int):
    def decorator(handler):
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            source = request.remote_addr
            with _attempts_lock:
                recent = [t for t in _attempts[source] if now - t < window_seconds]
                if len(recent) >= max_attempts:
                    raise HTTPException(status_code=429, headers={"Retry-After": str(window_seconds)})
                recent.append(now)
                _attempts[source] = recent
            return handler(*args, **kwargs)

        return wrapper

    return decorator


@app.post("/password-reset")
@rate_limit(max_attempts=3, window_seconds=600)
def reset(email: str):
    # Queued, not sent inline, so response timing is constant whether or not
    # the account exists. That closes user enumeration through this endpoint.
    queue_reset_if_known(email)
    return {"ok": True}
```

**How to detect.** Abuse-case testing, not just functional testing. Security requirements in the design document, reviewed with the same care as the API contract. Pentest as a verification step, scoped by design.

---

## A05 Security misconfiguration

**How it happens.** Insecure defaults left in place: verbose stack traces, `DEBUG = True`, directory listing, sample applications shipped, default credentials, permissive CORS, permissive bucket policies, `Access-Control-Allow-Origin: *` with credentials, missing security headers.

**How to prevent.**

- One hardened baseline for every environment, with differences explicit and reviewable.
- No debug mode, no profiler endpoints, and no example code in production images.
- Security headers set at the edge: `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and a restrictive `frame-ancestors`.
- CORS as an explicit allowlist of origins. Never reflect the `Origin` header blindly.
- Least privilege on storage, databases, and cloud identities. No public buckets by default.
- Automated configuration checks in CI, comparing the deployed configuration to the baseline.

```python
# Wrong: reflect any origin.
ALLOWED_ORIGINS = request.headers.get("Origin")

# Right: explicit allowlist.
ALLOWED_ORIGINS = {"https://app.example.com", "https://admin.example.com"}
origin = request.headers.get("Origin")
if origin in ALLOWED_ORIGINS:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
```

**How to detect.** Configuration scanning in CI. Review the deployed headers on every environment with an automated check, not by hand. Remove debug endpoints by default-deny routing rather than by remembering.

---

## A06 Vulnerable and outdated components

**How it happens.** Hundreds of transitive dependencies, base images, and OS packages, none inventoried. A known CVE in a package three levels deep goes unnoticed for a year. A base image is pinned to a floating tag and silently changes.

**How to prevent.**

- Maintain an SBOM (Software Bill of Materials) generated in CI, with the exact versions used for each build.
- Enable automated dependency update pull requests (Dependabot, Renovate, or your platform's equivalent) and require them to build green.
- Pin base images by digest, not by a floating tag.
- Remove dependencies you no longer use. Reach for the standard library first.
- Set an SLA for patching critical CVEs and track it.

**How to detect.** Dependency scanning in CI (the platform's built-in scanner, OSV-Scanner, Trivy, Snyk). Image scanning on every build. Track the age of your oldest unpatched dependency as a metric.

---

## A07 Identification and authentication failures

**How it happens.** Credential stuffing, credential stuffing rate limiting absent, password reuse, weak password storage, session tokens that never expire, missing MFA on privileged actions, user enumeration through login and reset responses, permissive session cookies.

**How to prevent.**

- Hash passwords with a KDF (bcrypt, scrypt, Argon2) with a per-user salt and a tuned work factor.
- Constant-time comparisons everywhere: `hmac.compare_digest` in Python, `crypto.timingSafeEqual` in Node.
- Rate limit and lock out or step up authentication on failures, per account and per source.
- Return identical responses for "no such user" and "wrong password".
- Require MFA for privileged and administrative actions, ideally phishing-resistant.
- Session cookies: `Secure`, `HttpOnly`, `SameSite=Lax` or stricter, and rotate on privilege change.
- Short-lived access tokens, revocable refresh tokens, and a server-side revocation path.

```python
import hmac

# Wrong: leaks timing information.
if submitted == stored_hash:
    ...

# Right
if hmac.compare_digest(submitted, stored_hash):
    ...
```

**How to detect.** Log failed authentication with source, without the password. Alert on password-spraying patterns (many accounts, one source) and on successful logins from new devices or geographies. Test enumeration explicitly.

---

## A08 Software and data integrity failures

**How it happens.** Deserializing untrusted data with a type that can construct arbitrary objects; trusting data that is not signed (CI artifacts, webhooks, JWTs, update manifests); insecure CI/CD where a build can be modified; unsafe YAML and XML parsing.

**How to prevent.**

- Never deserialize untrusted data with `pickle`, Java `ObjectInputStream`, .NET `BinaryFormatter`, PHP `unserialize`, or YAML's unsafe loader. Use a data-only format (JSON, or YAML with a safe loader).
- Verify integrity and authenticity of anything you consume or update: signature checks on artifacts, signature verification on webhooks, pinned dependencies.
- Protect the pipeline: least-privilege credentials, protected branches, required review, and provenance for releases.
- Validate the schema after parsing, not only before.

```python
# Wrong: pickle executes arbitrary code during deserialization.
data = pickle.loads(payload)

# Right: a data-only format, then schema validation.
from jsonschema import validate

instance = json.loads(payload)
validate(instance=instance, schema=PRICE_SCHEMA)
```

```python
# Wrong: YAML loaders can construct Python objects from the document.
config = yaml.load(raw, Loader=yaml.UnsafeLoader)
legacy = yaml.load(raw)              # pre-5.1 default loader; treat as unsafe

# Right: a loader that only produces plain data.
config = yaml.safe_load(raw)
```

**How to detect.** Static analysis rules for unsafe deserialization. Code review of every `deserialize` call. Verify webhook signature failures are logged and alerted on. See `references/defensive-code.md`.

---

## A09 Security logging and monitoring failures

**How it happens.** A breach goes undetected for months. Login failures are not logged, so a slow credential-stuffing attack looks like normal traffic. Logs omit the principal, the source, and the outcome. Logs contain the password or the token, so they become a second credential store.

**How to prevent.**

- Log authentication events, authorization denials, privilege changes, input validation failures, and administrative actions.
- Include enough context: who, what, from where, and the outcome.
- Never log secrets, session tokens, passwords, full card numbers, or unnecessary personal data. Redact at the logging boundary.
- Make logs tamper-resistant (append-only store, restricted access, shipped off-host) so an attacker cannot erase them.
- Alert on patterns, not only on thresholds: enumeration spikes, unusual volume, impossible travel, a new administrative principal.

**How to detect.** Review alert coverage against the threat model. Run a detection exercise: simulate an incident and see whether the alert fires. Track mean time to detect and mean time to respond as metrics.

---

## A10 Server-side request forgery

**How it happens.** The server fetches a URL the user supplied. The user points it at an internal service, the cloud metadata endpoint, or a file scheme. It has been prominent because metadata endpoints hand out credentials and the internal network is often unauthenticated.

**How to prevent.**

- Best: do not fetch user-supplied URLs. Accept an identifier and resolve it against your own known set.
- If you must: allowlist schemes (`https` only) and hosts, resolve the hostname, validate the resolved IP against a deny list including loopback, link-local, and private ranges, then re-check at connect time to defeat DNS rebinding.
- Set short timeouts and response size limits.
- Do not forward ambient credentials to the fetched target.
- Egress proxy or network policy that the application cannot bypass.

```python
import ipaddress
import socket
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlparse

import requests
from fastapi import HTTPException, Request

ALLOWED_HOSTS = {"api.partner.example.com"}


def is_public_ip(host: str) -> bool:
    for info in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def safe_fetch(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("url not allowed")
    if not is_public_ip(parsed.hostname):
        raise ValueError("url resolves to a non-public address")
    response = requests.get(url, timeout=5, allow_redirects=False)
    response.raise_for_status()
    return response.text
```

**How to detect.** Log every outbound request with its resolved IP. Alert on requests to link-local ranges and to internal service names. Network policy review. See `references/defensive-code.md` for the file-upload and path-traversal variants.

---

## Checklist

Before calling a feature "secure", confirm:

- [ ] Every endpoint authorizes server-side against the authenticated principal, scoped in the query.
- [ ] Every input that reaches a query, shell, template, or expression engine is parameterized or not evaluated.
- [ ] Every output that contains user data is encoded for its context.
- [ ] TLS verification is on everywhere, and no code disables it.
- [ ] Passwords use a KDF; comparisons are constant-time.
- [ ] No secrets in code, images, or logs.
- [ ] Untrusted data is never deserialized with a type-executing format.
- [ ] Outbound requests to user-supplied URLs are allowlisted.
- [ ] Rate limits exist on authentication, expensive operations, and abuse-prone features.
- [ ] Security events are logged with context, and at least the top three scenarios alert.
- [ ] Dependencies are inventoried and scanned on every build.
- [ ] Production configuration matches a hardened baseline, with no debug mode and explicit CORS.

## Common mistakes

- **Checking authorization in the UI.** Symptom: an unauthenticated user replays a request with curl and reads another tenant's data. Fix: authorize server-side, scoped in the data layer.
- **"The ORM prevents injection."** Symptom: injection through `text()`, `raw()`, or a filter built by string concatenation. Fix: parameterize; audit raw-query escape hatches.
- **Disabling certificate verification to "fix" a CI error.** Symptom: works everywhere, including for an attacker. Fix: install the correct CA bundle in CI.
- **An allowlist checked against the user-provided hostname only.** Symptom: SSRF via a hostname that resolves to a private address. Fix: validate the resolved IP, and re-validate on connect.
- **A WAF as the only control.** Symptom: a bypass and a false sense of coverage. Fix: fix the code; the WAF is defense in depth.
- **Logging the request body "just for debugging" on an auth endpoint.** Symptom: passwords in the log aggregator, retained for a year. Fix: redact at the logging boundary.
- **Security headers added once, then lost in a proxy rewrite.** Symptom: headers absent only in production. Fix: assert headers automatically against the deployed environment.

## Anti-patterns

- **A security checklist with no verification.** A control nobody tests is a comment.
- **Shared credentials and shared API keys across services and environments.** One leak compromises everything.
- **Tokenizing a payment with a stored PAN and no tokenization.** You have taken on PCI scope you did not need.
- **Homegrown authentication.** Every hand-built auth flow is a research project you did not budget for.
- **Blocking client-side only.** Modify the request and the check disappears.
- **Filtering with a denylist of "bad words" for XSS.** Output encoding is correct and complete; denylists are not.
- **Treating a dependency's "secure by default" badge as a review.** Read its threat model.

## References

- OWASP Top Ten (current edition) - https://owasp.org/Top10/
- OWASP Top 10 2021 (frozen edition, used by many programs) - https://owasp.org/Top10/2021/
- OWASP Cheat Sheet Series - https://cheatsheetseries.owasp.org/
- OWASP Application Security Verification Standard - https://owasp.org/www-project-application-security-verification-standard/
- OWASP Web Security Testing Guide - https://owasp.org/www-project-web-security-testing-guide/
- OWASP Testing Guide (test techniques for each category) - https://owasp.org/www-project-web-security-testing-guide/
- CWE Top 25 - https://cwe.mitre.org/top25/
- CWE category view - https://cwe.mitre.org/data/categories/1000
- OWASP Cheat Sheet, Injection Prevention - https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_Cheat_Sheet.html
- OWASP Cheat Sheet, Access Control - https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- OWASP Cheat Sheet, Logging - https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- OWASP Cheat Sheet, Server Side Request Forgery Prevention - https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- CWE-284 Improper Access Control - https://cwe.mitre.org/data/definitions/284.html
- CWE-78 OS Command Injection - https://cwe.mitre.org/data/definitions/78.html
- CWE-918 Server-Side Request Forgery - https://cwe.mitre.org/data/definitions/918.html
- CWE-502 Deserialization of Untrusted Data - https://cwe.mitre.org/data/definitions/502.html
- NIST SP 800-63 Digital Identity Guidelines - https://pages.nist.gov/800-63-3/