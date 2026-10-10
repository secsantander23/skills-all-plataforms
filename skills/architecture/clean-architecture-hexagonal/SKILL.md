---
name: Clean Architecture and Hexagonal Design
description: Covers the dependency rule, entities and use cases, interface adapters, ports versus adapters, dependency inversion, structuring a codebase so the domain imports no framework, composition roots, architecture tests that enforce import boundaries, and pragmatic adoption without full ceremony. Use when the user asks about clean architecture, hexagonal architecture, ports and adapters, decoupling the domain from a framework, testing the inner layers in isolation, or enforcing layering in a codebase.
metadata:
  opencode/autoinvoke: true
---

# Clean Architecture and Hexagonal Design

One rule does the work: **source-code dependencies point inward.** The domain knows nothing
about the database, the web framework, the message queue, or the ORM. Everything outer calls
inward through an interface the inner code owns.

That gives you two concrete payoffs, and they are the only justification:

1. The inner layers can be tested without a database, a network, or a framework.
2. You can replace an outer adapter without touching the inner code.

Everything else in this document is detail in service of those two.

## When to use this

- Business logic is spread across controllers, ORM models, and stored procedures.
- Unit tests need a database or a mock HTTP server to run.
- A framework upgrade touches business code.
- The domain cannot be read without knowing the framework.
- Multiple entry points (HTTP, a queue, a CLI) need the same logic.

## When not to use it

- A CRUD app with no real rules. A framework and a schema is the right answer.
- A script. Two files, no ceremony.
- A team that will not maintain it. Structure you do not keep is worse than none, because it
  creates friction without providing the payoff.

## The layers

```
   +---------------------------------------------------------------+
   |  Frameworks and drivers                                       |
   |  HTTP routes, CLI, queue consumers, DB driver, GUI            |
   +---------------------------+-----------------------------------+
                               |  implements ports
   +---------------------------v-----------------------------------+
   |  Interface adapters                                            |
   |  controllers, presenters, repository implementations, mappers   |
   +---------------------------+-----------------------------------+
                               |  depends on
   +---------------------------v-----------------------------------+
   |  Application / use cases                                       |
   |  orchestrate the domain, define ports, transactions, boundaries |
   +---------------------------+-----------------------------------+
                               |  depends on
   +---------------------------v-----------------------------------+
   |  Domain / entities                                             |
   |  invariants, value objects, domain events, no I/O              |
   +---------------------------------------------------------------+
```

Two framings of the same idea:

| Clean architecture | Hexagonal |
|---|---|
| Entities | The inside of the hexagon |
| Use cases | The inside of the hexagon |
| Interface adapters | Primary adapters (driving) and secondary adapters (driven) |
| Dependency rule | Ports and adapters |

Hexagonal adds the vocabulary of **ports**: the domain defines the interfaces it needs; the
outside supplies implementations. Driving ports (what the application calls: a use case) and
driven ports (what the application needs from the world: a repository, a publisher) are both
owned by the inner side.

## Ports and adapters

```text
   HTTP request
       |
       v
  [REST controller]  ------------>  <UseCase>  interface
   (primary adapter)                  |
                                      | depends on
                                      v
                                  <OrderRepository>   <EventPublisher>
                                      ^                     ^
                                      |                     |
                          [SQL repository]        [Kafka publisher]
                            (secondary adapter)    (secondary adapter)
```

Rules:

- **Ports are defined in the inner layer, implemented outside.** Not the other way around. A
  repository interface next to the SQL implementation is dependency inversion performed backwards
  and it achieves nothing.
- **The domain owns the repository interface.** The application layer owns the repository
  *implementation* question for infrastructure ports.
- **Adapters translate.** No business logic in a controller, no SQL in the domain.
- **The composition root is the only place that knows the concrete classes.**

## The dependency rule, concretely

Allowed:

- `domain` imports nothing from your project. Standard library only.
- `application` imports `domain`.
- `adapters` imports `application` and `domain`.
- `main` (composition root) imports everything.

Forbidden:

- `domain` importing a framework, an ORM, an HTTP library, or a config module.
- `application` importing a concrete adapter class.
- An adapter imported by a domain module "just for a type hint".

That last one is the common leak. Use a `Protocol` or a plain data structure in the inner layer
and let the adapter map to it.

## Example: a domain with zero imports

```python
# domain/order.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("currency mismatch")
        return Money(self.amount_minor + other.amount_minor, self.currency)


@dataclass
class Order:
    order_id: str
    customer_id: str
    items: list[tuple[str, Money, int]] = field(default_factory=list)
    status: str = "placed"
    version: int = 0

    def add_item(self, sku: str, unit_price: Money, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.items.append((sku, unit_price, quantity))

    @property
    def total(self) -> Money:
        if not self.items:
            raise ValueError("cannot total an empty order")
        result = self.items[0][1]
        for _sku, unit_price, quantity in self.items[1:]:
            result = result + unit_price
        return result


class OrderRepository(Protocol):
    def get(self, order_id: str) -> Order | None: ...
    def save(self, order: Order, expected_version: int) -> None: ...


class EventPublisher(Protocol):
    def publish(self, event_name: str, payload: dict[str, object]) -> None: ...
```

Not one line of that imports anything from your infrastructure. That is the test: you can copy
this file into a different project and it works.

## Application layer

Use cases orchestrate. They load, they call the domain, they decide what to publish, they
define the transaction boundary.

```python
# application/place_order.py
from dataclasses import dataclass

from domain.order import EventPublisher, Money, Order, OrderRepository


@dataclass(frozen=True, slots=True)
class PlaceOrderResult:
    order_id: str
    total: Money


class PlaceOrder:
    def __init__(self, orders: OrderRepository, events: EventPublisher) -> None:
        self._orders = orders
        self._events = events

    def execute(self, customer_id: str, lines: list[tuple[str, Money, int]]) -> PlaceOrderResult:
        order = Order(order_id=new_id(), customer_id=customer_id)
        for sku, unit_price, quantity in lines:
            order.add_item(sku, unit_price, quantity)

        self._orders.save(order, expected_version=0)
        self._events.publish(
            "order_placed",
            {"order_id": order.order_id, "customer_id": customer_id},
        )
        return PlaceOrderResult(order_id=order.order_id, total=order.total)


def new_id() -> str:
    """Injected in production. Kept local so this module imports nothing."""
    from uuid import uuid4

    return uuid4().hex
```

The application layer is where you would inject a clock, an id generator, or a transaction
manager. It is still inner relative to the frameworks.

## Adapters

```python
# adapters/sql_order_repository.py
from domain.order import Money, Order


class SqlOrderRepository:
    def __init__(self, connection) -> None:
        self._connection = connection

    def get(self, order_id: str) -> Order | None:
        row = self._connection.execute(
            "SELECT order_id, customer_id, status, version FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        order = Order(order_id=row["order_id"], customer_id=row["customer_id"], status=row["status"], version=row["version"])
        for item in self._load_items(order_id):
            order.items.append(item)
        return order

    def save(self, order: Order, expected_version: int) -> None:
        # Optimistic concurrency: the write only lands if nobody else moved the row.
        cursor = self._connection.execute(
            "UPDATE orders SET status = ?, version = version + 1 "
            "WHERE order_id = ? AND version = ?",
            (order.status, order.order_id, expected_version),
        )
        if cursor.rowcount == 0:
            raise ConcurrentModification(order.order_id)

    def _load_items(self, order_id: str) -> list[tuple[str, Money, int]]:
        rows = self._connection.execute(
            "SELECT sku, amount_minor, currency, quantity FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchall()
        return [(r["sku"], Money(r["amount_minor"], r["currency"]), r["quantity"]) for r in rows]


class ConcurrentModification(Exception):
    pass
```

The adapter knows about SQL. The domain does not. `ConcurrentModification` is raised by the
adapter and caught by the application layer, which decides whether to retry.

## Composition root

One place where concrete classes are chosen and wired.

```python
# main.py
import os

from adapters.sql_order_repository import SqlOrderRepository
from application.place_order import PlaceOrder


def build_place_order(connection) -> PlaceOrder:
    orders = SqlOrderRepository(connection)
    events = InMemoryEventPublisher()  # or a broker-backed publisher
    return PlaceOrder(orders=orders, events=events)


if __name__ == "__main__":
    connection = open_database(os.environ["DATABASE_URL"])
    place_order = build_place_order(connection)
```

Everything else receives its dependencies through the constructor. No service locator, no global
registry, no import-time side effects.

## Testing the inner layers

The payoff. The domain and use cases test with in-memory fakes and no framework.

```python
from domain.order import EventPublisher, Money, Order, OrderRepository


class FakeOrderRepository(OrderRepository):
    def __init__(self) -> None:
        self.saved: list[Order] = []

    def get(self, order_id: str) -> Order | None:
        return next((o for o in self.saved if o.order_id == order_id), None)

    def save(self, order: Order, expected_version: int) -> None:
        self.saved.append(order)


class RecordingPublisher(EventPublisher):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def publish(self, event_name: str, payload: dict[str, object]) -> None:
        self.events.append((event_name, payload))


def test_placing_an_order_records_it_and_announces_it():
    repository = FakeOrderRepository()
    publisher = RecordingPublisher()
    use_case = PlaceOrder(repository, publisher)

    result = use_case.execute("c-1", [("sku-1", Money(1000, "EUR"), 2)])

    assert len(repository.saved) == 1
    assert publisher.events[0][0] == "order_placed"
    assert result.total == Money(2000, "EUR")
```

No database, no fixtures, no framework, no mocks library. If your unit tests are slow, this is
usually the reason and the cure.

## Enforcing the rule with a test

Structure that is only maintained by good intentions decays within a quarter. An architecture
test makes the rule executable.

```python
# tests/test_architecture.py
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
LAYERS = ["domain", "application", "adapters"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_domain_layer_imports_nothing_from_the_project() -> None:
    violations: list[str] = []
    for path in (SRC / "domain").rglob("*.py"):
        for module in _imports(path):
            if any(module.startswith(layer) for layer in LAYERS):
                violations.append(f"{path.name} imports {module}")
    assert not violations, "dependency rule violated: " + ", ".join(violations)


def test_application_layer_does_not_import_adapters() -> None:
    violations: list[str] = []
    for path in (SRC / "application").rglob("*.py"):
        for module in _imports(path):
            if module.startswith("adapters"):
                violations.append(f"{path.name} imports {module}")
    assert not violations, "dependency rule violated: " + ", ".join(violations)
```

Run it in CI. It is about thirty lines and it is the difference between an architecture and a
diagram.

Your language and tooling may already offer this. Many ecosystems have import-linting tools
configured with layer allowlists; check what your project already has before adding your own.

## Directory layout

```
src/
  domain/            # entities, value objects, domain events, repository interfaces
  application/       # use cases, DTOs, ports for infrastructure
  adapters/
    http/            # controllers, request and response schemas
    persistence/     # repository implementations
    messaging/       # publisher and consumer implementations
  main.py            # composition root
tests/
  unit/              # domain and application, no infrastructure
  integration/       # adapters against real or containerised infrastructure
```

Optional within this: `entities`, `use_cases`, `interface_adapters`, `infrastructure`. The
number of packages is not the point. The import direction is.

## Dependency inversion versus dependency injection

Dependency inversion is a design principle: high-level policy should not depend on low-level
detail, and both should depend on abstractions the high level owns.

Dependency injection is a mechanism for achieving it.

Teams get this backwards and end up injecting a repository into the domain and calling it clean
architecture. The test is not "was it injected", it is "can the domain be loaded and tested with
the adapters deleted". If deleting the adapters directory breaks the domain tests, the inversion
never happened.

## Pragmatic adoption

You do not need the full ceremony. In order of value:

1. **Stop the framework in the domain.** Move business rules out of controllers and ORM models
   into plain classes. This alone gets you most of the benefit.
2. **Define one port for the thing that is hardest to fake.** Usually the repository.
3. **Move one use case out of the controller.** Let it orchestrate instead.
4. **Add a composition root.** Stop constructing dependencies inline.
5. **Add the architecture test.** Now the structure holds.
6. **Introduce more ports only where a second implementation exists or a test demands it.**

Adopt this inside a **modular monolith** first. Modules with enforced boundaries give you most
of the value of services with none of the network. Extract a module into a service when its
scaling or its team boundary actually requires it.

## Common mistakes

- **Ports defined next to the adapters.** Symptom: nothing is actually inverted; the inner layer
  still imports the outer. Fix: the interface lives in `domain` or `application`.
- **Framework types in the domain.** Symptom: a model with ORM annotations cannot be constructed
  in a test. Fix: map at the adapter boundary.
- **DTOs everywhere.** Symptom: five classes for one order. Fix: use plain dataclasses and
  share them across layers when they genuinely represent the same thing.
- **Every class gets its own interface.** Symptom: a file of one-method interfaces. Fix: ports
  at boundaries, not at every class.
- **Repository interface per entity.** Fix: one per aggregate, as in DDD.
- **Architecture test that only checks one rule.** Fix: assert the whole direction, including
  that adapters may import inward.
- **Anemic inner layer.** Symptom: use cases are one-line wrappers and all logic is in the
  controller. Fix: move the logic in.
- **No composition root.** Symptom: modules construct their own dependencies, so nothing is
  swappable. Fix: build everything in one place.

## Anti-patterns

- **Clean architecture as folder ceremony with dependencies flowing outward.** Looks structured,
  is not.
- **Repository interfaces for read models.** Reads belong to the application layer, with query
  objects, not to a domain repository.
- **A single "domain" package with 4000 lines and 200 concepts.** Split by subdomain, not by
  layer count.
- **Passing ORM models across the boundary.** They are adapter types.
- **Using events to avoid writing a use case.** In-process domain events inside one transaction
  are fine. Async messaging for a synchronous rule is not.
- **Adopting the architecture without changing any code.** A new folder tree around the same
  tangled logic.
- **One adapter per interface from day one.** Write the second adapter when the second
  requirement appears.
- **Treating the diagram as the deliverable.** The executable test is the deliverable.

## Checklist

- The domain package imports nothing but the standard library and its own modules.
- Ports are declared inside, implemented outside.
- Controllers contain no business logic.
- Adapters translate and do not decide.
- There is one composition root per runnable thing.
- Tests for the domain and use cases run with no database and no framework.
- An architecture test runs in CI and asserts the import direction.
- Cross-boundary data is mapped, not shared as adapter types.
- Each port exists because something needs to replace or observe it.

## References

- Robert C. Martin, original Clean Architecture post: https://blog.cleancoder.com/2012/11/14/the-clean-architecture.html
- Alistair Cockburn, Hexagonal Architecture: https://alistair.cockburn.us/hexagonal-architecture/
- Microsoft guidance on clean architecture layers: https://learn.microsoft.com/azure/architecture/microservices/model/tactical-ddd
- Ports and adapters, Alistair Cockburn: https://alistair.cockburn.us/ports-and-adapters/
- Hexagonal architecture article series: https://alistair.cockburn.us/
- Dependency Inversion Principle, Robert C. Martin: https://web.archive.org/web/20151112163415/http://www.oodesign.com/article/dependency-inversion-principle
- Robert C. Martin's Clean Architecture book page: https://www.oreilly.com/library/view/clean-architecture/9780134494166/
- Vasu Kunda, Practical Test Pyramid, publisher page: https://www.oreilly.com/library/view/practical-test-pyramid/9781803276070/
- pytest documentation: https://docs.pytest.org/