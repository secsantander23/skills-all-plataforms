# Test Data and Fixtures

Test data is a design surface. Unreadable arrange blocks are the most common reason unit tests get skipped during review.

## Rules

1. Every test constructs its own data. No shared mutable fixtures.
2. Make the value the test is about explicit. Do not bury it in a graph.
3. Build data through intent-revealing helpers, not positional booleans.
4. Prefer small, deterministic values. Magic numbers beat random data for locating a failure.
5. Do not load production fixtures into unit tests.

## Inline literals first

```python
def test_plan_limit_rejects_overage():
    # Arrange
    usage = {"seats": 11, "plan_limit": 10}

    # Act
    result = check_plan_limit(usage, plan="team")

    # Assert
    assert result.allowed is False
    assert result.overage == 1
```

If this stays under about six lines, stop here.

## Builder objects

Use a mutable, chainable builder when the object is large and one or two fields vary per test.

```python
from dataclasses import dataclass, field


@dataclass
class Customer:
    id: str
    email: str
    country: str
    tags: list[str]
    lifetime_value_cents: int
    created_on: object


@dataclass
class CustomerBuilder:
    _id: str = "cus_default"
    _email: str = "customer@example.com"
    _country: str = "PT"
    _tags: list[str] = field(default_factory=list)
    _lifetime_value_cents: int = 0

    def with_id(self, value: str) -> "CustomerBuilder":
        self._id = value
        return self

    def with_email(self, value: str) -> "CustomerBuilder":
        self._email = value
        return self

    def from_country(self, value: str) -> "CustomerBuilder":
        self._country = value
        return self

    def tagged(self, *values: str) -> "CustomerBuilder":
        self._tags.extend(values)
        return self

    def with_lifetime_value(self, cents: int) -> "CustomerBuilder":
        self._lifetime_value_cents = cents
        return self

    def build(self) -> Customer:
        return Customer(
            id=self._id,
            email=self._email,
            country=self._country,
            tags=list(self._tags),
            lifetime_value_cents=self._lifetime_value_cents,
            created_on="sentinel",
        )
```

Usage reads as a sentence:

```python
def test_discount_requires_lifetime_value_over_threshold():
    # Arrange
    customer = (
        CustomerBuilder()
        .with_id("cus_7")
        .with_lifetime_value(50_000)
        .tagged("vip")
        .build()
    )

    # Act
    discount = compute_loyalty_discount(customer)

    # Assert
    assert discount.pct == 10
```

Use `dataclasses.replace` for immutable variants instead of a builder:

```python
from dataclasses import replace

vip_customer = replace(base_customer, tags=["vip"], lifetime_value_cents=50_000)
```

## Factory functions

Prefer a factory when the variation is a single keyword and the object is small.

```python
def make_order(**overrides) -> dict:
    order = {
        "id": "ord_1",
        "lines": [],
        "status": "NEW",
        "currency": "EUR",
    }
    order.update(overrides)
    return order


def test_shipment_requires_a_paid_order():
    order = make_order(status="PAID")
    assert ship(order).accepted is True
```

Avoid the boolean trap:

```python
# Bad
def make_order(is_paid=False, is_cancelled=False): ...

# Good
def make_order(**overrides): ...
```

## Test-data generators

Generated data is for *scale* tests (property-based, fuzz, large volumes), not for pinning behavior. Libraries such as `faker` (Python) and `@faker-js/faker` (JavaScript) produce plausible values; seed them for reproducibility.

```python
from faker import Faker

fake = Faker()

def test_address_formats_across_locales():
    address = fake.address()
    result = format_address(address)
    assert result.postal_code
```

Verify the current API with your library's docs; generator options change between major versions.

## Property-based testing

Property tests assert a universal rule over generated inputs. They find counterexamples your table of cases misses.

```python
from hypothesis import given, strategies as st


@given(st.integers(min_value=0, max_value=10_000))
def test_discount_never_exceeds_subtotal(subtotal_cents):
    assert apply_discount(subtotal_cents, 10) <= subtotal_cents
```

Hypothesis stores a failure database and replays the counterexample. That database is a fixture: commit it, and never ignore a failing example by adding a filter without understanding it.

## Golden files

Use a checked-in expected output when the value is large, stable, and a change should force a human to review the diff.

```python
def test_report_matches_golden_file():
    actual = render_report(orders_for("2024-03"))
    expected = (FIXTURES_DIR / "report_2024_03.golden").read_text(encoding="utf-8")
    assert actual == expected
```

Rules: normalize volatile fields (timestamps, ids, ordering) before comparing, and always regenerate goldens with a deliberate command, never by copying the actual output blindly.

## Fixtures (pytest)

Fixtures are setup and teardown hooks resolved by name. Use them for expensive or global setup, not for hiding the data the test asserts on.

```python
import pytest


@pytest.fixture(scope="session")
def loaded_rules():
    """Expensive: parse once for the whole session."""
    return RuleSet.parse(Path("config/rules.json"))


@pytest.fixture
def repo(tmp_path):
    """Fresh, isolated storage per test."""
    store = FileBackedRepo(tmp_path / "store.json")
    yield store
    store.close()


@pytest.fixture(autouse=True)
def deterministic_clock(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
```

Rules that matter:

- Default scope is function. Widen only when the object is immutable and genuinely expensive.
- Prefer `yield` fixtures when teardown is needed. Pytest runs teardown even if the test fails.
- Autouse fixtures should be rare and should restore global state.
- Do not have one fixture depend on another in a chain longer than a couple of links. The test then cannot tell you what it needs.

## JavaScript setup helpers

```js
// test/helpers.js
const baseOrder = (overrides = {}) => ({
  id: 'ord_1',
  status: 'NEW',
  currency: 'EUR',
  ...overrides,
});

module.exports = { baseOrder };
```

```js
// vitest / jest style lifecycle hooks
beforeEach(() => {
  container = new InMemoryContainer();
  container.start();
});

afterEach(async () => {
  await container.stop();
});
```

With Playwright, prefer a function that returns plain data over a `beforeEach`, so the test reads as arrange, act, assert:

```js
function makeOrder(overrides = {}) {
  return { id: 'ord_1', status: 'NEW', ...overrides };
}
```

## Data hygiene

- Never copy production customer data into a test fixture. You will leak it eventually.
- Never depend on a specific record id that another test created. Create your own data.
- Reset any seed database between suites rather than assuming a clean slate, and assert the assumption once per suite so the failure points at the wrong place.
- Keep fixture files close to the tests that use them. A shared `fixtures/` directory at the repo root becomes a dumping ground.

## Anti-patterns

- A `conftest.py` that constructs the entire application context for every test.
- Module-level mutable data used as a fixture.
- Random data without a fixed seed, so failures cannot be reproduced.
- A helper named `setup()` or `build_default_thing()` with fifteen parameters, none of which the reader can infer.
- Copying expected values from the actual output once, which asserts only that the code runs.