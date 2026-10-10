# Typing and Packaging Depth

## Choosing the right typing tool

| Tool | Use when |
| --- | --- |
| `Protocol` | structural interface; you do not control the implementation |
| `ABC` + `@abstractmethod` | you control both sides and want shared behaviour |
| `TypeVar` | you need the caller to supply a type and want it preserved |
| `TypedDict` | a dict with a known key set crossing a boundary |
| `NewType` | a distinct type over an existing one, checked only statically |
| `NamedTuple` | immutable record you want to unpack |
| `Enum` | a closed set of values with identity |
| dataclass | a value with data and derived methods |
| `Literal` | a parameter that must be one specific string/number |

### Protocol

```python
from typing import Protocol, runtime_checkable


class SupportsWrite(Protocol):
    def write(self, data: bytes) -> int: ...


def dump(obj: object, sink: SupportsWrite) -> None:
    sink.write(repr(obj).encode())


with open("out.txt", "wb") as handle:
    dump({"a": 1}, handle)   # a real file satisfies the protocol
```

A protocol type is satisfied structurally; `runtime_checkable` only enables
`isinstance` checks, and only for *attribute* protocols (not ones with non-method members
in some checker versions). Verify behaviour before relying on it.

### NewType versus a dataclass

```python
from typing import NewType

UserId = NewType("UserId", str)


def greet(uid: UserId) -> str:
    return f"hello {uid}"


uid = UserId("u_1")     # only a function call; no runtime cost, no validation
bad: UserId = "u_1"     # type error: str is not UserId
```

NewType is a *static* distinction only; nothing validates at runtime. If the difference
matters at runtime (a non-empty string), use a frozen dataclass with `__post_init__`
validation, as in the main skill.

### Generic functions

```python
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")
E = TypeVar("E", bound=Exception)


def first_error(errors: Iterable[E], handler: Callable[[E], None]) -> E | None:
    for err in errors:
        if isinstance(err, ValueError):   # narrowing inside a generic function
            handler(err)
            return err
    return None
```

Bound `TypeVar` constraints are enforced at the call site. Prefer `collections.abc`
(`Iterable`, `Sequence`, `Mapping`, `Callable`, `Iterator`) over `typing` equivalents; the
`typing` names still work and remain widely used.

### Variance

`Iterable[T]` is covariant in `T`, so `Iterable[bool]` is a subtype of `Iterable[object]`
and you cannot put arbitrary objects into it. `list[T]` is invariant: `list[bool]` is not
a `list[object]`. This is why a `list[Any]` or `list[object]` variable cannot be appended
to with a specific type without a checker complaint. Accept `Sequence[T]` in parameters
(read-only intent) and return `list[T]` when you construct.

## Checker configuration

`pyproject.toml` sections accepted by the two mainstream checkers:

```toml
[tool.mypy]
python_version = "3.12"
files = ["src", "tests"]
strict = true
disallow_any_unimported = true
enable_error_code = ["ignore-without-code"]

[[tool.mypy.overrides]]
module = ["pandas.*", "yaml.*"]
ignore_missing_imports = true

[tool.pyright]
include = ["src"]
pythonVersion = "3.12"
typeCheckingMode = "strict"
reportMissingTypeStubs = false
```

Points worth knowing:

- Strict mode turns on the bulk of optional annotations: missing parameter types,
  missing return types, `Any` leaking through, unsafe equality.
- `ignore_missing_imports` per module is the pragmatic escape for untyped dependencies.
  A blanket `ignore_missing_imports = true` disables checking of your own code's imports
  too and should be reserved for throwaway scripts.
- Setting `python_version` (mypy) or `pythonVersion` (pyright) to the project's minimum
  version makes results reproducible across developer machines.
- For third-party stubs, check whether the library ships `py.typed`; if not, treat its
  types as `Any` rather than assuming a shape.

Running in CI:

```bash
python -m mypy src tests
python -m pytest
```

Run from a clean environment, the same environment the package is installed into, so the
checker sees the same `typing` extensions the package depends on.

## Build backends

`pyproject.toml` `[build-system]` selects the backend. Common choices:

| Backend | Notes |
| --- | --- |
| `hatchling` | good defaults, config in `[tool.hatch.*]`, fast builds |
| `setuptools` | the long-standing default; explicit `package-dir`/`packages` usually needed |
| `flit_core` | minimal metadata, requires the module docstring and `__version__` in `__init__.py` |
| `poetry-core` | use when the project is Poetry-managed |

Minimal hatchling configuration:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "acme-tools"
version = "0.4.0"
description = "Internal tooling"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27"]

[tool.hatch.version]
path = "src/acme_tools/__about__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/acme_tools"]
```

`[project.scripts]` creates console entry points. The value is the
`module:function` import path, not a file path.

### Layout

```text
acme-tools/
  pyproject.toml
  README.md
  src/
    acme_tools/
      __init__.py
      __about__.py
      cli.py
      core.py
  tests/
    test_core.py
```

The `src/` layout matters: with a flat layout, running `pytest` from the repository root
puts the working tree on `sys.path`, and tests import the source directory instead of the
installed package. That hides packaging errors (missing files in the wheel, wrong package
name) until release.

### Editable installs

```bash
python -m pip install -e ".[dev]"
```

Editable installs with a `src/` layout rebuild the mapping when files move; some backends
cache the file list, so a fresh editable install after restructuring is the reliable fix.

## Dependency groups and locks

Modern packaging distinguishes:

- `[project.dependencies]` - runtime requirements of the published package.
- `[project.optional-dependencies]` - published extras, installable as `name[extra]`.
- A lock file - the fully resolved, exact versions used for a reproducible install.

Which mechanism a project uses for dev dependencies (extras, a requirements file, or a
tool-specific group table) is a project choice; check the current packaging guide and your
build tool's docs before assuming a particular key. The rule that does not change: the
published metadata must never include dev-only tools.

Verify against the packaging guide rather than copying a config from memory:

- https://packaging.python.org/en/latest/
- https://packaging.python.org/en/latest/specifications/