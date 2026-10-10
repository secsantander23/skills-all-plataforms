# Security Review Checklist

Work through this in order. Each item is a question to answer about the change, not a pattern to grep for. Mark each one explicitly, including the "checked and clean" ones, so the next reviewer knows the ground was covered.

## 1. Trust boundary and data flow

- [ ] What does an unauthenticated attacker control in this change?
- [ ] What does an authenticated attacker control?
- [ ] Can one tenant reach another tenant's data through this path?
- [ ] Which values come from outside the process boundary, and where do they terminate?
- [ ] Is any value passed to another service, or written to a log or a cache, that then becomes input elsewhere?

## 2. Authentication and session

- [ ] Does the change add an endpoint that does not require authentication where it should?
- [ ] Are sessions rotated on login and on privilege change?
- [ ] Are cookies `Secure`, `HttpOnly`, and `SameSite` set or preserved?
- [ ] Do new endpoints implement rate limiting or lockout?
- [ ] Do error responses reveal whether an account exists?
- [ ] Are tokens short-lived, scoped, and revocable, with a revocation path?

## 3. Authorization

- [ ] Is the authorization check on the server, on the resource, not just the route?
- [ ] Is the lookup scoped to the principal in the query itself?
- [ ] Do list endpoints filter by tenant or owner?
- [ ] Do batch and bulk endpoints authorize every item?
- [ ] Do error responses avoid distinguishing forbidden from nonexistent?
- [ ] Are field-level and role-level permissions enforced in the model layer?
- [ ] Is there a negative test proving the denial?

## 4. Input validation

- [ ] Is every external input validated for type, length, range, and format at the boundary?
- [ ] Are unknown fields rejected rather than ignored (mass assignment)?
- [ ] Are enums validated against an allowlist?
- [ ] Are empty, negative, zero, huge, unicode-normalized, and duplicated inputs handled?
- [ ] Is an untrusted value ever used to build a path, a URL, a command, or an expression?

## 5. Injection

- [ ] All queries parameterized, including through ORM raw or text escape hatches.
- [ ] Identifiers (sort columns, directions, table names) allowlisted.
- [ ] No `exec`/`system` with a command string.
- [ ] No `eval` or dynamic template compilation on untrusted input.
- [ ] No CR or LF injected into a response header.
- [ ] Stored values not concatenated into later queries or commands.

## 6. Cryptography

- [ ] TLS verification on; no `verify=False` or equivalent.
- [ ] Passwords hashed with bcrypt, scrypt, or Argon2, with per-user salt.
- [ ] Comparisons use a constant-time primitive.
- [ ] Randomness from a cryptographically secure generator.
- [ ] Encryption uses a vetted library and an authenticated mode with a unique nonce.
- [ ] No homegrown algorithm, and no use of a random library primitive for security decisions.

## 7. Secrets

- [ ] No key, token, password, or connection string in code, tests, fixtures, images, or docs.
- [ ] Secrets come from the environment or a secret manager at runtime.
- [ ] No secret printed, logged, or included in an error message.
- [ ] Separate credentials per environment, and separate per service.

## 8. Data handling

- [ ] Sensitive fields classified, and encrypted or redacted where required.
- [ ] Logs contain no credentials, tokens, full card numbers, or unnecessary personal data.
- [ ] Data retention and deletion defined for anything sensitive.
- [ ] Backups and exports carry the same protections as the primary store.

## 9. Deserialization and parsing

- [ ] Untrusted data deserialized only with data-only formats.
- [ ] Schema validated after parsing, including field types and unknown fields.
- [ ] Archive extraction validates each entry path and rejects absolute or traversing entries.
- [ ] XML parsing disables external entities.
- [ ] JavaScript merges reject `__proto__`, `constructor`, and `prototype`.

## 10. Filesystem and paths

- [ ] User-controlled path segments confined to a resolved root.
- [ ] The check happens after resolution, so symlinks and `..` cannot escape.
- [ ] Upload size enforced while streaming; content type determined from bytes.
- [ ] Files stored outside the web root or served as attachments.
- [ ] Temporary files created securely, and cleaned up on every path.

## 11. Outbound requests

- [ ] User-supplied URLs allowlisted by scheme and host.
- [ ] Resolved addresses validated against private, loopback, link-local, and metadata ranges.
- [ ] Redirects either disabled or re-validated at each hop.
- [ ] Timeouts and response size limits set.
- [ ] Ambient credentials not forwarded.
- [ ] Network egress policy exists as a second layer.

## 12. Concurrency and transactions

- [ ] Check-then-act sequences are atomic, or protected by a lock.
- [ ] Uniqueness enforced by a database constraint, not only by a prior check.
- [ ] Multi-step writes inside one transaction.
- [ ] Counters updated atomically.
- [ ] Idempotency for retried requests (payments, webhooks, order creation).
- [ ] Cache invalidation consistent with the write.

## 13. Numbers and logic

- [ ] Money in integer minor units or a decimal type, never binary floats.
- [ ] Widths and ranges validated before narrowing or arithmetic.
- [ ] Rounding applied once, at a defined point.
- [ ] Units consistent (milliseconds, bytes, percent versus fraction).
- [ ] Pagination bounded, with a maximum page size.

## 14. Errors and logging

- [ ] No stack traces, SQL, or file paths in client responses.
- [ ] Correlation id returned to the client and present in the log.
- [ ] Security events logged: auth, authz denial, privilege change, validation failure, admin action.
- [ ] Log fields sufficient to reconstruct who did what to which resource.
- [ ] Logs shipped off-host and access-restricted.

## 15. Dependencies and build

- [ ] New dependencies reviewed for maintenance, size, and license.
- [ ] Lock file committed and used in CI.
- [ ] Base images pinned to a digest or an immutable tag.
- [ ] No install-time scripts from untrusted packages.
- [ ] CI secrets scoped per job and not exposed to forked pull requests.
- [ ] Branch protection and required review on protected branches.

## 16. Verification

- [ ] Negative tests exist for authorization denial and for validation rejection.
- [ ] Security regression test added for the bug this change fixes.
- [ ] Static analysis and secret scanning pass on the branch.
- [ ] Findings that are "likely" are marked as such, with what would confirm them.