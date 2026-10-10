# GCP Project, Identity, and Networking Layout

## Projects and folders

```
Organization
├── Folder: Platform
│   ├── Folder: Shared Services
│   │   ├── acme-logs          (log sink destination)
│   │   ├── acme-artifacts     (Artifact Registry)
│   │   └── acme-network       (Shared VPC host project)
│   └── Folder: Environments
│       ├── Folder: Production
│       │   ├── acme-prod-us
│       │   └── acme-prod-eu
│       └── Folder: Non-Production
│           ├── acme-staging-us
│           ├── acme-dev-us
│           └── acme-sandbox
└── Folder: Security
    └── acme-security          (audit, SCC export, incident tooling)
```

```bash
gcloud resource-manager folders create \
  --display-name="Environments" --folder=ORG_ID

gcloud resource-manager folders create \
  --display-name="Production" --folder=ENVIRONMENTS_FOLDER_ID

gcloud projects create acme-prod-us \
  --name="Acme Prod US" \
  --organization=ORG_ID \
  --folder=PRODUCTION_FOLDER_ID \
  --set-as-org

gcloud billing projects link acme-prod-us \
  --billing-account=BILLING_ACCOUNT_ID

gcloud projects get-iam-policy acme-prod-us --flatten="bindings[].members" \
  --filter="bindings.members:user:*" --format="table(bindings.role,bindings.members)"
```

## Guardrails with organization policies

Organization policies are constraints rather than permissions: they refuse an
operation that would break a required pattern.

| Policy | Effect | Use for |
|---|---|---|
| `constraints/compute.disableSerialPortAccess` | Blocks serial console access | Preventing instance takeover via console |
| `constraints/compute.requireOsLogin` | Blocks project SSH keys, requires OS Login | Centralized access control and audit |
| `constraints/compute.disableGuestAttributesAccess` | Blocks guest attribute writes | Metadata hardening |
| `constraints/iam.disableServiceAccountKeyCreation` | Blocks service account key creation | The single most valuable one: no more key leaks |
| `constraints/iam.disableServiceAccountKeyUpload` | Blocks key upload | Same |
| `constraints/storage.publicAccessPrevention` | Blocks public bucket access | Storage hardening |
| `constraints/storage.uniformBucketLevelAccess` | Blocks per-object ACLs | Removing two sources of truth |
| `constraints/sql.restrictPublicIp` | Blocks public Cloud SQL IPs | Database hardening |
| `constraints/compute.skipDefaultNetworkCreation` | No auto-created `default` network | Forcing deliberate network design |
| `constraints/compute.restrictXpnProjectLienRemoval` | Protects Shared VPC host | Guarding the network |
| `constraints/gcp.detailedDebugLoggingMode` | Blocks DEBUG | Log cost control |

```bash
gcloud resource-manager org-policies enable-enforcement \
  --organization=ORG_ID \
  --policy=constraints/iam.disableServiceAccountKeyCreation

gcloud resource-manager projects get-ancestors acme-prod-us
gcloud resource-manager organizations describe ORG_ID \
  --format="table(constraints)"
```

Enabling `disableServiceAccountKeyCreation` is the highest-value single guardrail on
this list. It breaks any pipeline that still uploads keys, which is exactly what you
want to discover during a migration rather than during an incident.

## Identity and access review

```bash
# What can a principal actually do?
gcloud projects get-iam-policy acme-prod-us \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:acme-api@acme-prod-us.iam.gserviceaccount.com" \
  --format="table(bindings.role,bindings.condition.expression)"

# Who has owner or editor?
gcloud projects get-iam-policy acme-prod-us \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/owner OR bindings.role:roles/editor" \
  --format="table(bindings.members,bindings.role)"

# Human access review: inactive accounts
gcloud projects get-iam-policy acme-prod-us \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:*" \
  --format="value(bindings.members)" | sort -u > production-humans.txt
diff production-humans.txt previous-quarter.txt

# Service account usage: find identities nothing calls as
gcloud logging read \
  'resource.type=("audited_service_account" "service_account")' \
  --project=acme-prod-us --freshness=30d --format="value(protoPayload.authenticationInfo.principalEmail)" \
  | sort | uniq -c | sort -rn
```

Audit logs are the ground truth for these questions. Grant lists describe intent;
audit logs describe behavior.

## Least-privilege roles in practice

```bash
# What a predefined role actually grants
gcloud iam roles describe roles/pubsub.publisher
gcloud iam roles describe roles/storage.objectUser

# Where is this permission granted across the org?
gcloud projects get-iam-policy acme-prod-us \
  --flatten="bindings[].members" \
  --format="value(bindings.role)" | sort -u

# Trim a role by writing a custom one
gcloud iam roles create acmeBucketWriter \
  --project=acme-shared \
  --title="Acme Bucket Writer" \
  --permissions=storage.objects.create,storage.objects.delete,storage.objects.get \
  --stage=GA
```

A useful review technique: collect the union of permissions the application actually
calls (from audit logs), subtract that set from the role it holds, and treat the
difference as the report.

## Shared VPC

```
Host project: acme-network        owns subnets, routes, firewall hierarchy
Service projects: acme-prod-us, acme-staging-us   attach subnets
```

```bash
gcloud compute shared-vpcs host-project create \
  --project=acme-network --name=acme-host-vpc \
  --network=acme-vpc

gcloud compute networks subnets create prod-us \
  --project=acme-network --network=acme-vpc \
  --region=us-central1 --range=10.20.1.0/24 \
  --enable-private-ip-google-access \
  --enable-flow-logs

gcloud compute shared-vpcs add-iam-policy-binding acme-network \
  --member serviceAccount:acme-prod-sa@acme-prod-us.iam.gserviceaccount.com \
  --role roles/compute.networkUser

gcloud compute shared-vpcs service-projects attach acme-prod-us \
  --host-project=acme-shared --network=acme-vpc
```

Tradeoff: shared VPC centralizes routing and firewall policy but couples service
project delivery to the host project team's availability. A dedicated VPC per
environment is simpler at the cost of duplicated configuration and less central
control. Pick one and write down why.

## Private Service Connect for Cloud SQL

```bash
gcloud compute addresses create prod-sql-psc \
  --project=acme-network --region=us-central1 \
  --network=acme-vpc --subnet=prod-us --addresses=10.20.1.10

gcloud services vpc-peerings connect \
  --service=servicenetworking.googleapis.com \
  --project=acme-network \
  --ranges=10.60.0.0/16 --network=acme-vpc

gcloud sql instances patch acme-prod \
  --network=prod-us \
  --enable-private-service-connect
```

Private Service Connect gives the instance an address inside your VPC with no NAT
and no public endpoint. Verify current support for your engine and region in the
Cloud SQL documentation before designing around it.

## Firewall posture

```bash
# Default deny ingress, tagged services above it
gcloud compute firewall-rules create deny-all-ingress \
  --project=acme-network --network=acme-vpc \
  --direction=INGRESS --action=DENY \
  --rules=all --priority=65534 \
  --source-ranges=0.0.0.0/0

gcloud compute firewall-rules create allow-alb-to-app \
  --project=acme-network --network=acme-vpc \
  --direction=INGRESS --action=ALLOW \
  --rules=tcp:8080 --target-tags=app \
  --source-tags=lb --priority=1000

gcloud compute firewall-rules create allow-egress-dns \
  --project=acme-network --network=acme-vpc \
  --direction=EGRESS --action=ALLOW \
  --rules=tcp:53,udp:53 --target-tags=app --priority=1000

gcloud compute firewall-rules list --project=acme-network --filter=network=acme-vpc
```

With a default-deny rule in place, every explicit rule is an intentional addition
rather than an accidental exposure. Tag-based rules survive instance replacement.

## What to check before a GCP architecture review

1. Is each environment in its own project with its own billing and quotas?
2. Which service accounts exist, and which are actually used?
3. Is `disableServiceAccountKeyCreation` enforced?
4. Which principals have owner or editor?
5. Are all buckets on uniform bucket-level access with public access prevention?
6. Which resources have public IPs, and does each one need one?
7. What is the log volume per project, and what is filtering at the sink?
8. Are egress costs attributed to a label?
9. Does the design survive the loss of a zone, a region, and a service account?
10. Who can delete the production project, and what stops them?