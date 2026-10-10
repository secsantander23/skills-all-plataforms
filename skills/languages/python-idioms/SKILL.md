---
name: Python Idioms and Correctness
description: Covers typing with mypy and pyright, dataclasses versus plain classes, generators and comprehensions, context managers, decorators, the GIL with threading versus multiprocessing versus asyncio, pyproject.toml packaging, virtual environments, exception handling discipline, and itertools and functools. Use when writing or reviewing Python, when the user asks about type hints, mutable default arguments, context managers, packaging, or when choosing between threads, processes, and async for CPU or IO work.
metadata:
  opencode/autoinvoke: true
---

# Python Idioms and Correctness

Python is dynamically typed with an optional static layer, dynamic containers, and implicit
costs that surprise people coming from statically typed languages. The four habits that
prevent most bugs: make invalid states unrepresentable with `dataclass` plus types, make
lifetimes explicit with context managers, make side effects explicit with `asyncio` and
file I/O, and make sharing rules explicit with the GIL.

## When to use this

- The user is writing Python and wants idiomatic structure rather than Java-in-Python.
- The user asks about type annotations, `mypy`, or `pyright` configuration.
- The user hit "got multiple values for argument", a `RuntimeError: dictionary changed size
  during iteration`, an "object has no attribute" from a decorator, or an
  `AttributeError` from a mutable default.
- The user is choosing between threads, `multiprocessing`, and `asyncio`.
- The user is publishing a package and asks about `pyproject.toml`, build backends,
  or virtual environments.
- The user is writing a decorator or context manager and asks about ordering,
  arguments, or `with` semantics.

## Quick start

A typed, immutable value object with behaviour, plus a context manager for the resource:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self


@dataclass(frozen=True, slots=True)
class UserId:
    """Newtype-ish wrapper so UserId and OrderId cannot be swapped."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("UserId must not be empty")


@dataclass(frozen=True, slots=True)
class User:
    id: UserId
    display_name: str
    tags: frozenset[str] = field(default_factory=frozenset)

    def with_tag(self, tag: str) -> Self:
        return User(id=self.id, display_name=self.display_name, tags=self.tags | {tag})


@contextmanager
def read_json(path: Path) -> Iterator[dict[str, object]]:
    """Context manager that guarantees the file closes, even on exception."""
    with path.open(encoding="utf-8") as handle:
        yield json.load(handle)


data = read_json(Path("config.json"))
```

`frozen=True` makes the value hashable and immune to accidental mutation. `slots=True`
removes the per-instance `__dict__`, cutting memory and adding a real performance benefit
for objects created in large numbers. `Self` (from `typing`, spelled `typing.Self`) lets a
method return its own concrete subclass type without naming it.

## Typed code that a checker accepts

```python
from collections.abc import Sequence
from typing import Final, TypeVar

T = TypeVar("T")


def first(items: Sequence[T]) -> T | None:
    return items[0] if items else None


MAX_RETRIES: Final = 5


def parse(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None
```

Rules that pay off:

- Annotate every public function. Internal helpers can be left to inference, but then
  `reveal_type` is your only debugger.
- Use `X | None` (PEP 604 syntax) with `from __future__ import annotations`, or a Python
  whose native parser supports it. The `Optional[X]` and `Union[X, Y]` spellings still
  work everywhere; check your minimum version before using the newer syntax.
- `Sequence[T]` (read-only) rather than `list[T]` for parameters; return `list[T]` when you
  actually build one.
- `Final` for module constants and class attributes that must not be reassigned.
- Use `@dataclass(slots=True)` rather than hand-writing `__slots__`.
- `TypedDict` for dictionaries with a known shape, `NamedTuple` for records, and
  `Protocol` for structural interfaces.

```python
from typing import NotRequired, TypedDict


class Row(TypedDict):
    id: str
    name: str
    email: NotRequired[str]


def greet(row: Row) -> str:
    return f"{row['name']} <{row.get('email', 'unknown')}>"
```

Checkers: `mypy` and `pyright` are the two mainstream options. Both read configuration
from the project (`mypy` from `mypy.ini`, `setup.cfg`, or `pyproject.toml`; `pyright`
from `pyproject.toml` under `[tool.pyright]` or a `pyrightconfig.json`). Both understand
`pyproject.toml`-embedded configuration; verify the exact section names and options against
each tool's own documentation rather than copying a config from a blog post.

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_unreachable = true

[[tool.mypy.overrides]]
module = "some_untyped_third_party.*"
ignore_missing_imports = true
```

Run the checker in CI. Editor-only checking hides configuration drift, the same failure
mode as running TypeScript only in an editor.

## dataclasses versus classes

| Use | When |
| --- | --- |
| `@dataclass(frozen=True, slots=True)` | data with derived behaviour, value semantics |
| `@dataclass` | mutable data with generated `__init__`, `__eq__`, `__repr__` |
| plain class | invariants enforced at construction, private state, invariants across fields |
| `NamedTuple` | immutable tuple-like record you also want to unpack |
| `Protocol` | a structural interface without inheritance |
| `attrs` or `pydantic` | large schemas, validation, or settings (external deps) |

The rule: a plain class earns its keep when an invariant spans several fields and must
hold at all times. If a caller can set one field into an inconsistent state, it is not a
value object.

```python
class Account:
    """Invariant: balance never goes negative, enforced on every mutation."""

    def __init__(self, opening: int = 0) -> None:
        if opening < 0:
            raise ValueError("opening balance must be non-negative")
        self._balance = opening

    @property
    def balance(self) -> int:
        return self._balance

    def withdraw(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if amount > self._balance:
            raise ValueError("insufficient funds")
        self._balance -= amount
```

Plain class fields annotated *without* defaults become required arguments; annotated with
defaults become optional, in declaration order. Fields without annotations are not fields,
they are class attributes and the dataclass machinery ignores them. That silent
omission is a real and confusing failure.

## Comprehensions and generators

```python
# List: builds everything. Set: dedupes. Dict: keys must be hashable.
names = [u.display_name for u in users if u.tags]
unique_tags = {tag for u in users for tag in u.tags}
by_id = {u.id.value: u for u in users}

# Generator: lazy, single pass, no intermediate list.
total = sum(len(line) for line in open("data.txt", encoding="utf-8"))

# Nested loop with a condition, readable.
pairs = [
    (a, b)
    for a in group_a
    for b in group_b
    if a.id == b.id
]
```

Use a generator function for anything that could be a multi-element stream:

```python
from collections.abc import Iterator


def rows_from(path: Path, batch: int = 1000) -> Iterator[dict[str, object]]:
    import json

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_all(source: Iterator[dict[str, object]], sink: Iterator[None]) -> int:
    written = 0
    for row in source:
        next(sink)
        written += 1
    return written
```

Comprehension rules: never put a side effect in one (no `for x in y: log(x)`); avoid
nested comprehension `for` clauses beyond two (rewrite as an explicit loop or
`itertools.product`); and remember a comprehension has its own scope, so variables it binds
do not leak.

`itertools` covers most needs you would otherwise write by hand:

```python
from itertools import chain, groupby, islice, product, accumulate, combinations

pairs = combinations(items, 2)                 # no duplicates, O(n^2) pairs
running = accumulate([1, 2, 3, 4], operator.add)  # 1, 3, 6, 10
flat = chain.from_iterable(list_of_lists)
first_ten = islice(huge_generator, 10)          # does not build 10 million rows
```

`groupby` requires the input to be *already sorted* by the key and only groups
consecutive runs. Sorting first:

```python
from operator import itemgetter

for key, group in groupby(sorted(users, key=itemgetter("department")), key=itemgetter("department")):
    print(key, sum(g["salary"] for g in group))
```

`functools` for callables and caching:

```python
import functools
import operator


@functools.lru_cache(maxsize=256)
def compute_crate_layout(w: int, h: int) -> tuple[int, ...]:
    ...


keyfunc = operator.attrgetter("created_at")
ordered = sorted(users, key=keyfunc, reverse=True)
reduce_example = functools.reduce(operator.add, [1, 2, 3], 0)  # 6
partial_ingest = functools.partial(ingest, source="s3", batch=500)
```

`lru_cache` on a method keeps `self` alive forever (the cache is on the class). For
per-instance caches, cache in an instance attribute or make it a `@staticmethod` taking
only hashable arguments.

## Context managers

`with` is the correct way to hold any resource: files, locks, transactions, temporary
state, timing.

```python
import sqlite3
from contextlib import contextmanager, suppress

with sqlite3.connect("app.db") as conn:   # commits or rolls back, closes
    with conn:                            # transaction scope
        conn.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (10, "u1"))

with suppress(FileNotFoundError):
    Path("optional.txt").unlink()
```

`contextlib.contextmanager` turns a generator function into a context manager. Code before
`yield` runs on entry; code after it runs on exit, including on exception:

```python
@contextmanager
def timer(label: str, sink: list[float]) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        sink.append(time.perf_counter() - start)   # always recorded
```

For a class-based manager, implement `__enter__` and `__exit__`. `__exit__` returning a
truthy value *suppresses* the exception, which is almost never what you want.

## Decorators

```python
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def retry(attempts: int = 3) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last: Exception | None = None
            for _ in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as err:      # narrow this in production code
                    last = err
            raise last if last else RuntimeError("unreachable")

        return wrapper

    return decorate
```

Non-negotiable points:

- Use `functools.wraps`. Without it the decorated function loses `__name__`,
  `__doc__`, and signature introspection, which breaks frameworks, tests, and error output.
- A decorator that does not return the function's value returns `None`. This is the number
  one "my decorator made everything return None" bug.
- Preserve parameters with `ParamSpec` and the return type with `TypeVar`, or accept the
  type degradation explicitly with `Callable[..., Any]`.
- A decorator that changes the signature is much harder to debug than one that does not.
  State parameters as arguments to the decorator factory, not to the wrapper.

```python
# Wrong: signature says (a, b) but wrapper takes (*args, **kwargs)
def bad(fn): return lambda *a, **k: fn(*a, **k)
```

## The GIL and choosing a concurrency model

CPython holds the Global Interpreter Lock: only one thread executes Python bytecode at a
time. It is released around blocking C calls (file I/O, some socket operations, `time.sleep`),
which is why IO-bound thread workloads do scale somewhat. It is *not* released during pure
Python CPU work.

| Workload | Choose | Why |
| --- | --- | --- |
| Network IO, many concurrent requests, one process | `asyncio` | cheap tasks, no per-request thread |
| Blocking library with no async version | `threading` (or `ThreadPoolExecutor`) | simple, and the GIL is released during the blocking call |
| Pure-Python CPU work | `multiprocessing` | separate interpreters, real parallelism |
| NumPy/scipy numeric heavy lifting | `threading` | the GIL is released inside the compiled kernels |
| Mixed | `asyncio` for IO, `ProcessPoolExecutor` for CPU | run CPU work off the event loop |

Never run CPU-bound work directly on the event loop; it blocks every other task. Offload
it:

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor


def checksum(path: str) -> int:
    with open(path, "rb") as handle:
        return sum(handle.read())


async def main(paths: list[str]) -> list[int]:
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor() as pool:
        return await asyncio.gather(*[loop.run_in_executor(pool, checksum, p) for p in paths])
```

Async rules that prevent bugs:

- Coroutines are lazy: calling an `async def` function does not run it. You must `await`
  it, schedule it with `asyncio.create_task`, or hand it to `asyncio.gather`.
- `await` inside a loop serialises the loop. Use `asyncio.gather` (all results, fail fast)
  or `asyncio.wait` with `FIRST_EXCEPTION`, or a semaphore for bounded concurrency.
- Cancellation is cooperative: a task only stops at an `await`. A long synchronous section
  is uncancellable.
- `asyncio.gather(..., return_exceptions=True)` collects outcomes instead of raising on
  the first failure.

```python
import asyncio


async def main(ids: list[str]) -> None:
    sem = asyncio.Semaphore(8)          # bounded concurrency

    async def guarded(item_id: str) -> None:
        async with sem:
            await handle(item_id)

    await asyncio.gather(*(guarded(i) for i in ids))
```

## Exceptions

```python
from __future__ import annotations

import json
from pathlib import Path


# Custom exceptions carry structure the caller can branch on.
class ConfigError(Exception):
    def __init__(self, path: Path, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


def load(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(path, f"cannot read: {err.strerror}") from err

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise ConfigError(path, f"invalid JSON at line {err.lineno}") from err

    if not isinstance(parsed, dict):
        raise ConfigError(path, "top level must be an object")
    return parsed
```

Discipline:

- `raise X from err` (or `raise X from None` to suppress context) preserves the cause.
  Without it, the traceback still shows the original but the causal chain is less clear.
- Catch the narrowest exception you can handle. `except Exception` in a library hides
  programming errors from callers.
- `except:` catches `KeyboardInterrupt` and `SystemExit` too. Never use it.
- Do not use exceptions for normal control flow in hot loops; use `itertools` or early
  returns.
- `finally` for cleanup, `else` on a `try` for code that should run only when no exception
  was raised (keeps the `try` block narrow so you do not swallow errors from your own
  success path).
- EAFP ("easier to ask forgiveness than permission", `try/except KeyError`) beats
  `if key in dict` for dictionary access in the presence of concurrency; LBYL
  ("look before you leap", `if os.path.exists(...)`) races.

```python
try:
    value = config["retries"]
except KeyError:
    value = 3
else:
    log.debug("retries explicitly configured")
```

## Packaging and environments

`pyproject.toml` is the project definition. Build backends are declared with the
`build-system` table; `setuptools` and `hatchling` are common choices, and you pick one
rather than both.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "acme-tools"
version = "0.4.0"
description = "Internal tooling for the Acme platform."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Proprietary" }
dependencies = [
  "httpx>=0.27",
  "pydantic>=2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "mypy>=1.10", "ruff>=0.5"]

[project.scripts]
acme-report = "acme_tools.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/acme_tools"]
```

- `version` is best set dynamically with a tool that reads it from `__init__.py` or from
  version control; the static form above is the fallback.
- An `src/` layout prevents tests from importing the working tree instead of the installed
  package, which is a classic source of "works locally, breaks in CI".
- Python discovers packages with `src/` roots; declare them explicitly as above rather
  than relying on autodiscovery.

Virtual environments and tooling:

```bash
# POSIX
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# Windows PowerShell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Use `python -m pip` rather than bare `pip`: it guarantees the pip from the same interpreter
you are running, which is the whole point of a virtual environment. `python -m venv`
instead of `virtualenv` avoids needing a second tool.

Reproducibility:

- Pin direct dependencies with `>=` lower bounds, and record exact resolved versions in a
  lock file for applications. Which lock tool to use (a tool-specific lock, or
  `pip-tools`/`requirements.txt` with hashes) is a project decision; check the current
  documentation of whichever tool you choose.
- Add `python_requires`-equivalent metadata (`requires-python`) so install fails early on
  the wrong interpreter.
- Run formatting and linting in CI, and set the target version explicitly
  (for example `[tool.mypy] python_version = "3.11"`) so results do not depend on the
  machine.

## Checklist

- [ ] Public functions are annotated; a checker runs in CI, not only in the editor.
- [ ] Value objects are frozen dataclasses; invariants spanning fields use plain classes.
- [ ] No mutable default arguments; `field(default_factory=...)` or `None` plus a check.
- [ ] Every resource is opened with `with`.
- [ ] Decorators use `functools.wraps` and return the wrapped value.
- [ ] Exceptions are narrow, chained with `from`, and never silently swallowed.
- [ ] No CPU-bound work on the event loop.
- [ ] `src/` layout with the package directory declared in the build config.
- [ ] Commands use `python -m ...` so the active environment is the one that runs.
- [ ] `x = None` means "not provided" in a signature default, and is handled explicitly.

## Common mistakes

**`def f(items=[], cache={})`** - one shared mutable object across all calls; mutations
accumulate forever and tests interfere with each other. Use `None` and construct inside,
or `field(default_factory=...)`.

**`RuntimeError: dictionary changed size during iteration`** - you added or removed keys
while iterating. Iterate over `list(d.keys())` or build a new dict.

**A decorator makes the function return `None`.** The wrapper never returned `fn(...)`.
Return the result of the call.

**`AttributeError` on a decorated function's attributes** - missing `functools.wraps`.

**`UnboundLocalError: cannot access local variable 'x'`** - assignment in a function makes
`x` local for the whole function, shadowing any module-level `x`. Add `global x` or
`nonlocal x`, or rename.

**`ValueError` from `dataclass` ordering** - a field without a default follows one with a
default. Reorder, or give the later field a default.

**Dataclass field silently ignored** - it has no type annotation. Fields without
annotations are class attributes, not dataclass fields.

**`TypeError: supersede() takes 2 positional arguments but N were given`** - the old
zero-argument `super()` form versus the `super()` zero-argument form. In a method use
`super()` with no arguments; inside a comprehension or a nested function, pass the class
and instance explicitly: `super(MyClass, self)`.

**Event loop blocked and everything times out** - CPU-bound or synchronous library calls
on the loop. Offload with `run_in_executor` or move to a worker process.

**`ImportError` / wrong package imported after installing** - installing into the system
interpreter instead of the virtual environment, or running from a directory that shadows
the package. Use `python -m pytest` and `python -c "import acme_tools, sys; print(acme_tools.__file__)"`.

**Class body appears to ignore instance attributes** - a mutable class attribute shared by
all instances (a list or dict defined in the class body and mutated in a method).

## Anti-patterns

- **`except Exception: pass`.** It converts bugs into silence. Log and rethrow, or narrow.
- **A mutable class attribute used as per-instance state.**
- **Returning `None` implicitly from a function documented to return a value.**
- **Bare `except:`** catching `KeyboardInterrupt` and `SystemExit`.
- **Importing inside a function on every call** for a module you always need; it hides
  the dependency and costs time. Reserve it for genuine optional imports or breaking
  import cycles.
- **`print` for logging** in a library. Use the `logging` module.
- **Deep inheritance chains** where composition would do; dataclasses and `Protocol` cover
  most needs without a class hierarchy.
- **Optional parameters that silently mean two different things** (`value=None` meaning
  both "not given" and "explicitly none"). Use a sentinel if the distinction matters.
- **Accessing `sys.argv`, `os.environ`, or the network inside a pure function**, which
  makes it untestable. Inject them.
- **String `getattr`/`setattr` gymnastics** where a `TypedDict` or dataclass makes the
  attribute real.

## References

- Python documentation
  - https://docs.python.org/3/
  - https://docs.python.org/3/tutorial/datastructures.html#comprehensions
  - https://docs.python.org/3/reference/datamodel.html
  - https://docs.python.org/3/reference/compound_stmts.html#the-with-statement
  - https://docs.python.org/3/glossary.html#term-global-interpreter-lock
  - https://docs.python.org/3/library/dataclasses.html
  - https://docs.python.org/3/library/contextlib.html
  - https://docs.python.org/3/library/itertools.html
  - https://docs.python.org/3/library/functools.html
  - https://docs.python.org/3/library/typing.html
  - https://docs.python.org/3/library/asyncio.html
  - https://docs.python.org/3/library/multiprocessing.html
  - https://docs.python.org/3/library/venv.html
  - https://docs.python.org/3/tutorial/errors.html
  - https://peps.python.org/pep-0257/ (context managers)
  - https://peps.python.org/pep-0604/ (the `X | Y` union syntax)
- Packaging
  - https://packaging.python.org/en/latest/tutorials/packaging-projects/
  - https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
  - https://peps.python.org/pep-0621/ (project metadata)
- Type checkers
  - https://mypy.readthedocs.io/en/stable/
  - https://microsoft.github.io/pyright/
  - https://typing.python.org/en/latest/spec/ (typing spec)

For deeper reading:

- `references/typing-and-packaging.md` - Protocol vs ABC vs TypeVar bound, generics that
  survive a checker, `pyproject.toml` build backends and layout, dependency groups.
- `references/concurrency-models.md` - GIL detail, executor selection, async patterns,
  process pools and pickling constraints.