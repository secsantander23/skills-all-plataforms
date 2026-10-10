# Kubernetes Troubleshooting Decision Guide

Start with the widest question, then narrow. Each block is an ordered set of
commands that answers one question.

## Which context am I pointed at?

```bash
kubectl config current-context
kubectl config get-contexts
kubectl config use-context <ctx>
echo "$KUBECONFIG"
```

Every other command in this document is meaningless until this is right. Most
production incidents in this category were caused by a kubectl pointed at the wrong
cluster.

## What state is every pod in?

```bash
kubectl -n <ns> get pods -o wide
kubectl -n <ns> get pods \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,PHASE:.status.phase,\
RESTARTS:.status.containerStatuses[0].restartCount,\
WAITING:.status.containerStatuses[0].state.waiting.reason,\
LAST_EXIT:.status.containerStatuses[0].lastState.terminated.exitCode,\
REASON:.status.containerStatuses[0].lastState.terminated.reason'
```

That one-liner resolves most triage cases. Read it as a table.

## Phase and reason decoder

| Phase / reason | Meaning | Go to |
|---|---|---|
| `Pending` | No node accepted the pod | Scheduling |
| `ContainerCreating` | Image pull or volume mount in progress | Images and volumes |
| `CrashLoopBackOff` | Container started and exited repeatedly | Logs and last state |
| `ImagePullBackOff` / `ErrImagePull` | Registry fetch failed | Images and volumes |
| `OOMKilled` | Memory limit exceeded | Resources |
| `Terminating` | Deletion in progress | Finalizers |
| `CreateContainerConfigError` | Bad env, missing key, bad secret ref | Config and secrets |
| `Evicted` | Node pressure or preemption | Node health |
| Running but `0/1 READY` | Container up, readiness failing | Probes |

## Scheduling

```bash
kubectl -n <ns> describe pod <pod> | sed -n '/Events/,$p'
kubectl get events -A --field-selector reason=FailedScheduling --sort-by=.lastTimestamp
kubectl describe node <node>
```

Read the `FailedScheduling` message. It states the concrete cause: insufficient
CPU, insufficient memory, too many pods, node selectors unsatisfiable, taints not
tolerated, or a required node affinity with no matching node.

```bash
kubectl get nodes -L topology.kubernetes.io/zone
kubectl get pods -A --field-selector spec.nodeName=<node> -o wide
kubectl get node <node> -o jsonpath='{.status.conditions}' | jq
```

Conditions to check on a node: `MemoryPressure`, `DiskPressure`, `PIDPressure`,
`NetworkUnavailable`, and the `Ready` condition.

## Logs

```bash
kubectl -n <ns> logs <pod>                              # default container
kubectl -n <ns> logs <pod> -c <container>
kubectl -n <ns> logs <pod> --all-containers
kubectl -n <ns> logs <pod> --previous                   # the previous, crashed container
kubectl -n <ns> logs <pod> --since=15m --timestamps
kubectl -n <ns> logs -l app.kubernetes.io/name=api --prefix --tail=50
kubectl -n <ns> logs -l app.kubernetes.io/name=api --since=1h | tail -100
kubectl -n <ns> logs -f <pod>
```

`--previous` is the answer to "the pod looks fine now". It shows the log of the
instance that just died.

If a sidecar writes most of the volume, the main container's log can be drowned out.
`--all-containers=true` plus `--prefix` separates them.

## Probe failures

```bash
kubectl -n <ns> describe pod <pod> | grep -A3 -i probe
kubectl -n <ns> get events --field-selector reason=Unhealthy
```

Events look like `Readiness probe failed: HTTP probe failed with statuscode: 503`.
Translate: which endpoint returned 503 and what does your readiness handler check?
Common causes: the port name does not match the container port name, the app binds to
loopback only, the health endpoint itself depends on a database, and the probe
timeout is shorter than a legitimate slow check.

Test the probe endpoint from inside the pod network:

```bash
kubectl -n <ns> run probecheck --rm -it --image=nicolaka/netshoot --restart=Never -- \
  curl -sv http://api.<ns>.svc.cluster.local/healthz
```

## Images and volumes

```bash
kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].image}{"\n"}'
kubectl -n <ns> get pod <pod> -o jsonpath='{.status.containerStatuses[*].imageID}{"\n"}'
kubectl -n <ns> describe pod <pod> | grep -A5 -i 'events'
```

- `imageID` shows the digest actually running, which proves whether the tag moved.
- For private registries, confirm `imagePullSecrets` exists on the pod spec or the
  ServiceAccount, and that the secret is in the same namespace as the pod.
- `FailedMount` events mean the PVC is not bound or the CSI driver is unavailable:
  `kubectl -n <ns> get pvc` and `kubectl get storageclass`.

## Endpoints and traffic

```bash
kubectl -n <ns> get endpointslices -l kubernetes.io/service-name=api
kubectl -n <ns> describe svc api
kubectl -n <ns> port-forward svc/api 8080:80
```

An empty EndpointSlice with healthy pods means the selector does not match the pod
labels. That is the single most common "Service has no endpoints" cause, and it is
almost always a label typo between the Service and the Deployment template.

## Cluster-scoped view

```bash
kubectl get nodes -o wide
kubectl -n kube-system get pods
kubectl -n kube-system describe pod -l component=kube-apiserver
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100
kubectl get --raw /readyz?verbose
```

## Emergency maneuvers

```bash
# Force removal of a pod stuck in Terminating (last resort)
kubectl -n <ns> delete pod <pod> --grace-period=0 --force

# Take a pod out of rotation without deleting it, for a bad instance
kubectl -n <ns> label pod <pod> app.kubernetes.io/name=api- --overwrite
kubectl -n <ns> label pod <pod> app.kubernetes.io/name- --overwrite

# Safe node maintenance
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data

# See what a node is running
kubectl get pods -A --field-selector spec.nodeName=<node> -o wide
```

## Things worth checking before declaring an outage

1. Did the client change, or the server? Reproduce from a neutral network path.
2. Is it one pod, one node, one zone, one version, or everything? That single
   question usually finds the layer.
3. Are the metrics wrong rather than the service? Alert fatigue on a broken
   telemetry pipeline looks exactly like an outage.
4. Did a recent deploy, config change, certificate expiry, or secret rotation land in
   the window? `kubectl rollout history`, ConfigMap timestamps, and ingress
   certificate dates.
5. Is it a capacity problem? Compare requests to `Allocatable` on `kubectl describe
   node`, and check HPA saturation.