---
name: GCP Deployment
description: Covers Google Cloud deployment including project and resource hierarchy, IAM and service accounts, choosing between Cloud Run, GKE, and Cloud Functions, Cloud Storage classes, Cloud SQL, Secret Manager, Cloud Logging and Monitoring, VPC networking, and cost awareness. Use when the user asks about deploying to Google Cloud, writing IAM bindings or service accounts, choosing a compute platform, configuring Cloud Storage or Cloud SQL, managing secrets in GCP, or reviewing a GCP architecture.
metadata:
  opencode/autoinvoke: true
---

# GCP Deployment

Google Cloud's organizing ideas are the **resource hierarchy** (organization, folder,
project) and **IAM** (who, as which principal, with which roles, on which resource).
Get those two right and the service choices become mechanical.

The service catalog and pricing change often. Treat this as a decision framework and
check current product availability, regional support, quotas, and pricing in the
linked official documentation.

## When to use this

- Deploying an application, container, or scheduled job to Google Cloud.
- Designing organization, folder, and project structure.
- Writing IAM bindings, custom roles, or service account permissions.
- Choosing between Cloud Run, GKE, Cloud Functions, and Compute Engine.
- Storing objects in Cloud Storage and controlling access and cost.
- Provisioning Cloud SQL, including HA configuration and backups.
- Storing and consuming secrets from Secret Manager.
- Setting up Cloud Logging sinks, log-based metrics, alerting policies, and SLOs.
- Setting up VPC, subnet, and firewall configuration.
- Estimating cost or reviewing a GCP architecture.

## Quick start

Cloud Run service with a least-privilege service account and a Secret Manager
binding.

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project acme-prod-us
gcloud config set compute/region us-central1
gcloud config set compute/zone us-central1-a

gcloud projects describe acme-prod-us
gcloud config list
```

```yaml
# service.yaml, deployed with: gcloud run services replace service.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: acme-api
  namespace: acme-prod-us
  labels:
    cloud.googleapis.com/location: us-central1
spec:
  template:
    metadata:
      labels:
        cloud.googleapis.com/location: us-central1
    spec:
      serviceAccountName: acme-api@acme-prod-us.iam.gserviceaccount.com
      containerConcurrency: 80
      timeoutSeconds: 60
      containers:
        - image: us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.4.2
          ports:
            - name: http1
              containerPort: 8080
          env:
            - name: NODE_ENV
              value: production
          envFrom:
            - secretRef:
                name: DATABASE_URL
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
          startupProbe:
            httpGet:
              path: /healthz
              port: 8080
            failureThreshold: 30
            periodSeconds: 2
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            periodSeconds: 30
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

```bash
gcloud run deploy acme-api \
  --image us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.4.2 \
  --region us-central1 \
  --service-account acme-api@acme-prod-us.iam.gserviceaccount.com \
  --no-allow-unauthenticated
```

## Resource hierarchy

```
Organization (managed by Cloud Identity or Workspace)
└── Folders: acme / environments / acme-prod
    ├── Project: acme-prod-us
    ├── Project: acme-prod-eu
    └── Project: acme-staging-us
```

| Level | Use it for | Notes |
|---|---|---|
| Organization | Policy, billing, shared VPC host projects, centrally-managed IAM | Requires an org admin |
| Folder | Grouping by department, environment, or data classification | The main unit of delegation |
| Project | Billing boundary, quota boundary, IAM boundary | Almost everything you create lives in a project |
| Resource | The individual service object | Can hold its own IAM bindings |

Best practice: **one project per environment per region**, plus shared projects for
common services. Separate projects give you separate quotas, separate billing, and
separate blast radius, and they are the precondition for per-environment service
identities.

```bash
gcloud projects create acme-prod-us --name="Acme Prod US"
gcloud projects add-iam-policy-binding acme-prod-us \
  --member group:platform-acme@acme.example \
  --role roles/viewer
gcloud projects get-iam-policy acme-prod-us
gcloud resource-manager folders create --display-name="Environments"
gcloud asset search-all-resources --scope=organizations/ORG_ID --resource-types=compute.googleapis.com/Instance
```

## IAM on Google Cloud

### Policy model

A policy is a list of bindings: principal, role, and optionally a condition.

```bash
# Resource level
gcloud projects add-iam-policy-binding acme-prod-us \
  --member serviceAccount:acme-api@acme-prod-us.iam.gserviceaccount.com \
  --role roles/cloudsql.client

gcloud run services add-iam-policy-binding acme-api \
  --region us-central1 \
  --member allUsers --role roles/run.invoker

# Remove an overly broad public grant
gcloud run services remove-iam-policy-binding acme-api \
  --region us-central1 \
  --member allUsers --role roles/run.invoker
```

### Role granularity

| Type | Example | Use |
|---|---|---|
| Primitive | `roles/owner`, `roles/editor`, `roles/viewer` | Avoid for workloads. `owner` in particular grants broad mutation |
| Predefined | `roles/cloudsql.client`, `roles/pubsub.publisher` | Good default. Document and audit the ones you grant |
| Custom | purpose-built with explicit permissions | Use when a predefined role is too broad and you can maintain it |

```bash
# Inspect what a role actually allows before granting it
gcloud iam roles describe roles/cloudsql.client

# Create a custom role from an existing one by removing permissions
gcloud iam roles create acmeDbOperator \
  --project acme-shared \
  --title="Acme DB Operator" \
  --description="Read/write Cloud SQL instances, no IAM" \
  --permissions=cloudsql.instances.get,cloudsql.instances.update,cloudsql.instances.connect \
  --stage=GA
```

### Conditions

```bash
gcloud projects add-iam-policy-binding acme-prod-us \
  --member group:contractors@acme.example \
  --role roles/viewer \
  --condition='expression=resource.name.startsWith("projects/acme-prod-us/zones/us-central1-a"),title=us-central1-a-only'
```

Conditions are where least privilege becomes precise: time-bounded access, region
restrictions, resource-name prefixes, or requiring a specific tag.

### Service accounts

```bash
gcloud iam service-accounts create acme-api \
  --display-name="Acme API runtime"

# Prefer a smaller predefined role than a hand-built one
gcloud projects add-iam-policy-binding acme-prod-us \
  --member serviceAccount:acme-api@acme-prod-us.iam.gserviceaccount.com \
  --role roles/cloudsql.client

# Restrict which principals can act as the service account
gcloud iam service-accounts add-iam-policy-binding \
  acme-api@acme-prod-us.iam.gserviceaccount.com \
  --member serviceAccount:acme-deployer@acme-prod-us.iam.gserviceaccount.com \
  --role roles/iam.serviceAccountTokenCreator
```

**Never create downloadable service account keys for a running workload.** Keys are
long-lived, appear in Terraform state and CI variables, and are the single most
common Google Cloud credential leak. Use one of:

- Attach the service account directly to the runtime (Cloud Run, GKE Workload
  Identity, Compute Engine instance service account).
- Workload Identity Federation from an external environment such as CI.
- Application Default Credentials from a developer machine.

```bash
# Impersonation, useful for one-off admin work and for CI
gcloud auth activate-service-account --impersonate-service-account=acme-admin@acme-prod-us.iam.gserviceaccount.com
gcloud auth print-access-token | head -c 40; echo

# Workload identity federation (external identity -> service account token exchange)
gcloud iam workload-identity-pools create github-pool \
  --project acme-shared --location global \
  --display-name="GitHub Actions"
gcloud iam workload-identity-pools create-cred-config github-pool \
  --project acme-shared --location global \
  --workload-identity-pool=github-pool \
  --output-file=/tmp/cred-config.json \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == 'acme/acme-api'"
```

Note that the attribute mapping and condition keys depend on your CI provider's claim
names. Read the identity provider's token documentation rather than guessing.

## Compute selection

| Service | You manage | Scales to zero | Best fit |
|---|---|---|---|
| Cloud Run | Container image and config | Yes | HTTP handlers, event-driven, spiky, containerized |
| Cloud Functions | Function and config | Yes | Small glue logic and event triggers |
| GKE Autopilot | Pods and manifests | Yes | You want Kubernetes without node management |
| GKE Standard | Pods, node pools, upgrades | No | Full control over nodes, GPUs, custom networking |
| Compute Engine | Instances and images | No | Persistent servers, unusual OS needs, self-managed software |

Decision path:

1. HTTP-triggered container with a container image already in Artifact Registry: **Cloud
   Run**.
2. Kubernetes is a requirement, or you need its ecosystem: **GKE** (Autopilot if you
   do not want to manage nodes).
3. Small stateless event handler: **Cloud Functions** (second generation is built on
   Cloud Run and is the current model).
4. A long-lived server or software you install and operate: **Compute Engine**, with
   instance groups and autoscaling.

Cloud Run's distinguishing properties: revisions with traffic splitting, per-revision
concurrency and resource limits, scale-to-zero, and integration with Secret Manager,
Cloud SQL connectors, and IAM invoker checks. Verify the current limits in the Cloud
Run quotas page.

## Cloud Storage

```bash
gcloud storage buckets create gs://acme-prod-data \
  --location=us-central1 \
  --uniform-bucket-level-access \
  --default-storage-class=STANDARD

gcloud storage buckets update gs://acme-prod-data \
  --versioning
gcloud storage buckets update gs://acme-prod-data \
  --encryption="projects/acme-shared/locations/us/keyRings/acme/cryptoKeys/bucket"

gcloud storage buckets add-iam-policy-binding gs://acme-prod-data \
  --member serviceAccount:acme-api@acme-prod-us.iam.gserviceaccount.com \
  --role roles/storage.objectCreator

gcloud storage cp --recursive ./uploads gs://acme-prod-data/uploads
gcloud storage objects describe gs://acme-prod-data/uploads/x.bin
gcloud storage signed-url https://storage.googleapis.com/acme-prod-data/uploads/x.bin \
  --expires-in=15m --service-account=acme-api@acme-prod-us.iam.gserviceaccount.com
```

Storage classes, as shapes:

| Class | Shape |
|---|---|
| Standard | Default; lowest latency |
| Nearline | Infrequent access, roughly a day-scale retrieval |
| Coldline | Rarely accessed, longer retrieval |
| Archive | Very rarely accessed, longest retrieval, minimum storage duration |
| Autoclass | Automatic transitions driven by per-object access patterns, for a small monitoring charge |
| Multi-Regional / Dual-Regional | Replication shape, for latency across regions |

Controls that matter:

- **Uniform bucket-level access** makes IAM the only access path and removes
  per-object ACLs, which removes an entire class of confusion and incident.
- **Public access prevention** on every bucket. `allUsers` bindings are a bug.
- **Lifecycle rules** for transitions and expiration; set them at creation.
- **Soft delete and object versioning** for recovery from accidental deletion and
  overwrite.
- **Customer-managed encryption keys** when compliance requires key control; note the
  key location must be co-located with the bucket.
- **Hierarchical namespace** for large buckets, improving listing performance.

## Cloud SQL

```bash
gcloud sql instances create acme-prod \
  --database-version=POSTGRES_16 \
  --tier=db-custom-4-16384 \
  --region=us-central1 \
  --availability-type=REGIONAL \
  --storage-size=100GB \
  --storage-auto-increase \
  --backup-start-time=03:00 \
  --enable-point-in-time-recovery \
  --retained-backups-count=14 \
  --delete-backup-retention-policy \
  --no-assign-ip \
  --network=projects/acme-prod-us/global/networks/acme-vpc \
  --enable-bin-log \
  --availability-config='{"startupCheckEnabled": true}' \
  --database-flags="cloudsql.enable_pglogical=true"

gcloud sql databases create app --instance=acme-prod

gcloud sql users set-password app dbadmin \
  --instance=acme-prod --prompt-for-password
```

- `--availability-type=REGIONAL` creates a synchronous standby in another zone with a
  standby instance. Automatic failover is supported; **failover loses in-flight
  transactions and the failover is not instantaneous**. Size your connection pool for
  the standby too.
- Point-in-time recovery requires binary logs and the `--enable-bin-log` flag; this is
  the flag people miss and then discover during an incident.
- `--no-assign-ip` plus a private service connection or private service access keeps
  the instance off the public internet.
- Read replicas are for read scaling and cross-region standby. Promotion to writer is
  a real topology change.
- Cloud SQL Auth Proxy, the language connectors, and Cloud Run's built-in connectors
  handle IAM authentication and connection pooling. Verify which engines and
  languages the connectors currently support.

```bash
gcloud sql connect acme-prod --user=dbadmin --database=app --quiet
gcloud sql backups list acme-prod
gcloud sql operations list acme-prod --limit=10
gcloud sql instances patch acme-prod --tier=db-custom-4-32768
```

Verify current tier names, memory-per-CPU ratios, flags, and regional availability in
the Cloud SQL documentation; they differ by engine and change over time.

## Secret Manager

```bash
echo -n 'postgres://app:...@10.20.1.10/app' | \
  gcloud secrets create DATABASE_URL --data-file=-

printf '%s' '{"password":"hunter2"}' | \
  gcloud secrets versions add SESSION_SECRET --data-file=-

gcloud secrets versions list DATABASE_URL
gcloud secrets versions add DATABASE_URL --data-file=-

# Access as a value, using the runtime's attached identity
gcloud secrets versions access latest --secret=DATABASE_URL
```

Integration surfaces: Cloud Run mounts secrets as environment variables or files;
GKE integrates with the Secrets Store CSI driver or the Google provider in the
external-secrets operator; Cloud Build can read secrets at build time with the
documented access steps.

Versioning is the point: adding a new version is safe, and rollback is adding a new
version with the old value. `gcloud secrets versions disable` exists for a value that
must stop being usable.

Consumers cache. A cached secret plus a rotation means an outage unless the
application re-reads it on a bounded interval or receives a signal.

## Cloud Logging and Monitoring

```bash
# Log sink to BigQuery for analysis and retention
gcloud logging sinks create bigquery-logs \
  --destination=bigquery.googleapis.com/projects/acme-logs/datasets/logs \
  --log-filter='severity>=WARNING AND resource.type="cloud_run_revision"' \
  --description="Application warnings and above"

# Log-based metric: a real SLI
gcloud logging metrics create api_5xx_count \
  --description="5xx responses from the API" \
  --log-filter='resource.type="cloud_run_revision"
                AND httpRequest.status>=500 AND httpRequest.status<600'

# Alerting policy on the metric. Some subcommands are still flagged as alpha in
# some releases; confirm with `gcloud monitoring policies create --help`.
gcloud monitoring policies create \
  --policy-from-file=policy.json
```

`policy.json` shape:

```json
{
  "displayName": "acme-api 5xx rate",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "5xx above threshold",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/api_5xx_count\" AND resource.type=\"cloud_run_revision\"",
        "aggregations": [
          { "alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE" }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.5,
        "duration": "300s"
      }
    }
  ],
  "notificationChannels": ["projects/acme-prod-us/notificationChannels/1234"]
}
```

```bash
# Uptime check on a user-facing endpoint
gcloud monitoring uptime create acme-api-probe \
  --resource-type=uptime_url \
  --resource-labels=host=acme.example \
  --period=1 \
  --timeout=10 \
  --selected-regions=us-central1,us-east1

# SLO from an uptime check
gcloud monitoring services create acme-api \
  --display-name="acme-api" \
  --project=acme-prod-us
```

Export logs for retention and analysis:

```bash
gcloud logging sinks create archive \
  --destination=storage.googleapis.com/acme-logs-archive \
  --log-filter='severity>=INFO' \
  --retention-days=90
```

Note that excluding `DEBUG` at the sink is the cheapest log cost reduction. Log
volume, not retention, is usually the dominant cost.

## VPC networking

```bash
gcloud compute networks create acme-vpc --subnet-mode=custom
gcloud compute networks subnets create acme-priv-us \
  --network=acme-vpc --region=us-central1 --range=10.20.1.0/24 \
  --enable-flow-logs --enable-private-ip-google-access

gcloud compute routers create acme-router --network=acme-vpc --region=us-central1
gcloud compute routers nats create acme-nat \
  --router=acme-router --region=us-central1 \
  --nat-all-subnet-ip-ranges \
  --auto-allocate-nat-external-ips \
  --nat-all-subnet-ip-ranges

gcloud compute firewall-rules create allow-https-in \
  --network=acme-vpc --direction=INGRESS --action=ALLOW \
  --rules= tcp:22,tcp:443 \
  --source-ranges=10.0.0.0/8 \
  --target-tags=web
```

| Object | Purpose | Note |
|---|---|---|
| Custom-mode network | Explicit subnets | Auto mode creates subnets per region and can surprise you with ranges |
| Subnet with Private Google Access | Outbound to Google APIs without a public IP | Cheaper and faster than NAT for Google APIs |
| Cloud NAT | Outbound to the internet | Egress is metered; Cloud NAT is also metered |
| Firewall rules | Ingress and egress control | `--target-tags` plus `--source-tags` approximate hierarchical policy |
| Hierarchical firewall policy | Organization-wide policy | Priority ordered; verify the ruleset carefully |
| VPC peering | Private connectivity between two VPCs | No transitive routing through peering |
| Shared VPC | One host project owns the network; service projects attach | Centralizes routing and firewall policy |
| Private Service Connect / private services access | Reach Cloud SQL and other managed services privately | Region-specific; verify engine support |

```bash
gcloud compute networks subnets describe acme-priv-us
gcloud compute firewall-rules list --filter=network=acme-vpc
gcloud compute routers nats list acme-router --region=us-central1
gcloud compute networks vpcs peering-connect --name=prod-to-observability
```

Use **target tags** so firewall rules do not break when an IP changes, and mirror the
default-deny posture with a priority-ordered ruleset rather than ad-hoc rules.

## Cost awareness

Levers without quoting prices:

```bash
gcloud billing accounts list
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name=acme-prod-monthly \
  --budget-amount=5000 \
  --threshold-rules="basis=ACTUAL,percent=0.8" \
  --threshold-rules="basis=ACTUAL,percent=1.0" \
  --notifications-default-email \
  --notifications-config="$HOME/.config/gcloud/notify.json"

# Export detailed cost data to BigQuery for real allocation
gcloud billing accounts billing-data enable \
  --billing-account=BILLING_ACCOUNT_ID

gcloud recommender recommendations list \
  --project=acme-prod-us --location=global
```

Levers:

1. **Labels on every resource** (`labels` key-value, visible in billing export), plus
   organization policies or a deployment-time check that rejects unlabeled resources.
2. **Scale to zero** where the latency budget permits. This is Cloud Run's single
   biggest cost characteristic compared with an always-on server fleet.
3. **Committed use discounts** for steady baseline compute; spot VMs for fault-tolerant
   batch. Verify current terms and coverage with the billing documentation.
4. **Log volume control.** Filter at the sink, not at the query. Structured logging
   at DEBUG in production is a large, invisible bill.
5. **Egress awareness.** Cross-region traffic and internet egress are metered and are
   frequently the largest surprise.
6. **Idle resources**: stopped-but-not-deleted disks, unused static addresses,
   abandoned snapshots, forgotten development projects.

Use the Cloud Billing export to BigQuery rather than eyeballing a console chart. It
is the only way to attribute cost to a label, a team, or a single service.

## Checklist

- Projects per environment, with an organization and folder hierarchy above them.
- Root or organization-level administrator separated from day-to-day identities.
- Workloads use attached service accounts, not key files.
- No `roles/owner` or `roles/editor` granted to a service account.
- IAM reviewed against actual API calls (`roles/iam.serviceAccountKeys` usage
  analysis, audit logs).
- Cloud Run services private, with invoker grants to specific service accounts.
- Cloud Storage: uniform bucket-level access, public access prevention, lifecycle
  rules, versioning or soft delete as needed.
- Cloud SQL: regional availability if it matters, PITR enabled, private IP,
  connection pooling, restore tested.
- Secrets in Secret Manager, with a consumer reload plan.
- Log sinks configured for retention, with `DEBUG` filtered out.
- Alerting policies on user-visible symptoms, and SLOs defined for critical journeys.
- Budgets, billing export to BigQuery, and labels enforced at creation.

## Common mistakes

**Symptom: `403 PERMISSION_DENIED` despite a role that looks sufficient.**
Cause: the role was granted on the project but the call targets a different resource
scope, or a binding has a condition that does not match, or the resource lacks a
required permission for the specific operation. Fix: read the exact denied permission
from the error, then check the role definition with `gcloud iam roles describe`.

**Symptom: Cloud Run service returns 403 to external users.**
Cause: the service is private and `allUsers` was never granted, or it is public when
it should not be. Fix: decide deliberately; grant `roles/run.invoker` to the
specific principal.

**Symptom: instance creation fails with `ZONE_NOT_FOUND` or `QUOTA_EXCEEDED`.**
Cause: wrong region/zone for the selected machine type, or a quota. Fix: check
`gcloud compute machine-types list --filter=zone=...` and
`gcloud compute regions describe <region>` for quotas.

**Symptom: a private subnet instance has no internet access.**
Cause: no Cloud NAT route, no default route in the subnet, or firewall egress rules
missing. Fix: check the router NAT configuration and `egress` firewall rules.

**Symptom: storage costs unexpectedly.**
Cause: unfiltered debug logs, old object versions accumulating, or objects left in
Nearline or Coldline before their minimum storage duration. Fix: lifecycle rules with
version expiration, and review the billing export by storage class.

**Symptom: `gcloud auth print-access-token` fails in a container.**
Cause: no attached service account and no Application Default Credentials. Fix: run
on a platform that attaches an identity, or set up Workload Identity Federation.

## Anti-patterns

- `roles/owner` or `roles/editor` on a service account. Use predefined or custom
  roles.
- Service account key files in Terraform state, CI variables, or container images.
- `allUsers` / `allAuthenticatedUsers` bindings on any production resource.
- A single project for all environments, which destroys billing attribution and
  makes per-environment identities impossible.
- Primitive roles for human access. Use groups in Cloud Identity and predefined roles.
- Custom firewall rules with `0.0.0.0/0` on ingress created "to debug", left behind.
- Cloud Storage ACLs alongside IAM, which produces two sources of truth.
- Logging at DEBUG in production without a sink filter.
- Manual console changes with no matching code change and no drift review.
- Alerts on every log line rather than on a symptom with a documented threshold.

## References

- Resource hierarchy: https://cloud.google.com/resource-manager/docs/organizing-resources
- Projects: https://cloud.google.com/resource-manager/docs/creating-managing-projects
- IAM overview: https://cloud.google.com/iam/docs/overview
- Least privilege: https://cloud.google.com/iam/docs/best-practices
- Service accounts: https://cloud.google.com/iam/docs/service-accounts
- Workload Identity Federation: https://cloud.google.com/iam/docs/workload-identity-federation
- IAM Conditions: https://cloud.google.com/iam/docs/conditions-overview
- Cloud Run: https://cloud.google.com/run/docs
- Cloud Run quotas and limits: https://cloud.google.com/run/quotas
- Cloud Functions: https://cloud.google.com/functions/docs/concepts/overview
- GKE Autopilot: https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview
- Cloud Storage classes: https://cloud.google.com/storage/docs/storage-classes
- Cloud Storage lifecycle: https://cloud.google.com/storage/docs/lifecycle
- Cloud SQL: https://cloud.google.com/sql/docs/postgres
- Secret Manager: https://cloud.google.com/secret-manager/docs
- Cloud Logging: https://cloud.google.com/logging/docs
- Cloud Monitoring: https://cloud.google.com/monitoring/docs
- SLOs: https://cloud.google.com/monitoring/slo-guide
- VPC networks: https://cloud.google.com/vpc/docs
- Shared VPC: https://cloud.google.com/vpc/docs/shared-vpc
- Billing export to BigQuery: https://cloud.google.com/billing/docs/how-to/export-data-bigquery
- Recommender: https://cloud.google.com/recommender/docs
- gcloud CLI reference: https://cloud.google.com/sdk/gcloud/reference

Verify before relying on this: Google Cloud product names, regional availability,
quotas, and pricing change frequently. Check `gcloud <group> <command> --help` for
current flags and the linked documentation for the region you are deploying to.