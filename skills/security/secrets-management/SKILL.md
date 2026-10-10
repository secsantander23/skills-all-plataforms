---
name: Secrets Management
description: Covers why secrets must not be hardcoded, secret types, storage options from environment variables to secret managers and KMS, rotation strategies, preventing and remediating leaks with gitignore and history rewriting, least-privilege access to secrets, injecting secrets into CI/CD and containers, pre-commit and CI leak detection, and incident response for a leaked secret. Use when the user asks about API keys, credentials, .env files, secret stores, Vault or cloud secret managers, detecting committed secrets, or responding to a leaked key.
metadata:
  opencode/autoinvoke: true
---

# Secrets Management

A secret is any value whose disclosure grants access: API keys, database passwords, signing keys, private keys, tokens, connection strings, and any value that can substitute for authentication. The core mental model is that a secret has a *lifecycle* (create, store, use, rotate, revoke, audit), and the only reason to build one is that real-world lifecycles do not match the lifecycle of the code that uses them.

## When to use this

- A credential appears in code, a config file, a commit, or a log.
- Choosing where to store credentials, or migrating off environment variables to a real secret store.
- Designing how secrets reach a deployment, a CI job, or a container.
- A key leaked to a public repository, a paste site, or a build log.
- Setting up scanning so the next leak is caught in seconds, not months.
- Deciding how often to rotate, and how rotation happens without downtime.

## Why not to hardcode

| Risk | Mechanism |
| --- | --- |
| Version control permanence | The value is in every clone, every fork, every cache, and forever |
| Accidental publication | Private repositories get made public; forks escape; mirrors and backups exist |
| Image and artifact leakage | A secret baked into a layer persists in the image registry even if the layer is deleted |
| Build log exposure | Echoed environment variables and debug flags end up in CI logs that are broadly readable |
| Human and process failure | Everyone who needs access gets a copy, so revocation is impossible |
| Cross-environment reuse | One development key works against production because nobody stopped it |

The technical argument: a hardcoded secret cannot be rotated without a code change and a redeploy, cannot be scoped to a single consumer, cannot be audited per use, and cannot be revoked without revoking everyone.

## Secret types

| Type | Example | Notes |
| --- | --- | --- |
| Symmetric key | Database password, API key | One value, both parties hold it. Rotate means both sides change |
| Asymmetric private key | TLS key, SSH key, signing key | Protect the private half. Never store the public half as a secret |
| Credential bundle | Connection string with a password | Rotate atomically; partial rotation can lock you out |
| Token | OAuth refresh token, session token | Short-lived where possible; revocable by design |
| Certificate with a private key | Client certificate (`.pfx`, `.pem`) | Often missed: certificate files contain a private key |
| Encryption key data key | KMS data key material | Usually generated and used inside the KMS rather than exported |
| App-specific secret | Stripe key, webhook signing secret, Sentry DSN | Often not individually revocable; know what a rotation looks like |

Note which of your secrets support overlapping validity. Most providers let you issue two valid credentials at once, which is what makes zero-downtime rotation possible. Check yours before promising it.

## Storage options

Ordered roughly by increasing capability. Choose the simplest option that meets your threat model and compliance requirements.

| Option | Use when | Limits |
| --- | --- | --- |
| Environment variables | Local development, single host, non-production | Visible in `ps`, in crash dumps, and to anything that can read the process environment. No rotation without redeploy |
| Untracked `.env` file | Local development | Must be gitignored everywhere; easily copied into an image |
| Container or orchestration secret | Kubernetes `Secret`, Docker Compose secrets | Better than environment variables; note that base64 is encoding, not encryption, unless you enable the platform's encryption at rest |
| Cloud secret manager | Production on a cloud platform | Network dependency; needs a retry-safe bootstrap and a credential to access the store itself |
| HashiCorp Vault or similar | Multiple clouds, dynamic credentials, PKI | Operationally significant. Worth it when the simpler options no longer cover your requirements |
| KMS envelope encryption | Encrypting bulk data with a key in a key management service | Data key is generated per object; master key never leaves the KMS |

The bootstrap problem is the hard part. A secret store needs a credential to unlock it, and that credential is itself a secret. Common answers, roughly in order of preference:

1. Workload identity: the platform proves *what workload* is calling (instance metadata, a projected service account token, a managed identity). No stored secret at all.
2. Short-lived, automatically issued credentials with automatic rotation.
3. A single bootstrap credential stored in the platform's secret store, itself rotated, accepted as a contained risk with monitoring.

```python
import os

# Local development: read from the environment, validate at startup, fail loudly.
def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


DATABASE_URL = require_env("DATABASE_URL")
STRIPE_SECRET_KEY = require_env("STRIPE_SECRET_KEY")
```

Start up in an environment where a secret is missing and you find out immediately. Discover it on the first request in production instead, and you find out during an incident.

```python
# Fetch from a cloud secret manager, cache in memory, refresh on a TTL so that
# a rotated value is picked up without a redeploy.
import json
import time
from typing import Any


class SecretCache:
    def __init__(self, secret_id: str, region: str, ttl_seconds: int = 300):
        self.secret_id = secret_id
        self.region = region
        self.ttl = ttl_seconds
        self._cache: dict[str, Any] = {}
        self._fetched_at: float | None = None

    def get(self, key: str) -> Any:
        now = time.monotonic()
        if self._fetched_at is None or (now - self._fetched_at) > self.ttl:
            self._refresh(now)
        return self._cache[key]

    def _refresh(self, now: float) -> None:
        import boto3

        client = boto3.client("secretsmanager", region_name=self.region)
        payload = json.loads(client.get_secret_value(SecretId=self.secret_id)["SecretString"])
        self._cache = payload
        self._fetched_at = now
```

Confirm the client library and API for your platform's secret manager in its own documentation.

## Least privilege for secrets

Access to a secret is access to everything it can do. Scope it.

- One secret per consumer and per environment, not one shared value across ten services.
- Read-only access at runtime. A service that only reads a database password has no business rotating it.
- Path-scoped policies: a service may read `prod/db/*` and nothing else.
- Audit every read. A read log is how you detect a leak and how you prove containment.
- No human long-lived access. Break-glass access is time-bound, approved, and logged.
- Separate credentials for each environment, always. A staging compromise should not reach production.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowReadOnlyFromOrdersService",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:eu-west-1:111122223333:secret:prod/orders/db-*"
    }
  ]
}
```

## Rotation

Rotation is the control that makes a leaked secret a non-event. A secret you cannot rotate is permanent.

Strategy:

1. **Issue the new value** before revoking the old one.
2. **Deploy** the new value and confirm it is in use.
3. **Revoke** the old value.
4. **Verify** revocation actually took effect, by calling the provider's API to list active credentials.
5. **Record** the rotation time and the actor.

Zero-downtime rotation requires the provider to accept both values concurrently. Check whether yours does before designing around it. If it does not, rotate during a maintenance window or accept a brief failure mode.

Rotation cadence. There is no universal answer. Anchor it to something concrete:

| Secret | Cadence driver |
| --- | --- |
| Long-lived third-party API keys | Provider policy, or quarterly |
| Signing keys (JWT, cookies) | Rotate on a schedule with key ids so verification accepts both during overlap |
| Database passwords | Rotate on a schedule and on staff offboarding |
| Internal service credentials | Rotate on schedule; prefer short-lived issued credentials instead |
| Short-lived tokens | Do not rotate manually. Shorten the lifetime and let them expire |

The best rotation strategy is not rotating more often; it is replacing long-lived secrets with short-lived, automatically issued ones so that rotation is a non-event.

```python
# Overlap pattern: the active key set contains the new key and, until its
# retirement deadline, the previous one. Tokens name their key with `kid`,
# so verification can pick the right public key while both are valid.
class KeyRing:
    def __init__(self, keys: dict[str, "PublicKey"]):
        self._keys = keys

    def public_key_for(self, kid: str):
        if kid not in self._keys:
            raise InvalidToken(f"unknown key id: {kid}")
        return self._keys[kid]
```

The rotation runbook then reads: publish the new public key, generate tokens
with the new `kid`, wait for the maximum token lifetime plus clock skew, remove
the old key. Every token issued before removal still verifies.

## Preventing leaks

### Pre-commit and CI scanning

- Pre-commit hooks (gitleaks, detect-secrets) block the commit locally.
- CI scanning over the whole branch and the full history blocks the merge.
- Platform push protection blocks the push and offers rotation guidance.

```yaml
# .pre-commit-config.yaml (shape only: pin each hook's `rev` to a release tag
# you have verified, and keep that pin in version control)
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: <pinned release tag>
    hooks:
      - id: gitleaks
```

```bash
# Local
pre-commit install
pre-commit run --all-files

# One-off repository scan, including history. Confirm the subcommands and flags
# with each tool's own documentation for the version you install.
gitleaks detect --source .
trufflehog git file://$(pwd)
```

Tune scanners with a baseline file so you can adopt them on an existing repository without blocking every developer on day one. Keep the ruleset narrow enough that developers do not learn to bypass it.

### Design rules that prevent leaks

- No secret literals, even as placeholders that look fake but have the right shape.
- Fail fast on a missing secret rather than falling back to a default.
- Redact at the logging boundary, and never log a whole request body on an auth path.
- Separate test credentials from production, and never point test suites at production.
- Do not put secrets in URLs (they end up in access logs and `Referer` headers).
- Do not pass secrets as command-line arguments; they are visible in the process list.
- `.gitignore` the secret files, and audit that the ignore rule is actually applied.

## A leaked secret: what to do

Order matters. Rotation first, cleanup second.

1. **Revoke and rotate the credential immediately.** Not after cleaning up. Assume it is compromised from the moment it was exposed.
2. **Determine the exposure window.** When was it published, to whom, and what reached it? Check provider access logs if available.
3. **Search for downstream copies.** Registries, forks, caches, container image layers, CI caches, log aggregators, pastes, chat history.
4. **Scrub git history** if it was committed, on all branches and tags.

```bash
# git-filter-repo is the tool to reach for. It rewrites history and requires a fresh clone.
# Install it separately; see the project README for current install instructions.
git filter-repo --sensitive-data-removal --in-place
git filter-repo --path config/secrets.env --invert-paths

# For a specific leaked value, replace it across all history:
git filter-repo --replace-text replacements.txt
# where replacements.txt lines look like:  literal:OLD_SECRET==>REDACTED
```

History rewriting is disruptive: every collaborator must re-clone, open pull requests break, and forks are not covered. It is still worth doing, because the value should already be revoked.

5. **Notify** anyone who needs to know, including security owners, if your policy requires it.
6. **Write it up.** What leaked, through which control, how quickly you detected it, what you changed. Fix the control, not just the value.
7. **Check for misuse** during the exposure window: unexpected API calls, data access from unfamiliar sources, new credentials created with the leaked key.

## Common mistakes

- **Committing, then deleting the file.** The value remains in history, in forks, and in CI caches. Symptom: a scanner flags it months later. Fix: rotate first, then rewrite history.
- **Treating rotation as optional because the repo is private.** Symptom: an org change makes the repo public, or an employee forks it. Fix: assume any repository may become readable.
- **One shared credential for every service.** Symptom: you cannot revoke one service without breaking five. Fix: per-service, per-environment secrets.
- **`.gitignore` added after the first commit.** Symptom: the rule protects nothing retroactively. Fix: verify with `git check-ignore -v <path>` and scan history.
- **Secrets in container image layers.** Symptom: a deleted layer still exists in the registry. Fix: inject at runtime, never `COPY` a secret file into the image.
- **Secrets as command-line arguments.** Symptom: visible in `ps` to any user on the host and in CI process listings. Fix: pass through stdin, a file descriptor, or the environment.
- **Long-lived credentials with no rotation plan.** Symptom: a leak has no remediation other than "rotate", which nobody has rehearsed. Fix: rehearse the rotation.
- **Logging a full request or environment on error.** Symptom: the log aggregator becomes the leak surface. Fix: redact at the boundary.

## Anti-patterns

- **Secrets in a shared wiki, a chat message, or an email.** Every one of those is a copy you cannot revoke.
- **Encrypting a secret in the repository with a key in the same repository.** This is obfuscation with extra steps.
- **A single "master" credential that everything is derived from.** One compromise is total, and the derivation is invisible in audit logs.
- **Auto-revoking on every deploy.** Rotation on every release destroys your ability to audit which credential did what.
- **Trusting a `.gitignore` in a nested directory.** Git ignores do not apply to already-tracked files. Verify with `git ls-files`.
- **Storing a database password in a config-management tool's variables and calling it managed.** It is still a long-lived shared secret, just with a nicer UI.
- **A secret scanner that is advisory only and never runs.** Symptom: it is discovered during the postmortem that it existed.

## Checklist

- [ ] No secret literal anywhere in source, tests, fixtures, or documentation.
- [ ] Startup fails fast when a required secret is missing.
- [ ] Each consumer and each environment has its own secret.
- [ ] Access is read-only, path-scoped, and audited.
- [ ] Rotation is documented, rehearsed, and automated where possible.
- [ ] Short-lived, automatically issued credentials replace long-lived ones where the platform supports it.
- [ ] `.gitignore` covers secret files, and `git check-ignore` confirms it.
- [ ] Secret scanning runs in pre-commit and in CI, with a tuned baseline.
- [ ] CI secrets are scoped per job and hidden from forked pull requests.
- [ ] Containers receive secrets at runtime, never through a build layer.
- [ ] The revocation runbook exists and someone has performed it at least once.
- [ ] Logs and traces redact credentials at the boundary, not by convention.

## References

- OWASP Secrets Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- OWASP Credential Stuffing Prevention Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Credential_Stuffing_Prevention_Cheat_Sheet.html
- OWASP Cryptographic Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html
- git-filter-repo - https://github.com/newren/git-filter-repo
- BFG Repo Cleaner - https://rtyley.github.io/bfg-repo-cleaner/
- gitleaks - https://github.com/gitleaks/gitleaks
- TruffleHog - https://github.com/trufflesecurity/trufflehog
- Yelp detect-secrets - https://github.com/Yelp/detect-secrets
- HashiCorp Vault documentation - https://developer.hashicorp.com/vault/docs
- AWS Secrets Manager - https://docs.aws.amazon.com/secretsmanager/latest/userguide/introduction.html
- Google Cloud Secret Manager - https://cloud.google.com/secret-manager/docs
- Azure Key Vault - https://learn.microsoft.com/en-us/azure/key-vault/general/overview
- GitHub secret scanning and push protection - https://docs.github.com/en/code-security/secret-scanning
- GitLab secret detection - https://docs.gitlab.com/ee/user/application_security/secret_detection.html
- NIST SP 800-63 Digital Identity Guidelines - https://pages.nist.gov/800-63-3/
- SLSA supply chain levels - https://slsa.dev/spec/v1.0/levels