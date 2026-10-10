# Container Debugging Playbook

Ordered triage. Work top to bottom; each step is cheap and rules out a class of
failures before the next one.

## 1. Did it start at all?

```bash
docker ps -a --filter 'name=<name>'
docker inspect <name> --format '{{json .State}}'
```

Look at `Status`, `Running`, `ExitCode`, `OOMKilled`, `Error`, `StartedAt`,
`FinishedAt`. Note `Error` is about daemon-level failures (bad mount, unknown
network), not application failures.

```bash
docker logs --timestamps --tail 300 <name>
docker logs --since 10m <name>
```

`--timestamps` is what you want for anything involving ordering or timeouts.

## 2. Configuration and process identity

```bash
docker inspect <name> --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}}'
docker inspect <name> --format '{{json .Config.Env}}'
docker inspect <name> --format '{{json .Config.User}}'
docker inspect <name> --format '{{json .Mounts}}'
docker inspect <name> --format '{{json .NetworkSettings.Networks}}'
```

`docker top <name>` shows what is actually running inside the PID namespace. If you
expected one process and see a shell plus a child, you are in shell form.

## 3. Health and restarts

```bash
docker inspect <name> --format '{{json .State.Health}}'
docker inspect <name> --format '{{.RestartCount}}'
```

For a healthcheck that never goes healthy, run the exact `test` command by hand
inside the container. The most common failures are the health endpoint binding to
`127.0.0.1` while the check uses the container IP, or a `start-period` shorter than
the application boot time.

Note that HEALTHCHECK status is informational in plain Docker. Nothing restarts an
unhealthy container unless a restart policy or an external supervisor does.

## 4. Get inside without disturbing the app

```bash
# Normal case
docker exec -it <name> sh

# Dropped to non-root, need root
docker exec -u root -it <name> sh

# No shell in the image
docker run --rm -it \
  --pid container:<name> \
  --network container:<name> \
  --cap-add SYS_PTRACE \
  busybox sh
```

`--pid container:<name>` shares the PID namespace, so `ps` inside the helper shows the
app process. `--network container:<name>` shares the network namespace, so you can hit
the app on localhost and inspect its sockets.

For read-only filesystems and distroless images this is the standard escape hatch.
Be aware it has the same privileges as the target container.

## 5. Look at what the process is doing

```bash
docker stats --no-stream
docker top <name>
```

Inside the container (requires a shell):

```bash
cat /proc/1/cmdline | tr '\0' ' '; echo
ls -l /proc/1/fd | head -50
ls /proc/1/fd | wc -l                 # fd count vs ulimit -n
cat /proc/1/status | grep -E 'Threads|VmRSS'
ss -tulpn                           # or: netstat -tulpn
```

## 6. What did the process write?

```bash
docker diff <name>                    # added/changed/deleted paths in the writable layer
docker cp <name>:/var/log/app.log ./app.log
```

`docker diff` often immediately explains a "read-only filesystem" error: the
application wrote somewhere unexpected and you can now see where.

## 7. Reproduce outside Docker

Run the same command with the same environment locally. A failure that reproduces
outside Docker is an application problem; one that does not is an image, mount, or
networking problem.

## Symptom to cause table

| Symptom | Likely cause | Next command |
|---|---|---|
| Exit code 0, immediate | Entrypoint/cmd empty or overridden | `docker inspect` on `.Config.Cmd` |
| Exit code 1 with stack trace | Application-level failure | `docker logs` |
| Exit code 137, `OOMKilled: true` | Memory limit hit | `docker stats`, `docker inspect` memory setting |
| Exit code 137, `OOMKilled: false` | SIGKILL after stop grace period | check signal handling, add `--init` |
| Exit code 143 | Received SIGTERM | expected on `docker stop`; app did not exit in time |
| Exit code 126 / 127 | Not executable, or not found (often a missing loader) | `docker run --entrypoint sh <img> -c 'ldd /app/server'` |
| Hangs, no logs | Waiting on DNS, network, or a lock | `ss -tulpn` inside, `docker inspect` network |
| `permission denied` | UID mismatch or read-only path | `docker inspect` `.Mounts`, `docker diff` |
| Healthy locally, not on a server | Bind mount or architecture difference | compare `docker image inspect` platform |
| Restarting forever | Crash loop on startup | `docker logs --tail`; temporarily disable the restart policy to read the full error |

## Network triage

```bash
docker inspect <name> --format '{{json .NetworkSettings.Networks}}'
docker network ls
docker network inspect <network>
```

- Containers can only reach the internet if they are on a network that has a route
  to the outside. A user-defined bridge network has NAT; the default `bridge` network
  has a different, more limited setup.
- DNS between containers works by container name only on user-defined networks.
- Check the app is not binding to `127.0.0.1` when it needs to be reachable from
  outside the container namespace.

## Log and event timeline

```bash
docker events --since 30m --filter container=<name> \
  --filter type=container
```

Events show create, start, health_status, die, and kill in order, which is the
fastest way to distinguish "the app crashed" from "Docker killed the app".