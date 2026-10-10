# Compose and Storage Reference

## Compose file skeleton

```yaml
name: acme-stack

services:
  web:
    image: ghcr.io/acme/web@sha256:0000000000000000000000000000000000000000000000000000000000000000
    # Or build locally:
    # build:
    #   context: ./web
    #   target: runtime
    #   args:
    #     BUILD_MODE: dev
    command: ["node", "dist/server.js"]
    entrypoint: ["/usr/local/bin/tini", "--"]
    user: "10001:10001"
    init: true
    working_dir: /app
    env_file:
      - .env
    environment:
      NODE_ENV: production
      DATABASE_URL: postgres://app:${APP_DB_PASSWORD}@db:5432/app
    ports:
      - "127.0.0.1:8080:8080"     # published on the loopback only
      - "8443:8443"               # published on all interfaces
    expose:
      - "9000"                    # documented, not published
    volumes:
      - web-data:/var/lib/web
      - type: bind
        source: ./config
        target: /etc/acme
        read_only: true
    tmpfs:
      - /tmp:size=32m
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/healthz"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 20s
    restart: unless-stopped
    stop_grace_period: 30s
    deploy:
      resources:
        limits:
          cpus: "1.5"
          memory: 512M
    networks:
      - edge
      - internal
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  db:
    image: postgres:16-bookworm
    environment:
      POSTGRES_PASSWORD: ${APP_DB_PASSWORD}
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      retries: 10
    networks:
      - internal

  # One-shot container, gated on the database being healthy
  migrate:
    image: ghcr.io/acme/web:1.4.2
    command: ["./node_modules/.bin/knex", "migrate:latest"]
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

volumes:
  db-data:
  web-data:

networks:
  edge:
  internal:
    internal: true
```

### Keys worth knowing

| Key | Purpose |
|---|---|
| `name` | Project name; namespaces containers, volumes, and networks |
| `services.<id>.depends_on.condition` | `service_started`, `service_healthy`, `service_completed_successfully` |
| `services.<id>.restart` | `no`, `always`, `on-failure`, `unless-stopped` |
| `services.<id>.healthcheck.test` | Array form (`CMD`, `CMD-SHELL`) or `NONE` to disable an image healthcheck |
| `services.<id>.deploy.resources.limits` | Applied by Compose for `cpus` and `memory` in v2; replicas in the `deploy` block are ignored outside Swarm |
| `services.<id>.profiles` | Starts the service only when a named profile is enabled |
| `services.<id>.develop` | Compose watch-mode overrides for live reload during development |
| `networks.<n>.internal` | No external connectivity for containers on that network |

Top-level `version:` is obsolete and only emits a warning. `x-` prefixed top-level
keys are extension fields you can use for YAML anchors.

### Interpolation and environment

```bash
# .env in the project directory is loaded automatically for interpolation
DB_PASSWORD=hunter2

docker compose config          # render and validate the final file
docker compose config --services
docker compose --profile debug up -d
```

Use `--env-file path` to point at a different file, and remember that any variable
referenced with `${NAME}` will silently become an empty string if it is undefined,
which produces confusing runtime failures. Validate with `docker compose config`.

## Storage decision guide

| Question | Answer |
|---|---|
| Must data outlive the container? | Named volume |
| Do I edit the files from the host during development? | Bind mount |
| Is it a database, queue, or object store? | Named volume, never bind mount |
| Is it build output or cache? | tmpfs or a disposable named volume |
| Is it source code being hot-reloaded? | Bind mount plus a node_modules named volume |

The classic pattern that stops bind mounts from hiding installed dependencies:

```yaml
services:
  web:
    image: node:22-bookworm
    working_dir: /app
    command: ["npm", "run", "dev"]
    volumes:
      - ./:/app
      - node-modules:/app/node_modules
volumes:
  node-modules:
```

## Volume inspection and cleanup

```bash
docker volume ls
docker volume inspect db-data
docker volume prune            # only unused volumes; check with ls first
docker compose down -v         # removes volumes declared in this compose file
docker compose down --rmi local
```

Deleting a volume is not recoverable from Docker. Back it up first.

## Useful day-to-day commands

```bash
docker compose up -d --build            # rebuild and recreate changed services
docker compose up -d --scale worker=4   # scale a stateless service
docker compose up --abort-on-container-exit --exit-code-from migrate
docker compose logs --tail=200 -f web
docker compose exec -u root web sh
docker compose run --rm --no-deps web npm test
docker compose restart web
docker compose stop                      # keep containers, do not recreate
docker compose pull                      # refresh images from the registry
docker compose images
docker compose ps --format json
docker compose top web
```

## Platform pinning

```bash
docker compose up --build               # native architecture
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose up --build
```

For genuine multi-architecture images, use a Buildx builder rather than emulating:

```bash
docker buildx create --use --name multi
docker buildx inspect --bootstrap
docker buildx build --platform linux/amd64,linux/arm64 -t acme/api:1.4.2 --push .
```

Emulated builds are substantially slower and can produce binaries linked against the
emulated userland. Buildx cross-compiles using the build platform's toolchain.