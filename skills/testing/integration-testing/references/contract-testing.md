# Contract Testing Between Services

Contract testing replaces "start both services and call one from the other" with "publish a shared expectation about the seam". It is cheaper, runs in each repo's own CI, and fails at the moment one side changes.

## Consumer-driven contracts with Pact

The consumer owns the expectation. The consumer's tests record real interactions; the provider verifies them.

### Consumer side

```javascript
// test/contracts/pricing.pact.test.js
const path = require('node:path');
const { PactV3 } = require('@pact-foundation/pact');
const { createPricingClient } = require('../../src/pricingClient');

const provider = new PactV3({
  consumer: 'checkout-service',
  provider: 'pricing-service',
  dir: path.resolve(__dirname, 'pacts'),
  logLevel: 'warn',
});

describe('pricing client', () => {
  beforeEach(() => {
    provider
      .given('a product with a unit price of 1500 cents')
      .uponReceiving('a request for the unit price')
      .withRequest({ method: 'GET', path: '/prices/ABC-1' })
      .willRespondWith({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: { sku: 'ABC-1', unitPriceCents: 1500, currency: 'EUR' },
      });
  });

  test('returns the unit price', async () => {
    await provider.executeTest(async (mockService) => {
      const client = createPricingClient(mockService.url);
      const price = await client.getUnitPrice('ABC-1');

      expect(price.unitPriceCents).toBe(1500);
      expect(price.currency).toBe('EUR');
    });
  });
});
```

Python equivalent:

```python
# tests/contracts/test_pricing_pact.py
from pathlib import Path

from pact import Consumer, Provider
import pytest

from checkout.pricing_client import PricingClient

provider = Provider(
    name="pricing-service",
    pact_dir=str(Path(__file__).parent / "pacts"),
)
consumer = Consumer("checkout-service")


@pytest.mark.pact(provider=provider, consumer=consumer)
def test_get_unit_price(pact):
    pact.given("a product with a unit price of 1500 cents") \
        .upon_receiving("a request for the unit price") \
        .with_request("get", "/prices/ABC-1") \
        .will_respond_with(200, body={"sku": "ABC-1", "unitPriceCents": 1500, "currency": "EUR"})

    client = PricingClient(pact.url)
    price = client.get_unit_price("ABC-1")

    assert price.unit_price_cents == 1500
```

Check the current API for your Pact library; the DSL surface has been revised between major versions. The principles below do not change.

### Provider side

The provider runs a verification test against a real instance of the service, seeded with whatever states its pacts require.

```javascript
// pricing-service/test/contracts/verify-pact.test.js
const { Verifier } = require('@pact-foundation/pact');

describe('Pact verification', () => {
  it('honours its contracts', async () => {
    await new Verifier({
      provider: 'pricing-service',
      providerBaseUrl: process.env.PROVIDER_BASE_URL,
      providerStatesSetupUrl: process.env.PROVIDER_STATES_URL,
      pactBrokerUrl: process.env.PACT_BROKER_URL,
      providerVersion: process.env.GIT_COMMIT,
      logLevel: 'info',
    }).verifyProvider();
  });
});
```

The `providerStatesSetupUrl` points at a test-only endpoint that arranges the world for a given state name, for example "a product with a unit price of 1500 cents". Keep it disabled outside test environments.

### Broker and versioning

Without a broker, the consumer commits the pact file and publishes it as a build artifact (a container image tag, an S3 object, a package). The provider's CI fetches the latest consumer pacts and verifies them.

With a broker (PactFlow, or a hosted option), you get pending pacts, canary verification against a deployed environment, and a UI for pending failures. Costs: an extra service to operate, plus webhook configuration in both repos.

Version tags you can attach to a pact:

- Consumer branch tags, so a provider verifies pacts from a feature branch during review.
- Provider tags, so a provider instance only verifies pacts targeted at its branch or environment.
- Environment tags, for canary verification against staging.

Example commands (confirm against your broker's docs):

```bash
# consumer: publish the pact for this build
pact-broker publish pacts --consumer-app-version "$GIT_SHA"

# provider: fetch pacts for this provider and verify
pact-broker verify-provider --provider pricing-service --provider-app-version "$GIT_SHA"
```

## Message contracts

For event-driven systems, Pact models messages the same way: the consumer states which message it expects, with a provider state that arranges the broker to have that message published.

```javascript
provider
  .given('an order was placed')
  .expectsToReceive('an order placed event')
  .withContent({
    'content-type': 'application/json',
    body: {
      eventType: 'OrderPlaced',
      orderId: 'ord_1',
      customerId: 'cus_1',
      totalCents: 3000,
    },
  })
  .withMetadata({ 'content-type': 'application/json' });
```

Contract testing is particularly valuable here, because integration-testing async flows is slow and fragile.

## Managing the contract lifecycle

| Change | Who breaks | How to roll it |
| --- | --- | --- |
| Add a new response field | Nobody, if consumers ignore unknown fields | Deploy provider, then consumer is unnecessary |
| Add a new field the consumer requires | Provider | Provider first, then enable the consumer feature flag |
| Rename or remove a field | Provider | Versioned endpoint, or a deprecation window |
| Change a type or format | Provider | New version, migrate consumers, remove old |
| Tighten a validation rule | Provider | Announce, monitor 4xx rates, then enforce |

Rules that prevent outages:

- Treat the contract file as reviewed, version-controlled source. A PR that changes it gets a human reviewer from the other team.
- Prefer additive changes. New optional fields are backward compatible; removals are not.
- Verify pacts on every provider build, not nightly. A provider that merges a breaking change should fail its own CI.
- Add provider state coverage deliberately. A pact with an unmatched state silently skips verification.

## When contract testing is not worth it

- A single-team monolith split into modules with no network seam. A direct integration test is simpler.
- Very high-churn internal endpoints you fully control, where an integration test is cheap to write and maintain.
- Read-only third-party APIs with no chance of you changing them. Pin them with a contract-style fixture or a fake instead.

## References

- Pact documentation - https://docs.pact.io/
- Pact Pactfile format - https://docs.pact.io/reference/pactfile
- Pact broker CLI - https://docs.pact.io/pact_broker/cli
- Pact JS - https://github.com/pact-foundation/pact-js
- Pact Python - https://github.com/pact-foundation/pact-python
- Pact Broker - https://pact.dius.com.au or https://docs.pact.io/pact_broker
- Consumer-Driven Contracts (Martin Fowler) - https://martinfowler.com/articles/consumerDrivenContracts.html