---
name: Unit Testing
description: Covers unit test design, the arrange-act-assert structure, test doubles (dummy, stub, spy, mock, fake), test isolation and determinism, parameterized tests, readable assertions, test naming, and avoiding over-mocking. Use when the user asks how to write unit tests, asks what to mock or stub, when tests are slow flaky or order-dependent, when test names or assertions are unclear, or when reviewing test code quality.
metadata:
  opencode/autoinvoke: true
---

# Unit Testing

A unit test pins down one small piece of behavior so that a change to something else cannot silently break it. The core mental model is a fast, hermetic, deterministic experiment whose failure message tells you where to look without opening a debugger.

## When to use this

- Writing the first tests for a module, class, or function.
- Deciding whether a test should mock or use a real collaborator.
- Tests that pass alone but fail in the suite (order dependence, shared state).
- Tests that are slow, flake, or depend on the network, clock, filesystem, or randomness.
- Deciding what deserves a test at all versus what to leave to integration or E2E.
- Reviewing test code in a pull request.

## Quick start

Python with pytest:

```python
# tests/test_order_total.py
import pytest

from pricing.totals import compute_total


@pytest.mark.parametrize(
    ("unit_price_cents", "quantity", "discount_pct", "tax_pct", "expected_cents"),
    [
        (1000, 2, 0, 0, 2000),
        (1000, 2, 10, 0, 1800),
        (1000, 2, 10, 20, 2160),
        (0, 5, 50, 20, 0),
    ],
)
def test_compute_total(unit_price_cents, quantity, discount_pct, tax_pct, expected_cents):
    assert compute_total(unit_price_cents, quantity, discount_pct, tax_pct) == expected_cents
```

JavaScript with Jest:

```js
// src/pricing/__tests__/orderTotal.test.js
const { computeTotal } = require('../orderTotal');

describe('computeTotal', () => {
  it('applies the discount before tax', () => {
    // Arrange
    const order = { unitPriceCents: 1000, quantity: 2, discountPct: 10, taxPct: 20 };

    // Act
    const total = computeTotal(order);

    // Assert
    expect(total).toBe(2160);
  });
});
```

## What makes a good unit test

Judge a unit test by these properties. When one is missing, the test is costing more than it saves.

| Property | Meaning | If missing |
| --- | --- | --- |
| Fast | Milliseconds, not seconds | Suite becomes too slow to run, so people stop running it |
| Deterministic | Same result every run, any machine, any order | Trust erodes; flakes get retried instead of fixed |
| Isolated | Depends only on inputs you supply | Failures point at the wrong cause |
| One behavior | One reason to fail | A failure does not tell you what broke |
| Self-describing | Name and failure message locate the defect | You must re-read the source to understand the failure |
| Refactor-safe | Breaks when behavior changes, not when structure changes | Renaming a private field breaks the build |

A unit test also has a maintenance cost. Every assertion is a claim you must keep true. Prefer a handful of behavior-level tests over exhaustive structural tests.

## The AAA structure

Arrange, Act, Assert. Three blocks, in that order, each separated by a blank line.

```python
def test_rejects_an_expired_card():
    # Arrange
    card = Card(number="4242424242424242", exp_month=1, exp_year=2020)
    today = date(2024, 6, 1)

    # Act
    result = validate_card(card, today)

    # Assert
    assert result.is_valid is False
    assert result.reason == "expired"
```

Rules that keep AAA visible:

- One Act statement per test. If you need two Act calls, you usually have two tests.
- Assert on returned values and observable state, not on intermediate steps.
- Keep the Assert block to the claim under test. Use a helper or custom message when you need more than one assertion about the same outcome.

```python
# A custom assertion keeps the failure message readable and the test short.
def assert_result_is_rejected(result, reason):
    assert result.accepted is False, f"expected rejection, got accepted={result.accepted}"
    assert result.reason == reason, f"expected reason {reason!r}, got {result.reason!r}"
```

## Arranging test data

Data setup is where tests become unreadable and where shared mutable fixtures cause order dependence.

Use these in order of preference:

1. **Inline literals.** If the object is small, build it in the Arrange block. The test should be readable top to bottom without jumping.
2. **Small builder functions.** Extract when construction exceeds a few lines or varies along one dimension.
3. **Object mothers / factories.** Extract when the same shape recurs across many test files and needs variation.
4. **Snapshot or golden fixtures.** Use when the value is large, stable, and you genuinely want a change to force a review of a diff.

```python
# tests/builders.py
from dataclasses import dataclass, field, replace
from datetime import date


@dataclass
class OrderBuilder:
    lines: list = field(default_factory=list)
    discount_pct: int = 0
    tax_pct: int = 0
    placed_on: date = date(2024, 1, 15)

    def with_line(self, unit_price_cents: int, quantity: int = 1) -> "OrderBuilder":
        self.lines.append((unit_price_cents, quantity))
        return self

    def with_discount(self, pct: int) -> "OrderBuilder":
        self.discount_pct = pct
        return self

    def build(self) -> dict:
        return {
            "lines": list(self.lines),
            "discount_pct": self.discount_pct,
            "tax_pct": self.tax_pct,
            "placed_on": self.placed_on,
        }


def test_discount_applies_to_the_whole_order():
    # Arrange
    order = (
        OrderBuilder()
        .with_line(1000, quantity=2)
        .with_line(250, quantity=1)
        .with_discount(10)
        .build()
    )

    # Act
    total = compute_total(order)

    # Assert
    assert total == 2030  # 2250 - 225
```

Anti-patterns in arrange:

- A shared mutable module-level fixture object reused across tests. One test mutates it and the next fails mysteriously.
- `setUp` that builds a large object graph nobody reads.
- Helper functions with positional booleans (`make_order(True, False)`) that no reader can decode.
- Fixtures that hide the one field the test actually cares about.

## Test doubles

Five kinds, distinguished by what they record and control.

| Kind | Provides | Records calls | Purpose |
| --- | --- | --- | --- |
| Dummy | A valid, inert value | No | Fill a parameter you do not care about |
| Stub | Canned responses | No | Drive the code under test down a path |
| Spy | Real behavior, wrapped | Yes | Verify an outcome happened indirectly |
| Mock | Canned responses | Yes, and you assert on the calls | Verify an interaction or a contract |
| Fake | Working, simplified implementation | No | Replace something expensive or stateful |

Choose the lightest double that lets the test assert the behavior it cares about. Escalating from real to fake to stub to mock keeps tests resilient; a test built on mocks breaks when the collaborator's interface changes.

```python
# Dummy - just needs to be a valid object
result = send_report(recipient=DummyMailbox(), body="nightly")

# Stub - canned response to steer the branch
class FakeRateLimiter:
    def __init__(self, allowed: bool):
        self.allowed = allowed

    def allow(self, key: str) -> bool:
        return self.allowed


def test_upload_rejected_when_rate_limited():
    limiter = FakeRateLimiter(allowed=False)
    assert upload(bytes(1024), limiter).status == 429

# Spy - real object, observe what it did
def test_welcome_email_is_sent_on_signup(mailoutbox):
    signup("new-user@example.com")
    assert len(mailoutbox) == 1
    assert mailoutbox[0].subject == "Welcome"
```

Framework doubles, stable APIs:

```python
from unittest.mock import Mock, MagicMock, patch, call

# Stub
repo = Mock()
repo.find.return_value = User(id=7, email="a@example.com")

# Verify an interaction (mock)
repo.find.assert_called_once_with(7)
repo.find.assert_has_calls([call(7), call(9)])

# Patch where the name is looked up, not where it is defined
with patch("billing.pricing.tax.lookup_rate") as lookup_rate:
    lookup_rate.return_value = 0.2
    assert price_with_tax(1000) == 1200
```

```js
// Jest
const repo = { find: jest.fn().mockResolvedValue({ id: 7 }) };
const spy = jest.spyOn(logger, 'info');

expect(repo.find).toHaveBeenCalledTimes(1);
```

See `references/test-doubles.md` for the full catalog, and `references/test-data.md` for fixture and builder patterns.

## Isolation

A unit test must not be able to observe another test's effects.

- Create fresh objects in every test. Never share mutable state through module or class scope.
- Do not depend on execution order. If order matters, the suite has a design problem.
- Do not depend on leftovers in a database, a temp directory, or a global cache.
- Clean up anything global you must touch (environment variables, current working directory, timezone, locale) with a fixture that restores the original value.

```python
import os
import pytest


@pytest.fixture
def clean_env(monkeypatch):
    # monkeypatch restores every variable it touches, even when the test fails.
    monkeypatch.delenv("FEATURE_BILLING", raising=False)
    monkeypatch.setenv("TZ", "UTC")


def test_feature_flag_off_by_default(clean_env):
    assert os.getenv("FEATURE_BILLING") is None
```

## Determinism

Non-determinism is the single largest source of flaky unit tests. Ban it at the edges.

| Non-determinism | Symptom | Fix |
| --- | --- | --- |
| Wall clock | Fails at midnight or on a leap day | Inject a clock. Pass `now` as a parameter |
| Randomness | Fails 1 run in 20 | Inject a seeded generator or an explicit value |
| Sleep and timers | Fails on a loaded CI runner | Inject a scheduler, or drive the clock explicitly |
| Iteration order of a set or dict-derived ordering | Different failure each run | Sort before asserting, or compare as sets |
| Network | Fails behind a firewall | Replace with a fake, or move the test to the integration layer |
| Environment variables | Passes locally, fails in CI | Set them explicitly in the test, not in the shell |
| Locale, timezone, line endings | Passes on Linux, fails on Windows | Pin the relevant setting in the test |

```python
from datetime import date


def expires_after_one_year(valid_until: date, today: date) -> bool:
    """Business logic takes the clock as data, so it can be tested at any instant."""
    return (valid_until - today).days == 365
```

## Parameterization

Use parameterized tests to cover a table of cases without copying the Arrange/Act/Assert block. Each case becomes a separately reported test, so you still learn which input broke.

```python
@pytest.mark.parametrize("value,expected", [(0, 0), (1, 1), (-1, -1), (2**31, 2**31)])
def test_identity(value, expected):
    assert identity(value) == expected


@pytest.mark.parametrize("email", ["", "no-at-sign", "a@b", "a@b.c"])
def test_rejects_invalid_email(email):
    assert validate_email(email) is False
```

```js
test.each([
  [0, 0],
  [1, 1],
  [-1, -1],
])('identity(%i) returns %i', (input, expected) => {
  expect(identity(input)).toBe(expected);
});
```

Do not parameterize cases that fail for different reasons. One reason to fail per parameter row is the point.

## Readable assertions

A failing assertion should read as a sentence about behavior.

```python
# Weak: "assert 183 == 180"
assert result.total_cents == 180

# Strong: what was expected, what happened, and under what conditions
assert result.total_cents == 180, (
    f"expected 180 cents for a 10% discount on 200, got {result.total_cents}"
)
```

Useful assertion libraries and idioms:

- Python `pytest`: plain `assert` works everywhere and reads best. Use `pytest.approx` for floats.
- JavaScript Jest: `toBe` for primitives, `toEqual` for structure, `toContain` for membership, `toMatchObject` for a subset, `expect.assertions(n)` when a call count matters.
- Java AssertJ: `assertThat(actual).isEqualTo(expected).as("context").isNotNull()`.
- When asserting several facts about one outcome, group them so the first failure does not hide the rest. In Playwright use `expect.soft`; elsewhere prefer one clear assertion or a helper that reports all mismatches.

```python
assert compute_total(order) == pytest.approx(180.0, abs=0.01)
```

## Naming

A test name is a sentence describing a rule. Include the condition, the actor, and the expected outcome.

```
test_<unit>_<condition>_<expected outcome>
```

Good:

```
test_guest_checkout_rejects_expired_card
test_apply_discount_returns_zero_for_empty_cart
test_retry_is_attempted_three_times_then_surfaces_error
test_user_without_membership_gets_no_discount
```

Bad, and what to fix:

| Name | Problem |
| --- | --- |
| `test1`, `test_works` | Says nothing. Name the rule |
| `test_discount` | Too broad to identify the failure |
| `test_api_2` | Encodes ordering, not behavior |
| `test_uses_mock` | Describes the test's mechanics, not the system's behavior |

Group by unit and by situation so the suite reads as a specification:

```js
describe('refundOrder', () => {
  describe('when the order was paid by card', () => {
    it('issues a refund to the original card', () => {});
  });
  describe('when the order was already refunded', () => {
    it('raises AlreadyRefunded', () => {});
  });
});
```

## What to test, and what not to

Test:

- Business rules, branching, and edge cases (empty, zero, negative, boundary, maximum).
- Error translation: the error the caller receives, not just the message logged.
- Contracts at module boundaries, especially argument validation.
- Bug regressions. Write a failing test first that reproduces the bug, then fix.

Do not test:

- Private methods reached through reflection or by calling a public method "just to get there".
- Framework behavior. Testing that your ORM maps a column is testing the ORM.
- Generated getters and setters with no logic.
- Exact log message text or internal call order, unless the log is a machine-parsed contract.
- Constants and their values. Assert the behavior that depends on them.
- Everything, on the theory that coverage is the goal. Coverage tells you what was executed, not what was verified.

## Checklist

A unit test is done when:

- [ ] It runs in isolation, in any order, on any machine.
- [ ] It makes no network call, reads no clock, and sleeps nowhere.
- [ ] It has one Act statement and one reason to fail.
- [ ] The failure message names the expected value, the actual value, and the context.
- [ ] It asserts on behavior, not on internal call sequences.
- [ ] It uses the lightest double that still lets it assert the real outcome.
- [ ] It creates its own data and shares no mutable state with other tests.
- [ ] The name states the rule, not the mechanics.
- [ ] Running the whole suite takes seconds, not minutes.

## Common mistakes

- **Test passes alone, fails in the suite.** Cause: shared mutable fixture or leaked global state. Fix: construct per-test data and restore globals in a fixture.
- **Flaky roughly 1 run in 20.** Cause: unseeded randomness or a real clock. Fix: inject the value.
- **"Expected 180, got 183" with no context.** Cause: bare `assert`. Fix: add a message or restructure the assertion.
- **Renaming a variable breaks a test.** Cause: the test asserts on internals. Fix: assert on the public contract.
- **A test fails only on CI.** Cause: environment differences in locale, timezone, line endings, or CPU count. Fix: pin the setting inside the test.
- **Test doubles return `Mock()` for everything.** Cause: `MagicMock` used for a typed collaborator. Fix: write a small fake or a typed stub.
- **Coverage near 100 percent but production still has bugs.** Cause: asserting on getters. Fix: assert on behavior with realistic values.

## Anti-patterns

- **Mock everything.** Tests that pass while the real integration is broken, because every boundary is faked and nothing checks the contract between them.
- **Assert on call order** (`assert_has_calls` with exact ordering) for behavior that is order-independent. Couples the test to the current implementation.
- **Testing the framework.** A test that re-implements your function's logic in the assertion proves nothing.
- **Giant setup blocks** that hide the one value the test cares about.
- **One test per function per branch**, forcing a combinatorial explosion. Parameterize instead.
- **Using a mock's return value as the expected value.** `assert service.call(x) == mock.call(x)` is a tautology.
- **Chasing coverage percentage.** Tests written to execute a line rather than to verify a rule.
- **Over-specified error messages.** Asserting on a full stack-trace string breaks on every refactor.

## References

- pytest documentation, fixtures and parametrize - https://docs.pytest.org/en/stable/how-to/fixtures.html
- pytest documentation, parametrize - https://docs.pytest.org/en/stable/how-to/parametrize.html
- Jest documentation, mocking - https://jestjs.io/docs/mock-functions
- Jest documentation, expect - https://jestjs.io/docs/expect
- Python `unittest.mock` - https://docs.python.org/3/library/unittest.mock.html
- The xUnit Test Patterns book (test doubles, smells, and their fixes) - https://xunitpatterns.com/
- Martin Fowler, Test Double - https://martinfowler.com/bliki/TestDouble.html
- Google Testing Blog, Test Sizes - https://testing.googleblog.com/2010/12/test-sizes.html
- JUnit 5 user guide, assertions and assumptions - https://junit.org/junit5/docs/current/user-guide/
- Test frameworks differ in syntax; check the official docs for your runner before copying an idiom.