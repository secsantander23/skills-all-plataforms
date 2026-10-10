---
name: CI/CD Pipelines
description: Covers continuous integration and delivery pipeline design including stage layout, caching and dependency management, matrix builds, secrets in CI, artifact versus image promotion, blue/green, canary and rolling deployments, pull request checks, flaky test handling, build reproducibility, and software supply chain basics. Use when the user asks about writing or reviewing CI workflows, slowing down or speeding up builds, pipeline caching, pipeline credentials, deployment strategies, release automation, flaky tests, or supply chain security.
metadata:
  opencode/autoinvoke: true
---

# CI/CD Pipelines

A pipeline is a supply chain, not a build script. Its job is to turn a commit into a
thing you are willing to run in production, with evidence at every step: what was
built, from what source, with which dependencies, tested how, and approved by whom.

Design for three properties in order: **correctness** (fail closed), **speed**
(fail fast, cache aggressively), **reproducibility** (same inputs, same artifact).

## When to use this

- Writing or reviewing a CI configuration file.
- Builds take too long, or a cache never seems to hit.
- You need to decide what a pipeline should run on a pull request versus on main.
- Storing credentials in CI and worrying about who can read them.
- Choosing how a build moves from staging to production.
- A test fails intermittently and nobody trusts the pipeline.
- Setting up provenance, signing, or dependency update automation.

## Quick start

A GitHub Actions pipeline. Syntax shown is Actions; the stage model, caching keys,
secret handling, and promotion patterns transfer to any CI system. Pin action
versions to a release tag or, better, a commit SHA per your organization's policy —
verify the current major version in the marketplace before pinning.

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  NODE_VERSION: "22"

jobs:
  verify:
    name: Lint, types, unit tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
          cache-dependency-path: package-lock.json

      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm test -- --coverage
        env:
          CI: "true"

  build:
    name: Build image
    needs: [verify]
    runs-on: ubuntu-latest
    timeout-minutes: 30
    outputs:
      image-digest: ${{ steps.meta.outputs.digest }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        id: build
        with:
          context: .
          push: true
          tags: ghcr.io/acme/api:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true

      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/acme/api
          tags: |
            type=sha,format=long
            type=ref,event=branch

      - id: digest
        run: echo "digest=${{ steps.build.outputs.digest }}" >> "$GITHUB_OUTPUT"

  e2e:
    name: End-to-end against the built image
    needs: [build]
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.ci.yml up -d --wait
      - run: npm run e2e
      - if: always()
        run: docker compose -f docker-compose.ci.yml logs
      - if: always()
        run: docker compose -f docker-compose.ci.yml down -v
```

## Stage model

| Stage | Gate | Typical contents |
|---|---|---|
| Static checks | Every push and PR | Format, lint, types, license header |
| Unit tests | Every push and PR | Fast, parallel, no external services |
| Build | Once per commit | Compile, produce one immutable artifact |
| Integration tests | On merge to main | Real database, real broker |
| Security checks | On merge to main | Dependency audit, image scan, IaC policy |
| Deploy to dev | Automatic | Migrate, smoke test |
| Deploy to staging | Automatic | Full E2E suite |
| Deploy to prod | Approval + canary | Progressive rollout, automatic rollback |

Two rules keep pipelines trustworthy:

1. **Fail closed.** A skipped gate must not be silently green. If a test job is
   allowed to continue on error, make the skip loud and alert on it.
2. **One artifact, promoted forward.** Rebuilding at each stage means the thing you
   tested is not the thing you shipped. See promotion below.

## Caching and dependencies

Caching is the highest-leverage build speedup, and the easiest place to create a
correctness bug. The rules:

- The cache key must include every input that changes the output. Lockfile hash,
  language version, operating system image, architecture.
- Never cache a directory that is itself a build output you also archive.
- Cache miss must be fast and correct. "Cold" must always work.

```yaml
- uses: actions/cache@v4
  with:
    path: |
      ~/.cache/pip
      ~/.cache/uv
      .venv
    key: py-${{ runner.os }}-${{ hashFiles('uv.lock') }}
    restore-keys: |
      py-${{ runner.os }}-
```

With `actions/setup-node` and `actions/setup-python`, the built-in `cache:` option
keys on the lockfile for you. For language-level caches, prefer the ecosystem tool:
`npm ci` (not `npm install`), `pip install -r requirements.txt` with a fully pinned
file or a hash-locked `uv.lock` / `poetry.lock`, `go mod download` with a committed
`go.sum`, `cargo build --locked`, `bundle install --deployment`.

`npm ci` versus `npm install` is not a style preference: `npm ci` installs exactly
the lockfile and fails if `package.json` and the lockfile disagree.

## Matrix builds

```yaml
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      max-parallel: 4
      matrix:
        os: [ubuntu-latest, macos-latest]
        node: ["20", "22"]
        include:
          - os: windows-latest
            node: "22"
            experimental: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
          cache: npm
      - run: npm ci
      - run: npm test
```

- `fail-fast: false` tells you every broken combination instead of only the first.
  Turn it on when you want the fastest signal, off when you need the full picture.
- One matrix leg should always reproduce locally. Publish artifacts per leg so a
  failure can be inspected without rerunning the job.
- Do not enumerate the cross-product blindly; add platform-specific legs through
  `include` so the matrix stays readable.

## Concurrency and cost controls

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

Cancel superseded pull request builds, but never cancel a main-branch build: it
means something already reached production. Add `timeout-minutes` to every job — a
hung job costs a runner for hours.

## Secrets in CI

Rules, in priority order:

1. **Federate, do not store.** Use OpenID Connect so the CI platform exchanges a
   short-lived token for cloud credentials at run time. Most major CI systems and
   clouds support this; see your provider's OIDC documentation.
2. **Scope narrowly.** Separate credentials per environment, read-only where
   possible, with an explicit expiry.
3. **Never in the repository.** Not in YAML, not in Dockerfiles, not in build args.
4. **Mask in logs** where the platform supports it, and treat any secret echoed to a
   log as compromised — rotate it.
5. **Pull request code runs untrusted.** Never expose secrets to workflows triggered
   by `pull_request` from forks. Gate on `github.event.pull_request.head.repo.full_name
   == github.repository`, or run forks in a restricted context.

```yaml
permissions:
  contents: read
  packages: write
  id-token: write      # required for OIDC federation
```

Set `permissions` explicitly. The default permission set varies by repository
settings, and relying on the default is how read-only pipelines end up able to
delete releases.

## Artifacts versus image promotion

| | Artifact promotion | Rebuild per environment |
|---|---|---|
| Fidelity | Exactly what was tested runs in prod | Prod runs different bits |
| Speed | Fast, no rebuild | Slow |
| Debuggability | Hard: "works in staging" explains nothing | Easy: reproduce and rebuild |
| Reproducibility | Requires immutable, tagged artifacts | Requires a hermetic build |

Prefer promotion. The mechanics:

```bash
# Tag the tested image with an immutable environment tag, never rebuild
docker tag ghcr.io/acme/api:a1b2c3d ghcr.io/acme/api:staging
docker push ghcr.io/acme/api:staging

# Deploy by digest in production
kubectl -n prod set image deployment/api api=ghcr.io/acme/api@sha256:<digest>
```

Upload test reports, coverage, and build logs as pipeline artifacts for audit, but
never upload the production artifact from an untrusted context.

## Deployment strategies

| Strategy | Rollback speed | Cost | Use when |
|---|---|---|---|
| Rolling | Minutes (redeploy previous tag) | Low | Default for stateless services |
| Blue/green | Seconds (flip traffic) | Double capacity during cutover | Big-bang changes, hard rollback needs |
| Canary | Minutes | Small | Want production traffic metrics before full release |
| Recreate | Long | Low | Stateful workloads that cannot run two versions |

Blue/green and canary both need traffic control that plain Kubernetes Services do
not provide. In a cluster you implement them with an ingress controller that splits
by weight, or with two Deployments and a Service selector switch. In managed
platforms you use the platform's deployment options (traffic shifting, weighted
aliases, gradual deployments).

A canary without an automatic rollback condition is just a slow rolling update.
Define the abort criteria up front: error rate, latency percentiles, and any
business metric, measured against the baseline rather than a fixed threshold.

## Pull request checks

- Run the fast gate on PRs: lint, types, unit tests, a build smoke test.
- Run the expensive gate on merge to main, not on every PR.
- Require the check in branch protection, and require it to be up to date with the
  branch head before merging.
- Post coverage deltas, not just a percentage, so reviewers see direction.
- Give auto-review bots the authority to block, but keep them advisory on design.

## Flaky tests

A flaky test is a defect in the test, not noise to ignore.

```bash
# Find the suspects
npm test -- --onlyFailures        # re-run last failures, framework-dependent
pytest --lf --lfnf=none -q        # last-failed, no cache reset
go test ./... -count=1 -run TestX
```

Policy that works:

1. Detect: rerun a failed test once in isolation and record a marker when it passes
   differently. Keep flake occurrences as a metric per test name.
2. Quarantine: move the test to a separate suite with a tracked owner and an expiry
   date. A quarantine without an owner and a date is permanent disablement.
3. Fix or delete. A test that has not caught a real bug in six months and is
   unstable is costing more than it returns.
4. Seed data in tests must be deterministic. Use a fixed clock, fixed random seeds,
   and isolated identifiers.

Never solve flakiness by adding blanket retries in the pipeline. Retries convert a
red build into a green one at the cost of hiding the signal and doubling runtime on
the worst cases.

## Reproducibility

- Pin toolchain versions in a committed file (`.nvmrc`, `.python-version`,
  `.tool-versions`, `go.mod`'s `go` directive, `rust-toolchain.toml`).
- Commit lockfiles and install with lockfile-enforcing flags.
- Use `--frozen-lockfile` (npm), `--locked` (cargo), or the ecosystem equivalent so
  builds fail rather than silently updating.
- Strip timestamps and paths from build outputs where the toolchain allows it, and
  avoid embedding build hostnames.
- Build once, tag with the commit SHA and the content digest.
- Record the toolchain image digest, not just its tag, for the builder image.

## Supply chain basics

- **Lock dependencies to immutable references.** Commit SHAs for actions; pin
  container base images by digest; lock language dependencies.
- **Scan what you ship.** Dependency audit plus image vulnerability scanning on the
  artifact you actually push, not on a pre-build snapshot.
- **Minimal permissions and least-privilege tokens** for every step.
- **Provenance and signing.** Generate build provenance and sign artifacts and images,
  then verify the signature at deploy time rather than trusting a tag.
- **Protect branches** and require review for changes to pipeline definitions
  themselves — the pipeline is production code.
- **Automate dependency updates** with a tool that opens reviewed PRs, and let CI
  prove the update.
- **Restrict what can reach production.** Deployment credentials live only in the
  environment that deploys, and are reachable only from protected workflows.

## Checklist

- Every job has `timeout-minutes`.
- PRs run a fast gate; main runs the full gate.
- Dependencies install from lockfiles with enforcement flags.
- Cache keys include OS, architecture, toolchain version, and lockfile hash.
- `permissions:` set explicitly on every job; OIDC used instead of static cloud keys.
- No secret is available to fork-triggered workflows.
- One artifact is built per commit and promoted; production deploys by digest.
- Deployment strategy has an explicit automatic rollback condition.
- Canary abort criteria are documented and measured against a baseline.
- Flaky tests are detected, quarantined with an owner and an expiry date.
- Images and actions are pinned; vulnerability scanning gates promotion.
- Artifacts and logs are retained long enough to investigate an incident.

## Common mistakes

**Symptom: cache restores but the build is still slow.**
Cause: the cache key includes something that changes every run, such as a timestamp
or a run ID. Fix: derive keys only from inputs that affect the output.

**Symptom: "npm ci can only install with an existing package-lock.json".**
Cause: lockfile not committed, or the checkout step used a shallow/filtered clone.
Fix: commit `package-lock.json`.

**Symptom: tests pass in CI, fail locally, or the reverse.**
Cause: CI sets `CI=true`, which changes test framework behavior, or the local machine
has uncommitted lockfile changes. Fix: set the environment explicitly and make the
lockfile the single source of truth.

**Symptom: a leaked secret appears in build logs.**
Cause: `set -x`, a verbose flag, or a curl command with a token in the URL. Fix:
rotate the credential immediately; deleting the log line does not un-leak it. Use
header-based auth and mask the value.

**Symptom: fork PRs cannot access the registry and the build fails.**
Cause: secrets are correctly withheld from fork PRs. Fix: run a build-only job for
forks that does not need credentials.

**Symptom: staging passes, production fails with a missing dependency.**
Cause: a rebuild happened at deploy time and resolved different versions. Fix:
promote the tested artifact by digest.

**Symptom: a flaky test blocks every release.**
Cause: quarantine with no owner or expiry. Fix: assign an owner and a deletion date
and fail the quarantine job if the date passes.

**Symptom: a canary looked fine but error rates rose after full rollout.**
Cause: the canary received too little traffic to be statistically meaningful, or
metrics were measured without a comparable baseline. Fix: size the canary window
against your actual request volume and compare against the same period.

**Symptom: the pipeline is green but the deploy never happened.**
Cause: a job was allowed to continue on error, or an environment protection rule
silently blocked. Fix: fail closed and alert on skipped gates.

## Anti-patterns

- Long-lived cloud credentials stored as CI secrets. Use OIDC federation.
- `pull_request_target` with a checkout of untrusted PR code. It runs fork code with
  repository secrets and is a well-known compromise path.
- Deploying `latest`, or rebuilding from a branch name inside the deploy job.
- Retrying every failing test to get a green checkmark.
- Running the full E2E suite on every PR when it adds forty minutes and blocks
  everyone for a one-line change.
- Caching a directory that is also an artifact, so stale output is uploaded.
- Matrix explosion without `max-parallel` and without cost awareness.
- A single long-lived "cd" environment where staging and production share one set of
  credentials.
- Treating a security scan that reports thousands of findings and blocks nothing as
  a control. Scans without a triage policy get disabled within a month.
- Secrets passed as Docker build arguments. They persist in image history.
- Pinning to a mutable major tag with no automated update process for the pin.
- No retention policy on artifacts, so an investigation cannot inspect the build.

## References

- GitHub Actions documentation: https://docs.github.com/actions
- Workflow syntax: https://docs.github.com/actions/using/workflow-syntax-for-github-actions
- Secure use reference: https://docs.github.com/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
- Caching: https://docs.github.com/actions/using/caching-dependencies-to-speed-up-workflows
- OpenID Connect in cloud providers: https://docs.github.com/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect
- SLSA build levels: https://slsa.dev/spec/v1.0/about
- Sigstore cosign: https://docs.sigstore.dev/cosign/
- Google SRE book, chapter on reliable product launches: https://sre.google/sre-book/reliable-product-launches/
- Google SRE workbook, canary releases chapter: https://sre.google/workbook/canary-releases/
- Kubernetes Deployment mechanics a rollout relies on: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/

Verify before relying on this: action versions and platform features move quickly.
Check the action's marketplace page and the CI platform's own changelog before
pinning, and confirm flags with the tool's `--help`.