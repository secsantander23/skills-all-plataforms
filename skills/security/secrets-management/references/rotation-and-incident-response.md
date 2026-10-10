# Rotation and Incident Response

## Rotation

### Why rotate at all

Rotation is the only control that bounds the damage of a secret that has leaked and been missed. Every other mitigation assumes the leak does not happen. Rotation assumes it does, and limits how long it is useful.

It also limits the value of a leaked secret that is found later: a credential you rotated three months ago is a historical artifact rather than an active key.

### The five-phase rotation

```
1. ISSUE      create the new credential alongside the old one
2. DEPLOY     roll the new value to every consumer
3. VERIFY     confirm the new value is actually the one in use
4. REVOKE     disable or delete the old credential at the provider
5. RECORD     log the time, the actor, and the scope
```

Skipping step 3 is the common failure. Deployments sometimes partially succeed, a configuration cache holds the old value, or a service was missed. Verify before revoking, or you cause an outage instead of a rotation.

### Evidence that the new key is in use

- Provider access logs showing reads with the new credential identifier.
- Application logs emitting a non-secret key identifier at startup.
- A health or status endpoint that reports which key version is loaded.

Never log the key itself. A key *identifier* (the last four characters, or a version number the provider assigns) is enough to prove rotation and carries far less risk.

### Overlap requirements

Zero-downtime rotation requires the provider to accept both credentials concurrently.

| Credential type | Overlap generally possible? |
| --- | --- |
| Database user | Yes, create a second user with the same grants |
| Cloud access key | Yes, issue a second key |
| Basic auth password | Sometimes, via a second allowed user or an app-level switch |
| API signing key (HMAC) | Yes, verify against a key ring containing both |
| JWT verification key | Yes, publish the new public key and keep the old one until tokens expire |
| OAuth client secret | Usually, by registering a second client |
| Single shared symmetric key, one provider | Often not. Plan a maintenance window |

### What to rotate, and how often

There is no universal cadence. Anchor on something: a provider policy, a compliance requirement, or a named risk.

Prefer to *replace* rather than *rotate* where possible. A short-lived, automatically issued credential removes the rotation problem entirely.

| Secret | Approach |
| --- | --- |
| Third-party API key | Rotate on a schedule; confirm the provider supports two active keys |
| Database credential | Create a second user, deploy, revoke the first |
| Internal service credential | Move to workload identity or short-lived issued tokens |
| JWT signing key | Publish the new public key with a `kid`, wait out the maximum token lifetime, remove the old |
| Database encryption key | Envelope encryption: re-wrap data keys under a new master key |
| Password hashing parameters | Stored hashes keep their parameters; rehash on next successful login |
| Client TLS certificate | Issue a new certificate, deploy, revoke the old one |

Rehashing deserves a note: password hashes record the algorithm and cost that produced them. When you increase the work factor, rehash on the user's next successful login rather than forcing a reset.

## Incident response for a leaked secret

Assume compromise the moment you learn of exposure. The investigation informs the write-up; it does not gate the rotation.

### Phase 1: contain (minutes)

1. Revoke and reissue the credential at the provider.
2. If the credential had elevated privilege, revoke everything it could reach, not just the credential.
3. If the leak was to a public location, request removal from the platform while you revoke. Do not wait for removal.

Revoking first is correct even when you believe the exposure was contained, because a false negative is far more costly than an unnecessary rotation.

### Phase 2: scope (hours)

Answer these questions and write the answers down:

- **When** did it become publicly reachable? Commit date, push time, paste time.
- **Who** could have seen it? A private repository's contributors, a build artifact's audience, a public paste.
- **What** did it grant? Map the credential to every system it authenticates to.
- **What was used?** Review provider access logs for the credential's activity during the exposure window. Look for unfamiliar sources, unusual endpoints, data exports, new keys created, and permission changes.
- **Where else did it propagate?** Container registries, image layers, CI caches, backups, log aggregators, forks, mirrors, chat and email history, screenshots.

### Phase 3: clean up

**Rotation is the fix.** History rewriting is hygiene.

```bash
# git-filter-repo rewrites history in place and requires a fresh clone afterward.
# Install separately; see the project README for current instructions.
git filter-repo --path config/secrets.env --invert-paths

# Replace a specific literal across all history.
git filter-repo --replace-text replacements.txt
# replacements.txt:  literal:OLD_SECRET==>REDACTED
```

Consequences to plan for before you start:

- Every collaborator must re-clone. Old clones keep the value.
- Open pull requests reference the old history and will break.
- Forks are not rewritten by your rewrite.
- Commit hashes, signatures, and blame history change.
- CI caches and release artifacts must be purged separately.

Purge image layers from the registry and invalidate CI caches and build artifacts, because a secret that remains in an artifact is still extractable.

### Phase 4: verify

- Confirm the old credential is rejected by the provider. Call the API and check the credential list.
- Confirm no service is still using it, by reviewing access logs for activity attributed to the old value.
- Run a fresh secret scan over the repository and its history.
- Check that the value does not appear anywhere in monitoring, logging, or documentation systems.

### Phase 5: write it up

A good incident write-up contains:

- Timeline, from first exposure to containment.
- How it was detected. If it was a user report, say so.
- Blast radius, quantified: accounts, records, requests.
- The control that failed, and why it failed.
- What you changed to prevent recurrence, with owners and dates.
- Anything you could not determine, stated plainly.

The control, not the value, is the thing to fix. "Someone committed a secret" is not a root cause; "pre-commit secret scanning was not configured on repositories created by this team" is.

## Detection controls worth having

| Control | Detects | Note |
| --- | --- | --- |
| Pre-commit scanner | The developer, before the commit | Fastest feedback; developers sometimes bypass |
| CI scan on the branch | The PR, before merge | Requires review to notice a new baseline |
| Platform push protection | The push, immediately | Often offers rotation guidance |
| Full-history scan | Legacy leaks at adoption time | Create a baseline file for pre-existing findings |
| Log and data scanning | Leaked values in logs, backups, exports | Catches propagation the repo scan misses |
| Canary credentials | Unauthorized use of a leaked key | Some providers support reporting the compromise |
| Anomaly alerts on the provider's use | Actual misuse | The only control that catches a silent leak |

## Drills

Run the rotation runbook at least once, in a non-production environment, with a timing stopwatch. A runbook nobody has executed is a guess. Measure:

- Time from decision to revocation.
- Time from revocation to all consumers updated.
- How you discovered any consumer you had missed.

## References

- OWASP Secrets Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- OWASP Key Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html
- git-filter-repo - https://github.com/newren/git-filter-repo
- BFG Repo Cleaner - https://rtyley.github.io/bfg-repo-cleaner/
- GitHub secret scanning - https://docs.github.com/en/code-security/secret-scanning
- NIST SP 800-63 Digital Identity Guidelines - https://pages.nist.gov/800-63-3/
- SLSA supply chain levels - https://slsa.dev/spec/v1.0/levels