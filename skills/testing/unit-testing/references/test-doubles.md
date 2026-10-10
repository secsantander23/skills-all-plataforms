# Test Doubles Catalog

A test double replaces a real collaborator during a test. The vocabulary comes from xUnit Test Patterns. Pick the kind that gives you the least coupling to the collaborator's implementation.

## The ladder

Real implementation -> Fake -> Stub -> Spy -> Mock -> Dummy.

Move as far right as necessary and no further. Each step right buys finer control and costs more coupling to internals.

## Dummy

A valid but inert object passed to satisfy a signature you do not exercise.

```python
class DummyMailer:
    def send(self, to: str, subject: str, body: str) -> None:
        return None


def test_report_runs_without_a_mailer():
    result = build_report(DummyMailer(), today=date(2024, 3, 1))
    assert result.rows == 3
```

Use when the parameter is required but irrelevant. Never assert on the dummy.

## Stub

Returns canned values so the code under test takes a specific path. Not verified directly.

```python
class StubGateway:
    def __init__(self, response):
        self.response = response

    def charge(self, amount_cents: int, token: str):
        return self.response


def test_success_path_returns_confirmation():
    gateway = StubGateway(response=ChargeResult(ok=True, charge_id="ch_123"))
    result = process_payment(gateway, amount_cents=1000, token="tok_visa")

    assert result.status == "confirmed"
    assert result.charge_id == "ch_123"
```

Stub at the level of *data returned*, not at the level of *internal calls*. A stub that also fails if you call it in the wrong order has become a mock.

## Spy

A real or realistic implementation wrapped so you can observe what happened. Prefer this over a mock when the behavior under test is "did the side effect happen".

```python
class RecordingMailer:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject))


def test_welcome_email_sent_once():
    mailer = RecordingMailer()
    signup("a@example.com", mailer=mailer)

    assert len(mailer.sent) == 1
    to, subject = mailer.sent[0]
    assert to == "a@example.com"
    assert subject == "Welcome"
```

In Jest the same idea is a spy on a real object:

```js
const logSpy = jest.spyOn(logger, 'info');

doThing();

expect(logSpy).toHaveBeenCalledWith('done', { id: 1 });
logSpy.mockRestore();
```

## Mock

A double with a strict, expected protocol. You assert the interaction itself.

Use a mock when the *call* is the requirement: "the repository must have been asked to save the order exactly once".

```python
from unittest.mock import Mock

def test_order_is_persisted_once():
    repo = Mock()

    place_order(repo, order_id="o-1")

    repo.save.assert_called_once_with(order_id="o-1", status="PENDING")
```

Keep mock expectations narrow. Every extra `assert_called_with` is a claim about the current implementation.

### Mocking errors

```python
from unittest.mock import Mock, PropertyMock

session = Mock()
session.commit.side_effect = IntegrityError("duplicate key")

with pytest.raises(DomainError, match="could not save order"):
    save_order(session, order)
```

### Partial mocks: spy, do not replace

```python
import time
from unittest.mock import patch

@patch("myapp.retry.time.sleep")   # patch where the name is looked up
def test_retry_waits_between_attempts(mock_sleep):
    with pytest.raises(TooManyRetries):
        fetch_with_retry(attempts=3)

    assert mock_sleep.call_count == 2
```

Two rules that cause real bugs when forgotten:

1. Patch the symbol *where it is used*, not where it is defined. `patch("myapp.retry.sleep")` for `sleep` called inside `myapp/retry.py`; `patch("time.sleep")` for `sleep` called by the standard library on your behalf.
2. Wrap in the `with` block (or a decorator) so the patch is undone even when the test fails. A leaked patch silently rewrites behavior for every test that runs after it.

### Mocking properties and constants

```python
with patch("myapp.config.settings") as settings:
    settings.max_retries = 3
    settings.timeout_seconds = 0.5
    run()
```

For a module attribute, patch the module. For a class attribute, `patch.object(Cls, "attr", value)`.

## Fake

A working but simplified implementation. Faster and more honest than a mock for anything stateful.

```python
class InMemoryRepo:
    """Fake that behaves like the real store for the behavior under test."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def save(self, record: dict) -> None:
        self.rows[record["id"]] = dict(record)

    def find(self, record_id: str):
        return self.rows.get(record_id)
```

A fake is the right choice when:

- The real object is expensive (a database round trip, a network call, a large in-memory index).
- The real object's behavior is easy to reproduce correctly and would make the test slow.
- The test needs many interacting calls, where a mock's call assertions become unreadable.

A fake is the wrong choice when the point of the test is the interaction protocol with that specific collaborator. A fake cannot tell you it was called with the wrong arguments; it just behaves differently.

## Choosing quickly

| Question | Use |
| --- | --- |
| Do I care that the call happened? | Spy on the real thing |
| Do I care that the call happened with exact arguments? | Mock |
| Do I need the code under test to take a specific branch? | Stub or fake |
| Do I need a plausible value in a field I ignore? | Dummy |
| Do I need many interacting operations to be realistic? | Fake |

## Framework equivalents

| Concept | Python (`unittest.mock`) | JavaScript (Jest) | Java (Mockito) | Go |
| --- | --- | --- | --- | --- |
| Stub | `Mock` with `return_value` | `jest.fn()` with `mockReturnValue` | `when(...).thenReturn(...)` | hand-written struct or generated fake |
| Mock with verification | `Mock` + `assert_called_once_with` | `jest.fn()` + `toHaveBeenCalledWith` | `verify(...)` | testify `mock.On(...).Once()` |
| Spy | `patch(..., wraps=real)` | `jest.spyOn(obj, 'method')` | `spy(real)` | wrap the struct |
| Patch target | `patch("module.name")` | `jest.spyOn` / `jest.mock` | `mockStatic(...)` | build tags for build-time fakes |

For Go, the ecosystem standard is generated fakes (`go.uber.org/mock`, formerly `golang/mock`) checked in under `internal/mocks` or `pkg/mocks`. Generated fakes compile against the interface, so a signature change breaks the build rather than silently passing.

## What doubles must never do

- Return a value that makes an impossible scenario look realistic (a fake that accepts any email, when the real validator would reject it).
- Hide a bug by being more forgiving than the real implementation. This is the single most dangerous failure mode of a fake.
- Contain business logic. If the fake decides what the correct answer is, the test proves nothing.
- Be shared between tests with mutable state, unless they are created fresh each time.