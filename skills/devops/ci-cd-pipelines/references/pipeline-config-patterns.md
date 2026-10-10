# Pipeline Configuration Patterns

Vendor-specific syntax changes; the intent behind each block does not. Adapt the
shape, verify every key against your CI system's own documentation.

## GitLab CI (`.gitlab-ci.yml`)

```yaml
stages:
  - verify
  - build
  - test
  - deploy

default:
  image: node:22-bookworm-slim
  interruptible: true

variables:
  NODE_ENV: test
  # Never cache these
  GIT_DEPTH: "20"

.node_cache: &node_cache
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - .npm/
    policy: pull-push

verify:
  stage: verify
  script:
    - npm ci
    - npm run lint
    - npm run typecheck
  artifacts:
    when: always
    expire_in: 1 week
    paths:
      - reports/

build:
  stage: build
  <<: *node_cache
  script:
    - npm ci
    - npm run build
  artifacts:
    expire_in: 1 day
    paths:
      - dist/
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      when: manual
      allow_failure: true

deploy:staging:
  stage: deploy
  environment:
    name: staging
    url: https://staging.acme.example
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  script:
    - ./scripts/deploy.sh staging "$CI_COMMIT_SHA"

deploy:production:
  stage: deploy
  environment:
    name: production
  resource_group: production      # serializes deployments to one slot
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      when: manual
  script:
    - ./scripts/deploy.sh production "$CI_COMMIT_SHA"
  when: manual
```

`resource_group` is how you prevent two concurrent production deploys. `rules` is
how you make a job conditional without scattering `only`/`except` logic.

## Cache and key design

```
key = <toolchain version> + <os/arch> + <lockfile hash> + <build-mode>
restore-keys = <toolchain version> + <os/arch>          (prefix fallback)
```

| Language | Cache target | Lockfile |
|---|---|---|
| Node.js | `~/.npm` or pnpm store | `package-lock.json`, `pnpm-lock.yaml` |
| Python | `~/.cache/pip`, `~/.cache/uv`, virtualenv | `requirements.txt`, `uv.lock`, `poetry.lock` |
| Go | `$(go env GOMODCACHE)`, `$(go env GOCACHE)` | `go.sum` |
| Rust | `~/.cargo/registry`, `target/` | `Cargo.lock` |
| Java | `~/.m2/repository` | `pom.xml` / `gradle.lockfile` |
| Docker | BuildKit registry or GitHub Actions cache | image digests |

Prefer BuildKit cache mounts inside the build (`RUN --mount=type=cache`) for
language package caches: they are faster than CI-level cache restore because the
files stay on the builder's local disk.

## Multi-container integration test job

```yaml
  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-bookworm
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
      redis:
        image: redis:7-bookworm
        ports: ["6379:6379"]
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgres://postgres:postgres@localhost:5432/test
      REDIS_URL: redis://localhost:6379
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm run test:integration
```

`--health-cmd` in the service `options` is what makes the job wait for the database
to accept connections instead of failing on a race.

## Canary gate, conceptually

A reusable rollout contract that most platforms can express:

```bash
#!/usr/bin/env bash
set -euo pipefail

DEPLOY_REF="$1"           # the candidate build, by digest
PERCENT="${2:-5}"
DURATION="${3:-600}"

./deploy --ref "$DEPLOY_REF"

for ((i = 0; i < DURATION; i += 60)); do
  sleep 60
  error_rate=$(./metrics query 'sum(rate(http_requests_total{status=~"5.."}[5m]))'
                        / 'sum(rate(http_requests_total[5m]))')
  p99=$(./metrics query 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))')

  if awk "BEGIN{exit !($error_rate > $BASELINE_ERROR_RATE * 2 || $p99 > $BASELINE_P99 * 2)}"; then
    ./deploy --ref "$PREVIOUS_REF"
    echo "rollout aborted: error_rate=$error_rate p99=$p99" >&2
    exit 1
  fi
done

./deploy --ref "$DEPLOY_REF" --promote
```

The two decisions that matter are the threshold (relative to a baseline, not a magic
number) and the observation window (long enough that the request volume inside it is
statistically meaningful at the canary percentage).

## Reproducibility checklist

```bash
# 1. Toolchain pinned
cat .nvmrc .tool-versions 2>/dev/null

# 2. Lockfile honored, no silent updates
npm ci
pip install --require-hashes -r requirements.txt
cargo build --locked
go mod verify

# 3. No environment leakage into the build
env | grep -iE 'key|token|secret|password' || true

# 4. Same source, same digest
docker buildx build -t app:a -t app:b --provenance=false .
docker inspect -f '{{index .RepoDigests 0}}' app:a app:b

# 5. Layer reproducibility in CI
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
```

`git log -1 --pretty=%ct` pinned into `SOURCE_DATE_EPOCH` removes the single largest
source of build timestamp noise for many toolchains.

## Flake detection sketch

```bash
#!/usr/bin/env bash
set -euo pipefail
TEST_ID="$1"

if npx vitest run -- "$TEST_ID"; then
  if [ -f "/tmp/previous-failures" ] && grep -qxF "$TEST_ID" /tmp/previous-failures; then
    echo "::warning::FLAKY::$TEST_ID passed on retry after a previous failure"
  fi
  exit 0
fi

echo "$TEST_ID" >> /tmp/previous-failures
exit 1
```

Wire the marker into a metric keyed by test name. A test with more than a small
number of flake events in a window is a ticket, not a warning you scroll past.

## Pipeline cost levers

| Lever | Effect |
|---|---|
| Fail fast on lint before tests | Removes the wait for everyone on obvious errors |
| Split unit tests into a sharded matrix | Parallelism; requires test partitioning |
| Cache package downloads, not build outputs | Smaller, more reusable cache entries |
| Avoid `--no-cache` outside of cache-poisoning investigations | Restores the point of caching |
| Self-hosted runners for the stable, slow jobs | Cost only; add capacity planning and patching |
| Container image caching in the registry | Reuses layers across branches |
| Skip heavy jobs for fork PRs | Cost and secret hygiene |
| `interruptible` / `cancel-in-progress` | Stops superseded work on the same ref |