---
name: Docker Containers
description: Covers Docker fundamentals, Dockerfile best practices, multi-stage builds, layer caching, docker compose for local development, bind mounts versus named volumes, signal handling, image size reduction, and container debugging. Use when the user asks about Dockerfiles, container images, docker compose files, image bloat or slow builds, containers that will not shut down, "container exits immediately", permission errors inside containers, or how to debug a running container.
metadata:
  opencode/autoinvoke: true
---

# Docker Containers

An image is an immutable, layered filesystem plus metadata. A container is a single
running process with its own writable layer, namespaces, and cgroup limits on top of
that image. Almost every Docker problem you will debug falls into one of three
buckets: build caching, PID 1 behavior, or filesystem permissions.

## When to use this

- Writing or reviewing a `Dockerfile`.
- Builds are slow, or a one-line code change rebuilds everything.
- `docker compose up` fails, hangs, or services start in the wrong order.
- A container exits immediately with code 137, 143, or 1 and nobody knows why.
- An image is several gigabytes and deploys are painful.
- You need to debug a running container without restarting it.
- Deciding between bind mounts, named volumes, and tmpfs.

## Quick start

A production-shaped Node.js Dockerfile. Tags below are examples: resolve and pin a
concrete, current tag for your base images in the registry, and prefer digest pins for
anything deployed to production.

```dockerfile
# syntax=docker/dockerfile:1

# ---- build stage -------------------------------------------------------------
FROM node:22-bookworm-slim AS build
WORKDIR /app

# Dependencies first so the install layer is cached until the lockfile changes.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev

COPY . .
RUN npm run build && npm prune --omit=dev

# ---- runtime stage -----------------------------------------------------------
FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app

# Run as a numeric uid/gid that also exists on the host so bind-mounted files
# keep working during local development.
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=build --chown=app:app /app/node_modules ./node_modules
COPY --from=build --chown=app:app /app/dist ./dist
COPY --from=build --chown=app:app /app/package.json ./

USER 10001:10001
EXPOSE 8080

# Every child process of PID 1 is forwarded the stop signal.
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/server.js"]
```

Run it:

```bash
docker build -t acme/api:local --target runtime .
docker run --rm -p 8080:8080 --name acme-api acme/api:local
docker logs -f acme-api
```

## Dockerfile instructions that matter

| Instruction | Why it matters |
|---|---|
| `FROM` | Every `FROM` starts a fresh cache chain. Pin a digest for reproducibility. |
| `RUN` | Creates a layer. Combining commands with `&&` reduces layer count and guarantees ordering. |
| `COPY` | Preferred over `ADD` unless you genuinely want archive extraction or URL fetching. |
| `WORKDIR` | Creates the directory if missing and makes it the base for later relative paths. |
| `USER` | Switches for the rest of the build and for `CMD`. Numeric UID avoids name-resolution gaps at runtime. |
| `ARG` / `ENV` | `ARG` is build-time only and not present in the final image; `ENV` persists into the runtime environment. |
| `ENTRYPOINT` / `CMD` | `ENTRYPOINT` is the fixed executable; `CMD` supplies default arguments that can be overridden. |
| `STOPSIGNAL` | Changes the signal `docker stop` sends. Defaults to `SIGTERM`. |
| `HEALTHCHECK` | Records status, does **not** restart anything by itself. The restart policy does. |

### Shell form versus exec form

```dockerfile
CMD node dist/server.js        # runs via /bin/sh -c: node becomes a CHILD of the shell
CMD ["node", "dist/server.js"] # exec form: node IS the process, PID 1
```

Always prefer the exec form for anything that receives signals, runs in a
supervisor, or is inspected by tooling that expects a specific process name.

## Layer caching rules

Docker caches each instruction. A layer is reused only if the instruction **and every
layer below it** are unchanged. Consequences:

- Copy dependency manifests first, install, then copy source. Never `COPY . .`
  before the install step.
- Install into a single directory and clean up in the same `RUN`. A `RUN rm -rf
  /var/lib/apt/lists/*` on its own line does not shrink the image, because the
  deleted files still exist in the earlier layer.
- Build arguments used earlier in the file bust every subsequent layer. Put
  frequently changing arguments late.
- `.git`, `node_modules`, build outputs, and local secrets should never enter the
  build context.

```bash
# Compare layer sizes before and after a change
docker history --no-trunc myapp:latest

# Break the cache for one step when you truly need it
docker build --no-cache -t myapp:debug .
```

## .dockerignore

Without this, the whole directory is uploaded to the daemon on every build.

```
.git
.gitignore
node_modules
dist
coverage
.env
.env.*
!.env.example
*.log
Dockerfile*
docker-compose*.yml
.vscode
.idea
README.md
```

Check what actually enters the context with `docker build --no-cache .` plus
`docker history`, or review the context size printed at the start of a build.

## Multi-stage builds

Multi-stage builds keep compilers, package managers, and dev dependencies out of the
runtime image without manual cleanup.

```dockerfile
# ---- Go ----
FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/server"]
```

- `--mount=type=cache` keeps the package cache across builds without baking it into a
  layer. Requires BuildKit, which is the default builder in current Docker Engine.
- `--mount=type=secret` injects files that are needed during build but must not be
  persisted in the layer:

```dockerfile
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc npm ci
```

- Distroless and Alpine-style images are small, but they change failure modes:
  distroless has no shell (`docker exec sh` fails, so debug with
  `kubectl debug`-style ephemeral containers or copy a static busybox binary), and
  Alpine uses musl rather than glibc, which breaks prebuilt native modules and is
  usually slower to resolve DNS under concurrency.

## Signals and PID 1

`docker stop` sends `SIGTERM`, waits the stop timeout (10 seconds by default), then
sends `SIGKILL`. PID 1 in a Linux container has no default signal handlers, so an
app written as `CMD npm start` will typically be killed after the grace period,
dropping in-flight requests.

Fixes, in order of preference:

```dockerfile
ENTRYPOINT ["node", "dist/server.js"]        # 1. exec form, app handles SIGTERM
```

```bash
docker run --init acme/api:local            # 2. let Docker inject tini
```

```dockerfile
RUN apk add --no-cache tini
ENTRYPOINT ["/sbin/tini", "--", "node", "dist/server.js"]
```

```bash
docker run --stop-timeout 45 --name acme-api acme/api:local
```

In the app itself, handle the signal, stop accepting new work, finish or abort
in-flight work, then exit zero. Exit code `143` means terminated by `SIGTERM`; `137`
means killed (usually the OOM killer, but it is also what you see when the grace
period expires).

## Image size reduction, in order of payoff

1. Multi-stage build so the toolchain never ships.
2. A smaller base family: `slim` or `distroless` instead of full Debian; Alpine only
   if your dependencies tolerate musl.
3. One layer with a clean install rather than many install-then-delete layers.
4. Do not install documentation, shells, or package managers you do not use.
5. `.dockerignore` a large context and add it via `COPY` selectively.

Measure, do not guess:

```bash
docker images --format '{{.Repository}}\t{{.Tag}}\t{{.Size}}'
docker history myapp:latest
docker run --rm myapp:latest sh -c 'cat /etc/os-release'   # know your base
```

## Bind mounts versus named volumes versus tmpfs

| Type | Syntax | Use for | Failure mode |
|---|---|---|---|
| Named volume | `-v pgdata:/var/lib/postgresql/data` | Databases, queues, caches, anything that must survive container replacement | Silent staleness if you expect host file edits |
| Bind mount | `--mount type=bind,src=./src,dst=/app/src` | Local development source sync | Host file ownership and UID mismatch, slow on Windows/Docker Desktop |
| tmpfs | `--tmpfs /tmp:rw,size=64m` | Scratch space, in-memory caches | Lost on exit, RAM-backed |

Use the long `--mount` syntax rather than `-v`: it fails loudly instead of silently
creating a directory when the source path is wrong, and it accepts read-only flags
explicitly.

```bash
docker run --rm \
  --mount type=bind,source="$(pwd)/src",target=/app/src,readonly \
  --mount type=volume,source=cache,target=/app/.cache \
  --tmpfs /tmp:rw,size=64m \
  acme/api:local
```

On Windows hosts and macOS, bind mounts cross a filesystem boundary. File watching
often does not work without polling, and Linux UID/GID translation means the numeric
`USER` you pick in the Dockerfile may not match the host user.

## docker compose for local development

```yaml
# docker-compose.yml
name: acme

services:
  db:
    image: postgres:16-bookworm
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 10s

  api:
    build:
      context: .
      target: runtime
    environment:
      DATABASE_URL: postgres://app:app@db:5432/app
    ports:
      - "8080:8080"
    depends_on:
      db:
        condition: service_healthy
    command: ["node", "dist/server.js"]

volumes:
  db-data:
```

Notes that save real time:

- The top-level `version:` key is obsolete in Compose v2 and produces a warning.
  Delete it.
- `depends_on` with `condition: service_healthy` is the supported way to order
  startup. `condition: service_started` only waits for the container to exist, which
  is almost never what you want for a database.
- Compose resolves service names on the default network; `http://db:5432` works
  without any host mapping.
- `docker compose config` renders and validates the merged file, including
  interpolation. Run it in CI.

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f api
docker compose exec api sh
docker compose run --rm --no-deps api npm test
docker compose down -v        # stop AND delete named volumes
docker compose up --wait      # block until healthy; confirm with --help on your version
```

## Resource limits and hardening

```bash
docker run --rm \
  --cpus 1.5 \
  --memory 512m --memory-swap 512m \
  --pids-limit 200 \
  --read-only --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 10001:10001 \
  acme/api:local
```

`--memory-swap` equal to `--memory` disables swap, which makes OOM kills predictable
instead of letting the container swap indefinitely. `--read-only` plus explicit
tmpfs mounts catches surprise writes at the cost of some debugging friction.

## Debugging a running container

```bash
docker ps -a --filter 'exited=1'                     # recently dead containers
docker logs --tail 200 --timestamps acme-api
docker inspect acme-api --format '{{json .State}}'    # exit code, error, OOMKilled
docker inspect acme-api --format '{{json .State.Health}}'
docker stats --no-stream
docker top acme-api                                   # processes inside the PID namespace
docker events --filter container=acme-api             # lifecycle + health transitions
docker diff acme-api                                 # what the process wrote
docker exec -it acme-api sh
docker exec -u root -it acme-api sh                   # escalate after dropping USER
docker cp acme-api:/var/log/app.log ./app.log
docker update --memory 1g acme-api                    # live resource change
```

Inside a distroless or `--read-only` container, `exec sh` fails with "executable file
not found in $PATH". Use a sidecar or `docker run --rm --pid container:acme-api
--cap-add SYS_PTRACE busybox sh` to get a shell in the same namespaces.

`docker run --rm --pid container:<name> --network container:<name> ...` is the
general trick: it attaches to another container's PID and network namespaces so you
can strace it or inspect its sockets without restarting anything.

## Checklist

- Build args and lockfiles copied before source; `npm ci` / `pip install -r
  requirements.txt` / `go mod download` in its own cached layer.
- `.dockerignore` exists and excludes `.git`, `.env`, and dependency directories.
- Multi-stage build; runtime image has no compiler and no dev dependencies.
- Exec-form `ENTRYPOINT`/`CMD`; the app handles `SIGTERM` and drains connections.
- Non-root `USER` with a numeric UID; writes only to paths the image declares.
- `HEALTHCHECK` hits a real endpoint that reflects readiness, not liveness of a
  dependency.
- `STOPSIGNAL` set if the default is wrong for your process.
- Resource limits set on every long-running container.
- `docker history` reviewed; no secrets in any layer (they persist in history even
  if deleted later).
- `docker compose config` passes in CI.

## Common mistakes

**Symptom: build takes minutes even though nothing changed.**
Cause: `COPY . .` before the dependency install, so any file change busts the
install layer. Fix: manifest-first ordering plus a `.dockerignore`.

**Symptom: `RUN rm -rf /var/lib/apt/lists/*` did not shrink the image.**
Cause: deletion happened in a new layer; the bytes still exist in the layer below.
Fix: install and clean in one `RUN`, or use a multi-stage build.

**Symptom: image is small in the registry but huge on disk, or vice versa.**
Cause: confusing compressed transfer size with unpacked size, or comparing across
architectures. Fix: inspect `docker history`, then compare the same image digest per
platform with `docker buildx imagetools inspect`.

**Symptom: container exits with code 0 immediately.**
Cause: `CMD` was overridden or the entrypoint argument list is empty. Fix:
`docker inspect --format '{{json .Config.Cmd}} {{json .Config.Entrypoint}}' <c>`.

**Symptom: `permission denied` writing to `/app`.**
Cause: `USER` is non-root but the copied files are owned by root, or a bind-mounted
host directory has different ownership. Fix: `COPY --chown=10001:10001`, or
pre-chmod the host directory; on Docker Desktop set file sharing to include the path.

**Symptom: graceful shutdown takes the full 10 seconds and gets SIGKILL.**
Cause: PID 1 is a shell or an app that ignores SIGTERM. Fix: exec form, handle the
signal in the app, or `--init`.

**Symptom: killed with exit code 137 under load.**
Cause: OOM killer, or the stop grace period expired. Fix: read
`.State.OOMKilled` from `docker inspect`; if true, raise `--memory` or fix the leak.

**Symptom: `compose up` leaves services `restarting`.**
Cause: dependency not ready and no healthcheck gate. Fix: add a `healthcheck` to
the dependency and use `condition: service_healthy`.

**Symptom: source changes on the host do not appear in the container.**
Cause: a named volume is shadowing the bind mount, or the app was started without a
bind mount at all. Fix: `docker inspect <c>` and check `.Mounts`.

## Anti-patterns

- Committing secrets as build args or `ENV`. They land in image history and are
  readable by anyone who can pull the image. Inject at runtime.
- `latest` as the deployed tag. It is mutable, so you cannot tell what is running.
  Use the git SHA or a semver tag plus a digest.
- Running as root because the build needs root. Split the build and the runtime.
- `docker system prune -af --volumes` on a machine holding real state. It removes
  stopped containers, unused images, networks, and volumes, including database data.
- One huge base image with every tool preinstalled "just in case". It bloats the
  attack surface and the pull time.
- Baking a database dump or uploaded file into the image.
- Mounting the Docker socket into an application container for convenience.
- Relying on `HEALTHCHECK` to restart a broken process. It only reports status;
  the restart policy or an orchestrator acts on it.
- `latest`-pinned base images with no automated update path, so builds break months
  later when upstream tags move.

## References

- Dockerfile reference: https://docs.docker.com/reference/dockerfile/
- BuildKit cache and secret mounts: https://docs.docker.com/build/cache/optimize/
- Compose file reference: https://docs.docker.com/reference/compose-file/
- Compose Specification: https://compose-spec.github.io/compose-spec/
- `docker run` flag reference: https://docs.docker.com/reference/cli/docker/container/run/
- `docker build` flag reference: https://docs.docker.com/reference/cli/docker/buildx/build/
- Image size reduction: https://docs.docker.com/build/building/best-practices/
- HEALTHCHECK semantics: https://docs.docker.com/reference/dockerfile/#healthcheck
- Distroless images: https://github.com/GoogleContainerTools/distroless

Verify before relying on this: Docker Engine and Compose evolve quickly. Check
`docker --version`, `docker compose version`, and the flag list at
`docker <subcommand> --help` before assuming a flag exists in your environment.