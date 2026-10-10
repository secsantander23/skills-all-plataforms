# Secrets, Provenance, and Trust

## Where a CI secret should live

Ordered by preference. Each step up removes a class of exposure.

1. **No secret.** Public data, a reproducible build from open source.
2. **Short-lived federated credential.** The CI platform signs a token proving its
   identity; the cloud trusts that identity for one job, with a policy that names the
   repository, ref, and environment.
3. **Scoped, short-lived workload identity.** A managed identity assigned to the
   runtime (cluster workload identity, instance role, attached service account),
   rotated by the platform.
4. **Environment-scoped static credential.** Stored in the CI secret store, used
   only by the protected job that needs it, rotated on a schedule.
5. **Long-lived static credential in the repository.** Never.

### OpenID Connect federation, shape of the flow

1. The CI job requests an ID token from its platform's issuer. Claims typically
   include repository, ref, workflow, and environment.
2. The job posts that token to the cloud provider's STS endpoint, requesting a
   credential with no explicit secret.
3. The provider validates the issuer, audience, signature, and claim conditions
   against a trust policy you control.
4. The credential is scoped to that one run and expires with it.

The configuration surface differs per cloud and per CI platform. Look for
"OIDC" or "workload identity federation" in both the CI platform's and the cloud
provider's security documentation, and verify the exact claim names — they are the
part people get wrong, and a wrong claim silently yields zero permissions.

## Secret scanning and rotation

```bash
# Local scan before pushing, e.g. with gitleaks
gitleaks detect --source . --redact

# Git history scan in CI
gitleaks detect --source . --log-opts="--all" --redact
```

Operational rules:

- Rotation is a five-minute job. Write down, for each credential, who can rotate it,
  how, and how the application picks up the new value without a full outage.
- A credential that ever appeared in a log, an artifact, or a commit is compromised.
  Rotate it; scrubbing the artifact is not remediation.
- Scope by environment and by permission. A staging deploy credential should not be
  able to delete a production database.
- Record access to production secrets. If a human can read it, the audit trail is
  the control.

## Untrusted code: fork pull requests

The threat: a fork pull request runs with your repository's secrets if your workflow
uses the wrong trigger or the wrong checkout target.

| Trigger | Runs code from | Secrets available |
|---|---|---|
| `pull_request` | The fork's code | No (fork PRs) |
| `pull_request` | The fork's code | Secrets, for PRs from branches in the same repository |
| `pull_request_target` | Base branch code by default | Yes, always |

Rules:

- Never combine a checkout of untrusted PR code with a trigger that exposes secrets.
- If you must build fork PRs, run a credentials-free job that only builds and tests,
  and gate the privileged pipeline on the merged result.
- Treat any secret value a PR-controlled file can influence (a script path, a
  workflow file change, a downloaded binary) as an escalation path.

## Provenance and signing

The chain of custody for a build artifact:

1. **Build in a trusted environment.** The runner or builder must not accept
   arbitrary code from the PR into a privileged build.
2. **Record provenance.** What source revision, what builder, what parameters.
   Provenance can be attached to a container image at build time and stored with it.
3. **Sign the artifact.** A signature over the digest, using a key the pipeline
   controls.
4. **Verify before deploy.** The deployment must check the signature and refuse an
   unsigned or wrongly signed artifact. Verification that is only performed in a log,
   not enforced at the trust boundary, provides no security value.

```bash
# Conceptual cosign usage. Verify the current syntax with `cosign --help`.
cosign sign ghcr.io/acme/api@sha256:<digest>
cosign verify ghcr.io/acme/api@sha256:<digest> \
  --certificate-identity-regexp 'https://github.com/acme/api/.github/workflows/ci.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The identity and issuer flags bind the signature to a specific workflow in a specific
repository, which is what stops someone with push access to a different repo from
signing your image with their own key.

## Dependency integrity

```bash
npm audit --audit-level=high        # ecosystem-native audit
pip-audit                          # Python
osv-scanner scan source -r .        # multi-ecosystem, uses the OSV database
trivy image ghcr.io/acme/api:1.4.2  # image layer scan
grype dir:.                        # alternative image/filesystem scanner
checkov -d .                        # IaC policy checks
```

Making a scanner useful requires a triage policy, otherwise it is disabled after the
first week of noise:

- Severity threshold plus an exception list with owners and expiry dates.
- New findings on a change block; the existing backlog is tracked separately.
- Base images tracked with an update policy, not frozen forever.
- Findings on artifacts you ship, using the digest, so the scanned image is the
  deployed image.

## Access control around the pipeline

- Branch protection on the default branch; require review for pipeline definition
  changes because the pipeline is production code.
- Environments with required reviewers and wait timers for production.
- Concurrency control so two deploys cannot interleave.
- Log retention long enough to investigate an incident, short enough to respect data
  retention policy — and treat build logs as containing production identifiers.
- Audit who can re-run, cancel, and approve jobs.

## Quick verification of a pipeline's trust properties

Ask these of any pipeline you review:

1. Can code from a fork influence a job that holds credentials? If yes, it is a
   vulnerability, not a style issue.
2. What is the artifact, exactly, and is its digest recorded anywhere?
3. If a registry is compromised and a tag is repointed, would the deploy catch it?
4. Can an attacker who can push one commit exfiltrate any credential?
5. Does a failing security gate actually block, or only report?