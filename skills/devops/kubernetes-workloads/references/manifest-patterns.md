# Workload Manifest Patterns

Full, self-contained manifests for common workload shapes. Pin tags to your own
registry and verify every API field with `kubectl explain` against your server
version.

## One-shot migration Job gated on a Deployment

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  namespace: prod
  annotations:
    # Cosmetic: prevents accidental parallel hand-runs.
    "acme.example/change-ticket": "CHG-1042"
spec:
  backoffLimit: 2
  activeDeadlineSeconds: 1800
  ttlSecondsAfterFinished: 3600
  backoffLimitPerIndex: 1      # only meaningful with completionMode: Indexed
  completionMode: Indexed      # gives each task WORKER_INDEX/POD_NAME
  parallelism: 1
  completions: 1
  podFailurePolicy:
    rules:
      - action: FailJob
        onExitCodes:
          containerName: migrate
          operator: NotIn
          values: [0]
  template:
    metadata:
      labels: { app.kubernetes.io/name: migrate }
    spec:
      restartPolicy: Never
      serviceAccountName: migrate
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
      containers:
        - name: migrate
          image: ghcr.io/acme/api:1.4.2
          command: ["./node_modules/.bin/knex", "migrate:latest"]
          envFrom:
            - configMapRef: { name: api-config }
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: { name: api-db, key: url }
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { memory: 1Gi }
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: { drop: ["ALL"] }
```

Run it before rolling the Deployment, and make it idempotent so a re-run is safe.

```bash
kubectl -n prod delete job db-migrate --ignore-not-found
kubectl -n prod apply -f job.yaml
kubectl -n prod wait --for=condition=complete job/db-migrate --timeout=20m
kubectl -n prod logs job/db-migrate
```

## CronJob with a per-run timeout and history limits

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: reindex
  namespace: prod
spec:
  schedule: "*/15 * * * *"
  timeZone: "Etc/UTC"
  startingDeadlineSeconds: 300
  concurrencyPolicy: Forbid
  suspend: false
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 900
      ttlSecondsAfterFinished: 1800
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: reindex
              image: ghcr.io/acme/reindexer:3.1.0
              args: ["--full"]
              resources:
                requests: { cpu: 250m, memory: 512Mi }
                limits: { memory: 2Gi }
```

Check what the controller actually scheduled:

```bash
kubectl -n prod get cronjob reindex -o jsonpath='{.status.lastScheduleTime}{"\n"}'
kubectl -n prod get jobs -l batch.kubernetes.io/cronjob-name 2>/dev/null || \
  kubectl -n prod get jobs
kubectl create cronjob hourly-token-purge \
  --image=ghcr.io/acme/api:1.4.2 \
  --schedule="0 * * * *" \
  --restart=Never
```

## ExternalName Service and headless Service

```yaml
# Points at a service outside the cluster, resolved via cluster DNS
apiVersion: v1
kind: Service
metadata:
  { name: legacy-api, namespace: prod }
spec:
  type: ExternalName
  externalName: legacy.internal.acme.example

---
# Headless: no ClusterIP, DNS returns all ready pod IPs
apiVersion: v1
kind: Service
metadata:
  { name: worker-registry, namespace: prod }
spec:
  clusterIP: None
  publishNotReadyAddresses: false
  selector:
    app.kubernetes.io/name: worker
  ports:
    - { name: grpc, port: 9000, targetPort: grpc }
```

## TLS Secret and certificate rotation

```yaml
apiVersion: v1
kind: Secret
metadata:
  { name: api-tls, namespace: prod }
type: kubernetes.io/tls
data:
  # base64 of the PEM files, no headers
  tls.crt: <base64>
  tls.key: <base64>
```

Generate locally with cert-manager, the ACME client in your ingress controller, or
openssl:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout tls.key -out tls.crt -subj "/CN=api.acme.example"

kubectl -n prod create secret tls api-tls \
  --cert=tls.crt --key=tls.key
```

The kubelet refreshes mounted Secret volumes, but a process that read the file once
into memory will keep the old certificate until restarted or reloaded.

## PDB and topology spread for a highly available Deployment

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  { name: api, namespace: prod }
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: api
---
apiVersion: apps/v1
kind: Deployment
metadata:
  { name: api, namespace: prod }
spec:
  replicas: 3
  selector:
    matchLabels: { app.kubernetes.io/name: api }
  template:
    metadata:
      labels: { app.kubernetes.io/name: api }
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels: { app.kubernetes.io/name: api }
      containers:
        - name: api
          image: ghcr.io/acme/api:1.4.2
          resources:
            requests: { cpu: 200m, memory: 256Mi }
```

A PDB constrains **voluntary** disruptions (node drains, cluster upgrades). It does
nothing about involuntary ones, and a PDB that cannot be satisfied will block node
drains indefinitely.

## PriorityClass and preemption

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  { name: api-critical }
value: 100000
globalDefault: false
description: "Request-serving API; preempts batch work."
preemptionPolicy: PreemptLowerPriority
```

Used on the pod spec via `priorityClassName: api-critical`.

## NetworkPolicy

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  { name: api-allow-dashboard, namespace: prod }
spec:
  podSelector:
    matchLabels: { app.kubernetes.io/name: api }
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: { kubernetes.io/metadata.name: monitoring }
      ports:
        - { protocol: TCP, port: 8080 }
```

A NetworkPolicy that selects no pods is a no-op; a policy that selects a pod and
allows nothing denies everything inbound for that pod. Different CNIs implement this
differently, and some require the CNI to support it at all. Verify with your
networking provider.

## Verifying manifests before they reach a cluster

```bash
kubectl apply --dry-run=server -f manifests/
kubectl diff -f manifests/                       # live diff against the cluster
kubectl explain deployment.spec.strategy
kubectl get --raw /apis | jq '.groups[].name'    # what this cluster actually serves
```

Add `kubeconform`, `kubeval`, or `polaris` to CI if you want schema and policy checks
outside the cluster.