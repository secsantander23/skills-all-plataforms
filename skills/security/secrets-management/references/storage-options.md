# Secret Storage Options Compared

A decision aid. Confirm current product features, pricing, and API surfaces in each vendor's own documentation, since they change.

## Comparison

| Option | Encryption at rest | Rotation | Audit | Operational cost | Good fit |
| --- | --- | --- | --- | --- | --- |
| Environment variable | Depends on the platform | Redeploy | Poor | Very low | Local development, single host |
| Untracked `.env` file | Only if the disk is encrypted | Manual | None | Very low | Local development only |
| Docker Compose secrets (file mounts) | Only if the host disk is encrypted | Manual | None | Low | Small self-contained stacks |
| Kubernetes `Secret` | Only if you enable encryption at rest in etcd | Manual | Basic (etcd, API access) | Medium | Kubernetes workloads |
| Cloud secret manager (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault) | Yes, managed KMS keys | Programmatic | Yes, per-access | Medium | Most cloud production workloads |
| HashiCorp Vault | Yes | Dynamic, automatic | Yes, very granular | High | Multiple clouds, dynamic DB credentials, PKI |
| Platform-managed identities | No secret stored at all | Automatic | Yes | Low to medium | Cloud-native workloads with workload identity |
| KMS envelope encryption | Data keys encrypted by a customer master key | Manual | Cloud audit trail | Medium | Large amounts of application data to encrypt |

## What each layer actually gives you

### Environment variables

Weaknesses people underestimate:

- Visible in `/proc/<pid>/environ` and in `ps e` to users with process visibility rights.
- Inherited by every child process, so a crashing library that dumps its environment leaks everything.
- Frequently printed by crash reporters and by "debug: print environment" code added during an incident.
- No access control: any process that can start in the container can read them.

Still the right default for local development and for simple deployments where the process environment is already protected by platform isolation.

### Container and orchestration secrets

Kubernetes `Secret` objects are base64-encoded, which is encoding, not encryption, unless encryption at rest is enabled for etcd. Most clusters do not enable it by default. Treat a Kubernetes secret as roughly equivalent to a well-protected environment variable, which is a real improvement over a file in an image but not equivalent to a managed store.

File-mounted secrets, as opposed to environment variables, are slightly better because they do not appear in the process environment. Confirm your orchestrator supports file mounts from secrets; the exact field names differ by version, so check the docs.

### Cloud secret managers

The standard answer for cloud production. Typical characteristics:

- Encryption at rest with customer-managed or provider-managed keys.
- Programmatic rotation through an API.
- Access audit logs showing which principal read which secret and when.
- Versioning and rollback.
- Integration with identity services so runtime access uses a workload identity rather than a stored key.

The work they do not do for you: you still need to decide *which* secret each service may read, and you still need to handle the bootstrap credential used to reach the store.

### Vault and similar

Worth the operational cost when:

- You span multiple clouds or multiple clusters and want one policy model.
- You want dynamic credentials: the application authenticates, and Vault issues a short-lived database user that is revoked automatically. This removes the long-lived database password entirely.
- You need PKI: issuing short-lived certificates instead of long-lived ones.
- You need transit encryption with key rotation handled for you.

Not worth it for a single small service on one cloud, where a managed secret store covers the requirement.

## The bootstrap problem

A secret store needs a credential to unlock it. Approaches, strongest first.

### 1. Workload identity (no stored secret)

The platform proves the identity of the calling workload using a token or instance metadata rather than a credential you distribute.

- AWS: IAM roles for EC2, ECS task roles, EKS pod identity or IRSA.
- Google Cloud: service accounts attached to compute instances or workloads, with the metadata server providing tokens.
- Kubernetes: a projected service account token, combined with the cloud provider's workload identity federation.
- Azure: managed identities.

Nothing to leak, nothing to rotate. Choose this whenever the platform offers it.

### 2. Short-lived issued credentials

The application holds a credential that is valid for minutes rather than months, obtained automatically from the platform. The blast radius of a leak is bounded by the credential lifetime.

### 3. A single bootstrap secret

One credential stored in the platform's own secret store, used to reach that store. This is a contained risk, and only a contained risk. Mitigate it:

- Scope it as narrowly as possible.
- Rotate it on a schedule and on staff changes.
- Alert on reads that do not match the expected access pattern.
- Do not let it be usable outside the workload identity it belongs to.

### 4. A key file baked into the image

Acceptable only if the image itself is access-controlled and the file has restrictive permissions. Do not commit the key. Treat it as a last resort and document the risk.

## Separation by environment and by consumer

Design the secret *identity* before you create the secret.

| Axis | Recommendation |
| --- | --- |
| Environment | One secret per environment. Never reuse a staging credential in production |
| Service | One secret per consumer. Revoking one service then affects only that service |
| Purpose | Separate the signing key from the database password from the third-party API key |
| Blast radius | Scope database credentials to a read-only role where read-only is enough |
| Lifetime | Separate long-lived infrastructure credentials from short-lived user tokens |

Naming that scales:

```
prod/orders/db-credentials
prod/orders/stripe-api-key
prod/billing/jwt-signing-key
staging/orders/db-credentials
```

The path encodes environment and consumer, which makes policy rules and access review mechanical rather than a judgement call.

## Policy example

Scope a principal to a path prefix and to a read action only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OrdersServiceReadDbOnly",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:eu-west-1:111122223333:secret:prod/orders/db-credentials-*"
    }
  ]
}
```

Note the trailing wildcard. Secret ARNs include a random suffix, so an exact ARN without it will not match.

## Inventory and review

Keep a registry of every secret with:

- What it is, and what it grants.
- Where it is stored, and which paths it maps to.
- Which principals can read it.
- When it was created, when it was last rotated, and its next rotation date.
- The revocation procedure.

Review it on a schedule. Secrets you forgot you had are the ones still enabled two years later.

## Anti-patterns

- A single credential for every service and environment.
- Long-lived cloud keys when workload identity is available.
- Secrets stored in the same system they protect (a database password in the database it unlocks, on the database).
- A configuration file committed with placeholder values that get filled in by a deploy script writing into the repository.
- Secrets in a shared vault folder where every engineer has read access, with no per-service scoping.
- Rotating only the primary credential and leaving the previous one valid indefinitely.

## References

- OWASP Secrets Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- OWASP Cryptographic Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html
- HashiCorp Vault documentation - https://developer.hashicorp.com/vault/docs
- AWS Secrets Manager user guide - https://docs.aws.amazon.com/secretsmanager/latest/userguide/introduction.html
- AWS KMS concepts - https://docs.aws.amazon.com/kms/latest/developerguide/concepts.html
- Google Cloud Secret Manager - https://cloud.google.com/secret-manager/docs
- Azure Key Vault overview - https://learn.microsoft.com/en-us/azure/key-vault/general/overview
- Kubernetes Secrets - https://kubernetes.io/docs/concepts/configuration/secret/
- Kubernetes encryption at rest - https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/
- SLSA supply chain levels - https://slsa.dev/spec/v1.0/levels