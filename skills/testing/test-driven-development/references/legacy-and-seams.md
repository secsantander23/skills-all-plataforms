# Seams and Legacy Code

The vocabulary is Michael Feathers': a *seam* is a place where you can alter behavior without editing in that place. Every seam is a design tool, and most untestable code is untestable because it has none.

## What counts as a seam

| Seam type | Where | Example |
| --- | --- | --- |
| Preprocessing seam | Build or load time | `#define SLEEP(x)` replaced for tests; a test-only compilation |
| Link seam | Link time | Replacing a function implementation at link time (common in C and C++) |
| Object seam | Runtime | Replacing a method on an object with a subclass or a decorated instance |
| Parameter seam | Call time | Passing what was previously read from a global |
| Thread seam | Runtime | Replacing the value returned by `now()` |

In managed languages the parameter seam is the workhorse: turn a global read into an argument.

## A procedure for introducing seams

Work in small steps, and keep the suite green at every step.

1. **Characterize.** Write a test that pins the current behavior of the target method, including the surprising cases. Run it against the unmodified code.
2. **Locate the pain.** Run the next test you want to write and note precisely what makes it slow, flaky, or impossible: a sleep, a database, a network call, a global, `now()`.
3. **Apply the smallest mechanical change.** One of the refactorings below. No behavior change, no new tests, no drive-by cleanups.
4. **Re-run everything.** The characterization test must still pass, unchanged.
5. **Write the new test** through the seam. It should now be fast and deterministic.
6. **Refactor the design** if the seam exposes an awkward interface, still with tests green.

## Mechanical refactorings that create seams

### Extract an interface and inject it

Before:

```python
def send_receipt(order_id: str) -> None:
    conn = psycopg.connect(DATABASE_URL)
    row = fetch_order(conn, order_id)
    smtp.send_email(row["email"], build_receipt(row))
```

After:

```python
class OrderRepository(Protocol):
    def fetch(self, order_id: str) -> dict: ...


class EmailSender(Protocol):
    def send(self, to: str, body: str) -> None: ...


def send_receipt(order_id: str, repo: OrderRepository, mailer: EmailSender) -> None:
    row = repo.fetch(order_id)
    mailer.send(row["email"], build_receipt(row))
```

The production entry point wires in the real implementations; the test wires in fakes. Keep the plain signature available even if your framework has a container, so tests do not need the container.

### Parameterize a global

```python
# Before
def is_expired(record) -> bool:
    return record["expires_at"] < datetime.now()

# After
def is_expired(record, now: datetime) -> bool:
    return record["expires_at"] < now
```

Callers that want the convenience get a wrapper:

```python
def is_expired_now(record) -> bool:
    return is_expired(record, datetime.now())
```

### Inject a scheduler

Sleep and timers are the most common source of slow and flaky tests. Inject a scheduler interface and use a fake in tests, a real one in production.

```python
from typing import Protocol


class Scheduler(Protocol):
    def sleep(self, seconds: float) -> None: ...
    def now(self) -> datetime: ...


class RealScheduler:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now()


class FakeScheduler:
    """Advances a virtual clock instead of waiting."""

    def __init__(self, start: datetime):
        self.current = start

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.current
```

### Make randomness injectable

```python
class RandomSource(Protocol):
    def choice(self, population: list): ...
    def integer(self, low: int, high: int) -> int: ...


# Tests pass a scripted implementation, so failures are reproducible.
class ScriptedRandom:
    def __init__(self, values):
        self._values = iter(values)

    def choice(self, population):
        return next(self._values)

    def integer(self, low, high):
        return next(self._values)
```

### Isolate the filesystem

```python
from pathlib import Path


class Storage(Protocol):
    def read(self, key: str) -> bytes: ...
    def write(self, key: str, data: bytes) -> None: ...


class DirectoryStorage:
    def __init__(self, root: Path):
        self._root = root

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise ValueError("key escapes storage root")
        return candidate

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class InMemoryStorage:
    def __init__(self):
        self._data: dict[str, bytes] = {}

    def read(self, key: str) -> bytes:
        return self._data[key]

    def write(self, key: str, data: bytes) -> None:
        self._data[key] = data
```

### Break up a god method with characterize-then-split

A method that talks to the database, formats a report, and sends an email cannot be tested in pieces. Split it, but only after you know what it currently does.

1. Write a characterization test calling the method with fakes for every dependency, asserting the observable outcome (what got written, what got sent).
2. Extract the "compute" step into a pure function that takes data in and returns data out.
3. The characterization test now exercises the pure function.
4. Extract the I/O steps into a separate method.

## Characterization tests, in practice

Named after observed behavior, marked so they can be found:

```python
import pytest

characterization = pytest.mark.skipif(
    os.environ.get("PIN_LEGACY_BEHAVIOR") != "1",
    reason="pins current behavior; revisit before changing PricingEngine",
)


@characterization
def test_pricing_engine_returns_cents_contrary_to_its_name():
    assert PricingEngine().quote(1500) == 1500


@characterization
def test_pricing_engine_raises_on_empty_cart():
    with pytest.raises(IndexError):
        PricingEngine().quote_for([])
```

Rules:

- Assert observable behavior, not implementation detail.
- Include the awkward cases. Those are the ones that bite during a refactor.
- Convert them to proper tests as you refactor, and delete them when you fix the underlying bug.
- Never delete a characterization test without understanding what it pinned.
- Skip them in normal runs if they encode behavior you are actively trying to remove, but never skip them without a stated reason.

## Languages without convenient seams

- **C and C++:** preprocessing and link seams. `#ifdef TESTING` overrides, or link-time substitution of the implementation. Both are constrained by the build system.
- **Go:** interfaces are implicit and structural, so a function parameter of interface type is usually enough. Generate fakes with `go.uber.org/mock` (formerly `golang/mock`) so an interface change breaks compilation.
- **Java and Kotlin:** constructor injection; a service locator or framework container hides the seam and makes the test require the framework. Keep a plain constructor.
- **Dynamic languages:** module-level globals and the system clock are the usual culprits. Wrap them once, then use the wrapper everywhere.

## When a seam is not worth building

- A one-line getter that reads a field. Just test the consumer.
- Code you are deleting within the sprint. Spend the effort on code that will live.
- A seam that requires changing four call sites for a test that will never be written.

The test: would you write the test if the seam existed? If yes, build it. If no, do not.

## References

- Michael Feathers, Working Effectively with Legacy Code - https://www.workingeffectivelywithlegacycode.com/
- xUnit Test Patterns, seams and characterization tests - https://xunitpatterns.com/
- Martin Fowler, Refactoring catalog - https://refactoring.com/catalog/
- pytest fixtures - https://docs.pytest.org/en/stable/how-to/fixtures.html
- Python `unittest.mock` - https://docs.python.org/3/library/unittest.mock.html
- go.uber.org/mock (mockgen) - https://github.com/uber-go/mock
- Java JUnit user guide - https://junit.org/junit5/docs/current/user-guide/