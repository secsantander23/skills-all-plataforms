---
name: Integration Testing
description: Covers integration test scope and boundaries, real databases and fixtures, containerized dependencies with Testcontainers, transactional rollback for tests, consumer-driven contract testing with Pact, test data builders, dealing with slow and flaky integration suites, and environment parity. Use when the user asks how to test across process boundaries, when setting up a database or broker in tests, when tests pollute a shared database, when integration tests are flaky or slow, or when testing service-to-service contracts.
metadata:
  opencode/autoinvoke: true
---

# Integration Testing

Integration tests exercise your code together with the real collaborators it depends on: the database, the queue, the cache, the third-party client. The core mental model is *boundary placement*. Every network boundary you cross buys realism and pays in speed, flakiness, and diagnosability. Put the test at the lowest boundary that can actually catch the bug.

## When to use this

- Verifying that SQL, ORM mappings, transactions, and constraints actually work together.
- Testing a code path that only exists once two real services exchange messages.
- Reproducing a bug that a unit test with a mocked repository cannot see.
- Verifying that a schema migration leaves existing data readable.
- Before an E2E test, when E2E would be the only way to prove the same thing and much slower.

## Boundary placement

| Bug class | Lowest layer that catches it |
| --- | --- |
| Business rule is wrong | Unit test |
| Query returns the wrong rows, join drops data, index unused | Integration test with a real database |
| Constraint, trigger, or foreign key is missing | Integration test with a real database |
| Producer emits a payload the consumer cannot parse | Contract test |
| Consumer rejects a payload the producer legitimately sends | Contract test |
| The deployed wiring is wrong, or the UI cannot reach the API | E2E test |

Write integration tests only for bugs you have actually had, or for boundaries where the failure would be expensive. "We test everything against the database" produces a suite nobody runs.

## Quick start

Python with Testcontainers and pytest:

```python
# tests/integration/test_orders_api.py
import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgreSQLContainer

from orders.app import create_app


@pytest.fixture(scope="session")
def postgres():
    with PostgreSQLContainer("postgres:16-alpine") as pg:
        yield f"postgresql+psycopg://{pg.getusername()}:{pg.getpassword()}@{pg.get_container_host_ip()}:{pg.get_exposed_port(pg.POSTGRES_PORT)}/{pg.get_database_name()}"


@pytest.fixture
def client(postgres, migrate):
    app = create_app(database_url=postgres)
    with TestClient(app) as c:
        yield c
    clean_tables(app)   # truncate between tests


def test_order_total_is_persisted(client):
    # Arrange
    payload = {"sku": "ABC-1", "quantity": 2, "unit_price_cents": 1500}

    # Act
    response = client.post("/orders", json=payload)

    # Assert
    assert response.status_code == 201
    assert response.json()["total_cents"] == 3000
```

JavaScript with Node's built-in test runner and `pg`:

```js
// test/integration/orders.test.js
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { Pool } from 'pg';
import { createServer } from '../../src/server.js';

let pool;
let server;
let baseUrl;

before(async () => {
  pool = new Pool({ connectionString: process.env.TEST_DATABASE_URL });
  await migrate(pool);
  server = createServer({ pool });
  await new Promise((resolve) => server.listen(0, resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  server.close();
  await pool.end();
});

test('POST /orders stores the computed total', async () => {
  const response = await fetch(`${baseUrl}/orders`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sku: 'ABC-1', quantity: 2, unit_price_cents: 1500 }),
  });

  assert.equal(response.status, 201);
  const body = await response.json();
  assert.equal(body.total_cents, 3000);

  const { rows } = await pool.query('select total_cents from orders where id = $1', [body.id]);
  assert.equal(rows[0].total_cents, 3000);
});
```

Check your framework's official docs for the exact container and server bootstrap APIs; these ecosystems move faster than the principles.

## Containerized dependencies

Running against a real database installed on the host or a shared staging instance produces version drift and cross-test pollution. Start throwaway dependencies in containers.

| Language | Library | Example container |
| --- | --- | --- |
| Python | `testcontainers` | `PostgreSQLContainer`, `RedisContainer`, `KafkaContainer` |
| Java / JVM | `org.testcontainers:testcontainers` | `PostgreSQLContainer`, `GenericContainer` |
| .NET | `Testcontainers.Net` | `PostgreSqlBuilder` |
| JavaScript / TypeScript | `testcontainers` (Node) | `new PostgreSqlContainer('postgres:16-alpine')` |
| Go | `testcontainers-go` | `postgres.RunContainer(...)` |

Principles that hold across all of them:

- Use a pinned image tag, never `latest`. The suite must not change behavior when someone republishes a tag.
- Start containers once per session (module, class, or suite scope), not per test. Startup dominates runtime.
- Expose a random host port and read the mapped port from the container object; do not assume the same port on every machine.
- Wait for readiness explicitly. A listening socket is not the same as a ready database.
- Tear down deterministically, including on failure, so a crashed run does not leave orphan containers.

```java
// JUnit 5 + Testcontainers, singleton container pattern
@Testcontainers
class OrderRepositoryIT {

    @Container
    private static final PostgreSQLContainer<?> POSTGRES =
        new PostgreSQLContainer<>("postgres:16-alpine");

    @Test
    void finds_orders_by_customer() {
        // repository uses POSTGRES.getJdbcUrl() via an injected DataSource
        assertThat(repository.findByCustomer("cus_1")).hasSize(2);
    }
}
```

If you cannot use containers (locked-down CI, Windows without a Docker daemon, air-gapped builds), fall back to:

- An embedded engine where one exists and is production-equivalent (H2 in PostgreSQL compatibility mode is *not* equivalent; SQLite is a different engine).
- A managed ephemeral database provided by the CI platform.
- A single dedicated schema per test run on a shared server, truncated at the start.

State clearly in the test file which of these is in play. Silent engine substitution is a common source of "works locally, fails in CI".

## Test databases and schema

Decide explicitly how the schema reaches the test database. Three options, in increasing order of fidelity:

1. **ORM metadata create-all.** Fast, but does not exercise migrations, and drifts from production DDL.
2. **Migrate from scratch on every run.** Catches broken migrations. Slow if migrations are many.
3. **Migrate a snapshot database.** Start from a restored dump of a recent production-shaped dataset, then run pending migrations. Highest fidelity, and it also catches queries that break on real data shapes and volumes.

Use a migration tool that supports downgrades if your process depends on them. In Python, Alembic is the common choice; in Ruby on Rails, `db:migrate`; in .NET, EF Core migrations. Verify exact commands with the tool's docs.

Never point integration tests at a database that a human also uses. A test run that truncates your development data is a data-loss incident.

## Transactional rollback

The cheapest way to isolate database tests is to run each test inside a transaction and roll it back.

- Spring: annotate the test class with `@Transactional` from `org.springframework.test.annotation.Transactional`; the framework rolls back by default and does not commit.
- Django: use `django.test.TestCase` (transaction-wrapped) rather than `TransactionTestCase`, which truncates instead.
- Raw SQLAlchemy or JDBC: open a connection in the test, begin a transaction, run the test, and `ROLLBACK` in a `finally` block.

```python
from contextlib import contextmanager


@contextmanager
def rolled_back_session(engine):
    """Run a test inside a transaction that is always rolled back."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
```

Limitations you must design around:

- **The code under test must join your transaction.** If the service opens its own connection or a different session, the rollback does not cover it.
- **Background work escapes.** Jobs queued by the test may run after rollback and fail confusingly, or write data you thought was cleaned. Disable workers in tests, or assert on the queue instead of the consumer.
- **Committing inside the code under test breaks it.** If the system under test calls `commit()` explicitly, the rollback is a no-op and data leaks between tests.
- **Multiple resource managers need care.** A rollback on the database but not on an in-process cache leaves the cache dirty. Clear caches in the fixture.

When any of these apply, stop using rollback and truncate tables explicitly in setup instead. Truncation is slower but honest.

## Test data builders for integration tests

Integration data must satisfy real constraints: foreign keys, unique indexes, `NOT NULL`, check constraints. Builders let you build a valid parent quickly and vary the field under test.

```python
@dataclass
class UserFactory:
    seq: int

    def create(self, **overrides) -> dict:
        self.seq += 1
        row = {
            "email": f"user{self.seq}@example.com",
            "country": "PT",
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        row.update(overrides)
        return row
```

Notes:

- Generate unique values from a per-suite counter so uniqueness indexes never collide.
- Pin timestamps to a fixed value so assertions are stable.
- Create the parent, then the child, in the test. Do not hide referential setup in a deep helper.
- For large volumes, use `generate_series` (PostgreSQL) or an equivalent bulk insert rather than row-by-row inserts.

```sql
-- PostgreSQL: fast seed of a large, valid table
INSERT INTO orders (id, customer_id, status, total_cents, created_at)
SELECT
    'ord_' || g,
    'cus_' || (g % 1000 + 1),
    'PAID',
    g * 100,
    now()
FROM generate_series(1, 100000) AS g;
```

## Contract testing between services

When two services talk over HTTP or a message queue, integration tests need both running and are slow to write. Consumer-driven contracts test the seam without running both.

With Pact:

1. The consumer's test calls the real client against a mock provider and records the interactions.
2. Those interactions become a pact file, committed with the consumer.
3. The provider's CI runs a pact verification test against a real instance of the provider.
4. Both sides get a failure if either changes the contract unilaterally.

```javascript
// consumer test - pacts are generated here
const { PactV3 } = require('@pact-foundation/pact');

const provider = new PactV3({
  consumer: 'checkout-service',
  provider: 'pricing-service',
  dir: path.resolve(__dirname, 'pacts'),
});

provider
  .given('a product with price 1500 cents')
  .uponReceiving('a request for the total')
  .withRequest({ method: 'GET', path: '/prices/ABC-1' })
  .willRespondWith({
    status: 200,
    headers: { 'content-type': 'application/json' },
    body: { sku: 'ABC-1', unitPriceCents: 1500, currency: 'EUR' },
  });

test('fetches the unit price', async () => {
  await provider.executeTest(async (mockService) => {
    const client = createPricingClient(mockService.url);
    const price = await client.getUnitPrice('ABC-1');
    expect(price.unitPriceCents).toBe(1500);
  });
});
```

See `references/contract-testing.md` for provider verification, CI wiring, and pact lifecycle management.

Practical guidance:

- Version the pact as a build artifact between repos, or publish it to a pact broker if you already run one.
- Versioned endpoints mean additive changes do not need a new pact round.
- A contract test does not replace an integration test against a staging stack. It covers the seam, not the deployment.
- Verify the current Pact library API and broker setup with the project's docs; the DSL has changed across major versions.

## Managing slow and flaky suites

Integration suites fail for different reasons than unit tests. Triage by cause, not by retry.

| Cause | Signal | Fix |
| --- | --- | --- |
| Not waiting for readiness | First test in a suite fails, rest pass | Readiness gate with an explicit probe, not a fixed sleep |
| Shared database pollution | Fails only when run with other tests | Per-test isolation via rollback or truncate |
| Real network to a third party | Fails behind VPN or under load | Point at a local fake or a recorded-response proxy |
| Port conflicts | `EADDRINUSE` on a specific machine | Bind port 0 and read the assigned port |
| Ordering dependence | Passes alone, fails in suite | Remove the order dependence, do not reorder |
| Clock or timezone drift | Fails near midnight in a timezone | Pin the clock and timezone in the fixture |
| Container image drift | Fails on a day with no code change | Pin image digests, not just tags |
| Resource exhaustion | Random timeouts on the CI runner | Bound parallelism, add explicit timeouts, raise limits |

Rules that keep the suite trustworthy:

- Set explicit timeouts on every network call in tests, and fail with a clear message.
- Run integration tests in a separate job from unit tests, so a slow suite cannot block fast feedback.
- Allow a small number of retries only for *known* infrastructure flakiness, and track the retry count as a metric. A rising retry count is a defect.
- Never wrap an assertion in a retry. Retry the setup or the request, then assert once.
- Seed with deterministic data, and make the suite runnable on a laptop in one command.

## Environment parity

A test that passes locally and fails in CI has a parity gap. Enumerate them explicitly:

- **Dependencies.** Match major and, for critical ones, minor versions of the database, broker, and runtime. Pin the container image by tag and, where allowed, by digest.
- **Configuration.** Load `.env.test` in CI from the same source the developer loads. Do not keep a divergent list in the CI provider's UI.
- **OS and filesystem.** Line endings, path separators, case sensitivity of the filesystem, and file locking differ between Linux and Windows. Tests that touch real files must normalize or be skipped on the wrong platform.
- **Clock.** Set the container and CI runner to UTC. Assert in UTC.
- **Concurrency.** CPU count and available memory change how parallel code behaves. Do not write assertions that depend on a specific worker count.
- **Networking.** CI runners often cannot reach the public internet. Anything external must be stubbed or replaced.

Run the integration suite on the CI image, not just locally, before you trust it. A useful exercise: run the suite in a container identical to the CI image and see what breaks.

## Checklist

Integration tests are done when:

- [ ] Every external dependency is a container or a local fake with a pinned version.
- [ ] No test touches a database, namespace, or bucket that a human uses.
- [ ] State is isolated per test by rollback, truncate, or unique keys.
- [ ] Readiness is probed explicitly; no fixed sleeps.
- [ ] Timeouts are set on every network call.
- [ ] Service seams are covered by contract tests, not by starting both services.
- [ ] The suite runs with one command locally and in CI.
- [ ] Image tags are pinned and configuration comes from one source.
- [ ] Known flaky tests are quarantined and tracked, not silently retried.

## Common mistakes

- **Sharing one database across the whole suite.** Symptom: a test passes alone, fails after another test, and the failure points at unrelated behavior. Fix: per-test isolation.
- **Truncating with `DELETE FROM` and a foreign key graph.** Symptom: constraint violations, or a suite that gets slower as foreign keys grow. Fix: `TRUNCATE ... RESTART IDENTITY CASCADE` (PostgreSQL) or delete in reverse dependency order.
- **Assuming a listening port means a ready database.** Symptom: connection refused or "the database system is starting up" on the first test. Fix: poll a real query until it succeeds.
- **Fixed `sleep(5)` before the suite.** Symptom: a 5-second penalty on every run, and occasional failures on a slower machine. Fix: wait for a probe with a bounded retry.
- **Covering the seam with a full stack in every test.** Symptom: a 20-minute suite nobody runs locally. Fix: contract tests for the seam, one thin integration test for wiring.
- **Seeding with production data in a shared instance.** Symptom: personal data leaking into logs and screenshots. Fix: synthetic data on an ephemeral store.
- **Asserting on `SELECT *` column order or row order without sorting.** Symptom: intermittent failures on a different engine or plan.

## Anti-patterns

- **An "integration" suite that only calls the real service with every dependency mocked.** It is a slow unit test.
- **Rebuilding the world per test** when a session-scoped container would do.
- **Reaching the public internet from a test.** Non-deterministic, slow, and often blocked in CI.
- **Testing the ORM's basic CRUD.** If your test would pass with any correct ORM, it is not an integration test.
- **Asserting on full JSON documents** that contain timestamps and ids. Assert on the fields under test.
- **One giant end-to-end scenario** that covers checkout, email, invoicing and analytics. When it fails, no one knows where to look.

## References

- Testcontainers for Python - https://testcontainers-python.readthedocs.io/
- Testcontainers for Java - https://java.testcontainers.org/
- Testcontainers Node - https://node.testcontainers.org/
- Testcontainers for Go - https://golang.testcontainers.org/
- Spring Framework testing, transactional test rollback - https://docs.spring.io/spring-framework/reference/testing.html
- Django testing - https://docs.djangoproject.com/en/stable/topics/testing/
- Alembic documentation - https://alembic.sqlalchemy.org/
- Pact documentation - https://docs.pact.io/
- Pact broker - https://docs.pact.io/pact_broker
- PostgreSQL documentation, TRUNCATE - https://www.postgresql.org/docs/current/sql-truncate.html
- OWASP Testing Guide (integration and database testing) - https://owasp.org/www-project-web-security-testing-guide/