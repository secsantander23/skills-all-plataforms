# Deploying on Google Cloud

## Cloud Run revisions and traffic splitting

```bash
# Deploy a new revision without shifting traffic
gcloud run deploy acme-api \
  --image us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.5.0 \
  --region us-central1 --no-traffic \
  --tag candidate \
  --no-allow-unauthenticated

# Test the candidate revision directly
CANDIDATE_URL=$(gcloud run services describe acme-api \
  --region us-central1 --format='value(status.url)' | sed 's|$|-candidate|')
curl -fsS "https://$CANDIDATE_URL/version"

# Shift 10 percent
gcloud run services update-traffic acme-api \
  --region us-central1 \
  --to-revisions candidate=10,acme-api-00041-abc=90

# Promote
gcloud run services update-traffic acme-api \
  --region us-central1 \
  --to-latest
```

```bash
# Rollback
gcloud run revisions list --region us-central1 --service=acme-api --limit=5
gcloud run services update-traffic acme-api \
  --region us-central1 --to-revisions acme-api-00041-abc=100
```

## Cloud Run jobs

```bash
gcloud run jobs create db-migrate \
  --image us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.5.0 \
  --region us-central1 \
  --set-env-vars NODE_ENV=production \
  --command=./node_modules/.bin/knex --args=migrate:latest \
  --service-account=acme-api@acme-prod-us.iam.gserviceaccount.com \
  --max-retries=1 --timeout=1800s \
  --set-cloudsql-instances=acme-prod:us-central1=acme-prod

gcloud run jobs execute db-migrate --region us-central1 --wait
gcloud run jobs describe db-migrate --region us-central1
```

Job configuration surfaces: task count, parallelism, task timeout, retry count,
and whether tasks may retry. Choose retries carefully for migrations; a partially
applied schema change retried automatically is a bad afternoon.

## Cloud Run with Cloud SQL connector

```bash
gcloud run deploy acme-api \
  --image us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.5.0 \
  --region us-central1 \
  --add-cloudsql-instances=acme-prod:us-central1=acme-prod \
  --set-env-vars=CLOUD_SQL_INSTANCE=acme-prod \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest
```

The connector handles IAM-based authentication and pooling. In the application, use
the platform-specific connection mechanism so the connector is used; a plain TCP
connection string bypasses it. Verify which languages and drivers support the
connector in the Cloud SQL documentation.

## GKE Autopilot versus Standard

| Concern | Autopilot | Standard |
|---|---|---|
| Node management | Google manages nodes and DaemonSets | You manage node pools and versions |
| Billing | Per pod resource requests | Per node |
| DaemonSets | System workloads only, partly restricted | Full control |
| GPU / specialized hardware | Limited; verify availability | Available |
| Network policy / custom CNI | Limited choices | Full |
| System workloads | Some add-ons not supported | Full |
| Node-level tuning | Not available | Available |

```yaml
# Deployment that works on either
apiVersion: apps/v1
kind: Deployment
metadata:
  name: acme-api
  namespace: acme-prod-us
spec:
  replicas: 3
  selector:
    matchLabels: { app: acme-api }
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  template:
    metadata:
      labels: { app: acme-api }
    spec:
      serviceAccountName: acme-api
      securityContext:
        runAsNonRoot: true
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels: { app: acme-api }
      containers:
        - name: api
          image: us-docker.pkg.dev/acme-prod-us/apps/acme-api:1.5.0
          ports: [{ name: http, containerPort: 8080 }]
          resources:
            requests: { cpu: 200m, memory: 256Mi }
            limits: { memory: 512Mi }
          readinessProbe:
            httpGet: { path: /readyz, port: http }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 10
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
---
apiVersion: v1
kind: Service
metadata:
  name: acme-api
  namespace: acme-prod-us
spec:
  selector: { app: acme-api }
  ports: [{ name: http, port: 80, targetPort: http }]
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: acme-api
  namespace: acme-prod-us
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: acme-api }
  minReplicas: 3
  maxReplicas: 40
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
```

```bash
gcloud container clusters get-credentials acme-prod-us --region us-central1
gcloud container clusters describe acme-prod-us --region us-central1 \
  --format='value(status.currentMasterVersion)'
gcloud container clusters upgrade acme-prod-us --region us-central1
kubectl -n acme-prod-us get pods -o wide
```

Workload Identity for GKE: enable Workload Identity on the cluster and node pools,
create an IAM binding between the Kubernetes service account and the Google service
account, and annotate the pod spec with the Google service account email. Verify
current setup steps in the Workload Identity documentation, since the wiring has
changed across releases.

## Cloud Functions (2nd gen)

```javascript
// index.js
const { CloudEvent } = require('@google-cloud/functions-framework');

/**
 * @param {CloudEvent} event
 */
async function onObjectFinalized(event) {
  const { bucket, name, generation } = event.data;
  if (!name || !name.endsWith('.csv')) return;

  // Idempotency: the same event can be delivered more than once.
  console.log(JSON.stringify({ bucket, name, generation }));

  await processImport({ bucket, name, generation });
}

require('@google-cloud/functions-framework').cloudEvent(
  'onObjectFinalized',
  onObjectFinalized
);
```

```bash
gcloud functions deploy onObjectFinalized \
  --gen2 \
  --runtime=nodejs22 \
  --region=us-central1 \
  --source=. \
  --entry-point=onObjectFinalized \
  --trigger-bucket=acme-prod-imports \
  --service-account=acme-import@acme-prod-us.iam.gserviceaccount.com \
  --max-instances=10 \
  --timeout=540s \
  --memory=512Mi \
  --max-retries=3 \
  --min-instances=0 \
  --concurrency=1
```

Function-shaped programs must be idempotent: event delivery guarantees are
at-least-once, and retries are the default behavior when something fails.

## Artifact Registry and Cloud Build

```bash
gcloud artifacts repositories create apps \
  --repository-format=docker \
  --location=us-central1 \
  --description="Application images" \
  --immutable-tags

gcloud artifacts repositories list --project=acme-prod-us
gcloud artifacts docker images list us-docker.pkg.dev/acme-prod-us/apps/acme-api
```

`--immutable-tags` prevents a tag from being repointed, which turns a supply chain
problem into a loud failure instead of a silent substitution.

```bash
gcloud builds submit \
  --tag us-docker.pkg.dev/acme-prod-us/apps/acme-api:$SHORT_SHA \
  --timeout=1800s \
  . \
  --substitutions=_REGION=us-central1,_REPOSITORY=apps
```

## Secret Manager in the runtime

```bash
# Cloud Run: reference a secret as an environment variable
gcloud run deploy acme-api --region us-central1 \
  --update-secrets=DATABASE_URL=DATABASE_URL:latest

# Cloud Run: mount as a file (better for large values and rotation)
gcloud run deploy acme-api --region us-central1 \
  --add-volume=name=secrets,volume-source=secretManager:name=APP_CONFIG \
  --add-volume-mount=volume=secrets,mount-path=/etc/secrets
```

For Kubernetes, two common shapes:

```bash
# Google provider in the external-secrets operator
kubectl apply -f - <<'EOF'
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: app-config
  namespace: acme-prod-us
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gcp-secrets
    kind: ClusterSecretStore
  target:
    name: app-config
    creationPolicy: Owner
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: DATABASE_URL
EOF

# CSI driver mount (Kubernetes-native, no external controller)
kubectl create secret generic app-config \
  --from-literal=DATABASE_URL="$(gcloud secrets versions access latest --secret=DATABASE_URL)"
```

## Smoke test

```bash
#!/usr/bin/env bash
set -euo pipefail

SERVICE="$1" REGION="${2:-us-central1}" EXPECTED_VERSION="${3:?expected version required}"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(status.url)')

# A private service returns 403 without auth; expect that for the public probe.
code=$(curl -sS -o /dev/null -w '%{http_code}' "$URL/healthz")
case "$code" in
  200|403) ;;
  *) echo "unexpected status $code from $URL/healthz" >&2; exit 1 ;;
esac

# Check the serving revision explicitly
serving=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(status.traffic[0].revisionName)')
echo "traffic: $serving"

case "$serving" in
  *"$EXPECTED_VERSION"*) ;;
  *) echo "expected revision containing $EXPECTED_VERSION, got $serving" >&2; exit 1 ;;
esac
```

## Rollback paths to know before deploying

```bash
# Cloud Run
gcloud run services update-traffic SERVICE --region REGION --to-revisions PREV=100

# GKE
kubectl -n NS rollout undo deployment/NAME
kubectl -n NS rollout history deployment/NAME

# Cloud SQL: trigger a failover to the standby
gcloud sql instances failover acme-prod
```

Write these down per service. Discovering the rollback command during an incident
adds minutes to an outage you were trying to shorten.