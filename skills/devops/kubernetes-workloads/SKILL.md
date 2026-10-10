---
name: Kubernetes Workloads
description: Covers Kubernetes workload resources including Deployments, Services, Ingress, ConfigMaps and Secrets, liveness, readiness and startup probes, resource requests and limits, QoS classes, rolling update strategy, Jobs and CronJobs, HorizontalPodAutoscalers, namespaces, and RBAC, plus kubectl troubleshooting commands. Use when the user asks about writing or reviewing Kubernetes manifests, pods that are crash looping or never become ready, deployments not rolling out, autoscaling, ingress routing, cluster access permissions, or triaging a failing workload.
metadata:
  opencode/autoinvoke: true
---

# Kubernetes Workloads

Kubernetes is a reconciliation loop. You submit desired state as YAML; controllers
converge the cluster toward it. Most debugging questions are therefore *which
controller has not converged yet, and why* — and almost always the answer is in the
pod's events, its probe configuration, or its resource limits.

## When to use this

- Writing or reviewing Deployment, Service, Ingress, Job, CronJob, or HPA manifests.
- A pod is `CrashLoopBackOff`, `ImagePullBackOff`, `Pending`, or stuck `Terminating`.
- A rollout hangs or rolls back.
- Readiness gates never flip and the Service sends traffic to nothing.
- Containers are being OOMKilled, or the scheduler will not place a pod.
- Deciding requests and limits, or figuring out why a node ran out of capacity.
- Granting a service account or human access to a namespace.
- Working out whether a workload belongs on a cluster at all versus a managed
  container or serverless runtime (see the cloud skills for that comparison).

## Quick start

A Deployment with a Service, probes, resources, and a ConfigMap, applied as one file.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: api-config
  namespace: prod
data:
  LOG_LEVEL: info
  FEATURE_CHECKOUT_V2: "false"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: prod
  labels:
    app.kubernetes.io/name: api
    app.kubernetes.io/part-of: acme
spec:
  replicas: 3
  revisionHistoryLimit: 5
  progressDeadlineSeconds: 600
  minReadySeconds: 10
  selector:
    matchLabels:
      app.kubernetes.io/name: api
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app.kubernetes.io/name: api
        app.kubernetes.io/part-of: acme
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: api
          image: ghcr.io/acme/api:1.4.2
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
          envFrom:
            - configMapRef:
                name: api-config
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: api-db
                  key: url
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 512Mi
          startupProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: prod
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: api
  ports:
    - name: http
      port: 80
      targetPort: http
      protocol: TCP
```

```bash
kubectl apply -f manifests/api.yaml
kubectl -n prod rollout status deployment/api --timeout=5m
kubectl -n prod get pods -l app.kubernetes.io/name=api -w
```

## The mental model for controllers

| You write | Controller does |
|---|---|
| Deployment `spec.replicas: 3` | ReplicaSet creates and deletes Pods to match |
| Deployment `spec.template` | ReplicaSet rolls pods when the template hash changes |
| Service `spec.selector` | Endpoints/EndpointSlice controller publishes matching pod IPs |
| Job / CronJob | Job controller runs pods to completion |
| HPA | Scale subresource controller watches a metrics API and writes `replicas` |
| Namespace deletion | Two-phase: sets `terminating`, waits for cleanup |

Consequence: the Deployment never talks to the pod. It only tells the ReplicaSet
what to aim for. If pods are not being scheduled or are not becoming ready, the
Deployment will keep trying forever and `rollout status` will hang.

## Probes: the single most misunderstood resource

Three probes answer three different questions. Using one endpoint for all three is
the most common cause of cascading restarts.

| Probe | Question | Failure action |
|---|---|---|
| **liveness** | Is the process wedged and unrecoverable? | Container is **restarted** |
| **readiness** | Can this instance serve traffic right now? | Pod is **removed from Service endpoints** |
| **startup** | Has the app finished booting? | Other probes are **suppressed** until it succeeds |

Rules that follow from that table:

- A liveness probe must never depend on a downstream service. If your database is
  down and liveness checks the database, every pod restarts at once, which turns a
  partial outage into a full outage.
- A readiness probe **should** check the critical dependencies that must be up for
  this instance to serve correctly, because that is exactly what the Service uses
  endpoints for.
- Use a `startupProbe` instead of a large `initialDelaySeconds` on liveness. It is
  restart-free, and its `failureThreshold` times the total startup budget
  (`periodSeconds * failureThreshold`).
- `successThreshold` must be `1` for liveness and startup; only readiness allows
  more than one.

Probe handlers: `httpGet`, `tcpSocket`, `exec`, and `grpc`. `httpGet` supports `path`,
`port` (name or number), `scheme`, and `httpHeaders` for auth.

```yaml
readinessProbe:
  httpGet:
    path: /readyz
    port: 8080
    scheme: HTTP
    httpHeaders:
      - name: X-Probe
        value: kubelet
  initialDelaySeconds: 0
  periodSeconds: 5
  timeoutSeconds: 2
  failureThreshold: 3
  successThreshold: 1
```

`timeoutSeconds` matters more than people expect. A probe that hangs for the full
default timeout consumes a kubelet worker and can delay every other pod on that node.

## Requests, limits, and QoS

- **Requests** are used by the scheduler for placement and by the QoS class. Set them
  from observed usage, not from your laptop.
- **Limits** are enforced by the container runtime (cgroups v2 in current clusters).
  Exceeding a memory limit means an immediate `OOMKilled`; exceeding a CPU limit means
  throttling, not killing.
- CPU is `millicores` (`100m` is a tenth of a core). Memory is bytes with binary
  suffixes: `Mi`, `Gi`.

| QoS class | Condition | Behaviour under pressure |
|---|---|---|
| Guaranteed | Every container sets equal requests and limits for both CPU and memory | Last to be evicted |
| Burstable | At least one container has a request, but not all meet Guaranteed | Middle |
| BestEffort | No requests or limits | First to be evicted |

Omitting requests entirely puts you in BestEffort, which is the first thing evicted
during node pressure and blocks reliable HPA CPU-based scaling.

Get the facts rather than guessing:

```bash
kubectl -n prod top pods
kubectl -n prod describe node <node>        # Allocated resources section
kubectl get node -o custom-columns='NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory'
```

## Rolling update strategy

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 25%          # or an integer
    maxUnavailable: 0      # or a percentage
```

- `maxUnavailable: 0` plus `maxSurge: 1` means never drop below the desired count
  while adding one pod at a time. Safest, but requires headroom for one extra pod.
- `Recreate` stops everything before starting, for workloads that cannot run two
  versions concurrently against shared state.
- Required settings during a risky rollout: `minReadySeconds` (hold a pod "ready"
  long enough to catch fast crashes) and `progressDeadlineSeconds` (mark the rollout
  failed instead of hanging forever).

```bash
kubectl -n prod rollout history deployment/api
kubectl -n prod rollout undo deployment/api
kubectl -n prod rollout undo deployment/api --to-revision=3
kubectl -n prod rollout restart deployment/api
kubectl -n prod rollout status deployment/api --timeout=5m
kubectl -n prod patch deployment api -p '{"spec":{"paused":true}}'
```

## Jobs and CronJobs

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  namespace: prod
spec:
  backoffLimit: 3
  activeDeadlineSeconds: 1800
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never        # required for Jobs
      containers:
        - name: migrate
          image: ghcr.io/acme/api:1.4.2
          command: ["./node_modules/.bin/knex", "migrate:latest"]
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { memory: 512Mi }
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: nightly-report
  namespace: prod
spec:
  schedule: "0 3 * * *"            # five fields: minute hour dom month dow
  timeZone: "Etc/UTC"
  concurrencyPolicy: Forbid       # Allow | Forbid | Replace
  startingDeadlineSeconds: 600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  suspend: false
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: report
              image: ghcr.io/acme/reporter:2.0.0
```

Notes: Jobs require `restartPolicy: Never` or `OnFailure`. A `Forbid` policy prevents
overlapping runs but skips the run entirely if one is in flight — pick deliberately
for anything that writes billing or notification data. Cron schedules are UTC unless
`timeZone` is set; `?` is accepted by some controllers as "no specific value".

## Horizontal scaling

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api
  namespace: prod
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 3
  maxReplicas: 30
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: AverageValue
          averageValue: 256Mi
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
        - type: Percent
          value: 100
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
```

- CPU `Utilization` is a percentage **of the container's CPU request**, so unset
  requests make the number meaningless. `AverageValue` against the limit or an
  absolute value works without a request.
- Custom metrics come from the Kubernetes API aggregation layer (`metrics.k8s.io` for
  Prometheus-style metrics, `external.metrics.k8s.io` for cloud provider metrics).
- Scale down slowly and scale up quickly. The `behavior` block encodes this directly.
- HPA adjusts `spec.replicas`, so it conflicts with anything else writing that field.

## ConfigMaps and Secrets

```bash
# Create imperatively
kubectl -n prod create configmap api-config --from-literal=LOG_LEVEL=info
kubectl -n prod create secret generic api-db --from-literal=url='postgres://...'
```

Three ways to consume them:

```yaml
# 1. Whole object as environment variables
envFrom:
  - configMapRef: { name: api-config }
  - secretRef:    { name: api-shared }

# 2. Single keys
env:
  - name: LOG_LEVEL
    valueFrom:
      configMapKeyRef: { name: api-config, key: LOG_LEVEL, optional: true }

# 3. Mounted as files (best for large values and for rotation)
volumes:
  - name: config
    configMap:
      name: api-config
containers:
  - name: api
    volumeMounts:
      - { name: config, mountPath: /etc/acme, readOnly: true }
```

Reality check: a `Secret` value is base64-encoded, not encrypted, in etcd. Anyone
with `get secrets` in the namespace can read it, and it is visible in `kubectl
describe` output. Use a managed secret store and the external-secrets or secrets-store
integration, or mount from a CSI driver, when the threat model warrants it.

Environment variables do not update in a running pod. Changing a ConfigMap updates
the mounted files (eventually) but requires a pod restart to change the environment.

## Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api
  namespace: prod
  annotations:
    # Annotation keys and meanings are controller-specific. Check your
    # controller's documentation; these are common NGINX examples.
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
spec:
  ingressClassName: nginx
  tls:
    - hosts: ["api.acme.example"]
      secretName: api-tls
  rules:
    - host: api.acme.example
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api
                port:
                  name: http
          - path: /admin
            pathType: Prefix
            backend:
              service:
                name: admin
                port:
                  number: 8080
```

Important: `ingressClassName` is the portable field. Annotations, TLS termination
behavior, and default backends are all defined by whichever ingress controller is
installed, so verify the keys against your controller's own documentation.

## Namespaces and RBAC

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: prod
  labels:
    pod-security.kubernetes.io/enforce: restricted
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: api
  namespace: prod
automountServiceAccountToken: false
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: api-config-reader
  namespace: prod
rules:
  - apiGroups: [""]
    resources: ["configmaps", "secrets"]
    resourceNames: ["api-config"]
    verbs: ["get", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: api-config-reader
  namespace: prod
subjects:
  - kind: ServiceAccount
    name: api
    namespace: prod
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: api-config-reader
```

```bash
kubectl auth can-i --list --as=system:serviceaccount:prod:api
kubectl auth can-i get secrets -n prod --as=system:serviceaccount:prod:api
```

`resourceNames` plus `get` is the pattern that avoids a wildcard grant. ClusterRole
plus ClusterRoleBinding is cluster-wide; use it only for genuinely cluster-scoped
objects. `automountServiceAccountToken: false` should be the default for anything
that does not call the API server.

## Triage: a complete command ladder

```bash
kubectl config current-context          # ALWAYS check this first
kubectl config use-context <ctx>
kubectl -n prod get pods -o wide
kubectl -n prod get pods -l app=api \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,REASON:.status.containerStatuses[0].state.waiting.reason'
kubectl -n prod describe pod <pod>      # events at the bottom are the answer
kubectl -n prod logs <pod> --all-containers --tail=200
kubectl -n prod logs <pod> --previous   # the crashed instance's log
kubectl -n prod logs -l app=api --tail=100 --prefix --max-log-requests=10
kubectl -n prod get events --sort-by=.lastTimestamp | tail -30
kubectl -n prod describe deployment api
kubectl -n prod rollout status deployment/api
kubectl -n prod exec -it <pod> -- sh
kubectl -n prod port-forward svc/api 8080:80
kubectl -n prod top pods
kubectl -n prod get endpointslices -l kubernetes.io/service-name=api
kubectl -n prod auth can-i --list
```

For an ephemeral debug container in a pod (no shell or tooling in the image):

```bash
kubectl -n prod debug -it pod/<pod> --image=nicolaka/netshoot --target=api
```

Node-level problems:

```bash
kubectl -n prod get pods -o wide                     # note the NODE column
kubectl describe node <node>                         # pressure conditions
kubectl get events -A --field-selector reason=FailedScheduling
kubectl get pods -A --field-selector spec.nodeName=<node>
```

## Node placement essentials

```yaml
spec:
  nodeSelector:
    kubernetes.io/os: linux
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: topology.kubernetes.io/zone
                values: ["us-east-1a"]
  tolerations:
    - key: dedicated
      operator: Equal
      value: batch
      effect: NoSchedule
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: topology.kubernetes.io/zone
      whenUnsatisfiable: ScheduleAnyway
      labelSelector:
        matchLabels:
          app.kubernetes.io/name: api
```

Topology spread constraints are how you get "evenly across zones" without pinning to
one zone. `ScheduleAnyway` is a soft preference that the scheduler will break only
when it has no choice.

## Checklist

- Every container sets `requests` for CPU and memory based on measurement.
- `limits.memory` is set; `limits.cpu` is set where throttling would hurt.
- Liveness checks only the process; readiness checks what serving requires.
- A `startupProbe` exists instead of a large `initialDelaySeconds`.
- `maxUnavailable: 0` plus headroom, or an explicit reason not to.
- `minReadySeconds` and `progressDeadlineSeconds` set on anything risky.
- Labels follow `app.kubernetes.io/*` so tooling can find them.
- `runAsNonRoot`, `allowPrivilegeEscalation: false`, capabilities dropped.
- Pod Security Admission namespace labels set, or equivalent admission policy.
- A dedicated ServiceAccount with the minimum verbs, token automount disabled.
- Ingress uses `ingressClassName`; controller-specific annotations verified.
- Alarms on pod restart rate and on HPA saturation (hitting `maxReplicas`).
- `kubectl apply --dry-run=server` run in CI.

## Common mistakes

**Symptom: `CrashLoopBackOff` with no logs.**
Cause: the container writes to stdout but the crash happens before the log pipeline is
attached, or the app logs only to a file. Fix: `kubectl logs <pod> --previous`,
then `kubectl describe pod` and look for `Last State: Terminated, Reason: OOMKilled`
or a nonzero exit code with the exit signal in the events.

**Symptom: `OOMKilled`.**
Cause: memory limit below peak working set. Fix: read `resources.limits.memory` in
the pod spec, raise it, and fix the leak. There is no retry for OOMKill.

**Symptom: `ImagePullBackOff`.**
Cause: wrong tag, private registry without `imagePullSecrets`, or missing pull
policy. Fix: verify the digest exists, add
`imagePullSecrets: [{name: regcred}]` in the pod spec or the ServiceAccount.

**Symptom: pod `Pending` forever.**
Cause: unschedulable. Fix: `kubectl describe pod` and read the `FailedScheduling`
event. It almost always says insufficient CPU, memory, or too many pods.

**Symptom: rollout hangs at "waiting for deployment to be available".**
Cause: new pods never become ready. Fix: `kubectl get pods -l <label>`,
`kubectl describe pod`, check the readiness probe path and port. A probe pointing at
the wrong `targetPort` name is a classic.

**Symptom: traffic hits a pod that is still starting.**
Cause: no readiness probe, so the pod joins the Service endpoints the instant its
container starts. Fix: add a readiness probe that fails until the app can serve.

**Symptom: whole deployment restarts during a database outage.**
Cause: liveness probe checks the database. Fix: make liveness local only, move the
dependency check to readiness.

**Symptom: `pods` in `Terminating` for hours.**
Cause: a finalizer, a long pre-stop hook, or a node that is gone. Fix:
`kubectl get pod <pod> -o jsonpath='{.metadata.finalizers}'`, check node health with
`kubectl describe node`.

**Symptom: HPA never scales.**
Cause: no metrics server installed, or CPU requests unset. Fix:
`kubectl top pods` must work; then set requests.

**Symptom: `error: the object has been modified` on apply.**
Cause: concurrent writers, often a controller or a `kubectl edit` in another window.
Fix: retry with `--server-side` and a field manager, or re-apply and review the diff.

## Anti-patterns

- `hostNetwork: true`, `hostPID: true`, or `privileged: true` as a debugging shortcut.
  These break isolation and will not pass a restricted Pod Security profile.
- `latest` image tags in manifests. The same manifest can produce different rollouts
  at different times.
- No `resources` block at all, guaranteeing BestEffort QoS.
- Liveness and readiness pointing at the same heavyweight dependency-aware endpoint.
- `NodePort` in production for external access, bypassing the cloud load balancer and
  its TLS, WAF, and health checking.
- Cluster-admin bound to every service account in every namespace.
- `spec.replicas` edited by hand while an HPA owns the same field.
- ConfigMaps holding anything sensitive, on the assumption that the name confers
  protection.
- Probes with no `timeoutSeconds`, so a hung app ties up a kubelet worker.
- Jobs with `restartPolicy: Always` (invalid) or with `backoffLimit` high enough to
  hammer a broken external system for hours.
- Testing deployments only with `kubectl apply` and no `--dry-run=server` gate in CI.

## References

- Workload resources: https://kubernetes.io/docs/concepts/workloads/
- Deployments and rollout strategy: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
- Probes: https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/
- Configure probes: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
- Resource management and QoS classes: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
- Services: https://kubernetes.io/docs/concepts/services-networking/service/
- Ingress: https://kubernetes.io/docs/concepts/services-networking/ingress/
- ConfigMaps and Secrets: https://kubernetes.io/docs/concepts/configuration/configmap/
- Jobs and CronJobs: https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/
- Horizontal Pod Autoscaling: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
- RBAC: https://kubernetes.io/docs/concepts/security/rbac-good-practices/
- Debug running pods: https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/
- kubectl cheat sheet: https://kubernetes.io/docs/reference/kubectl/quick-reference/
- Pod Security Standards: https://kubernetes.io/docs/concepts/security/pod-security-standards/

Verify before relying on this: fields and stable fields such as
`progressDeadlineSeconds` and probe semantics are long-established, but verify any
newer key with `kubectl explain deployment.spec`, `kubectl explain pod.spec`, or the
API reference for the server version you actually run.