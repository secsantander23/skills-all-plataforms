# Tactical DDD Patterns

Every pattern here is optional. Add one when the code asks for it.

## Entity

An object with identity that persists across changes.

```python
from dataclasses import dataclass, field
from uuid import UUID


@dataclass(eq=False)
class Customer:
    customer_id: UUID
    name: str
    tags: set[str] = field(default_factory=set)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Customer) and other.customer_id == self.customer_id

    def __hash__(self) -> int:
        return hash(self.customer_id)
```

`eq=False` plus explicit `__eq__` and `__hash__` gives identity semantics: two customers loaded
from different rows are equal because their ids match, not because their fields do.

## Value object

Immutable, no identity, equality by value.

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailAddress:
    value: str

    def __post_init__(self) -> None:
        if "@" not in self.value or self.value.startswith("@") or self.value.endswith("@"):
            raise ValueError(f"not a valid email address: {self.value}")

    def __str__(self) -> str:
        return self.value
```

Good value object candidates:

- Money, quantities with units, percentages, ratios
- Ranges and intervals (date range, version range, geofence)
- Identifiers by type (OrderId, CustomerId) to stop argument-order bugs
- Email address, phone number, postal code
- Country, currency, locale
- Anything a caller repeatedly has to remember to validate

Candidates to avoid: things that are naturally mutable in the domain, and things that are just
a string with no rules attached. A value object with no validation and no behavior is overhead.

Typed identifiers are worth calling out:

```python
@dataclass(frozen=True, slots=True)
class OrderId:
    value: str


def total_for(order_id: OrderId, repo) -> Money:  # arguments can no longer be swapped
    ...
```

## Aggregate

The consistency boundary. See the SKILL.md for the ordering rules. The mechanical part:

```python
from abc import ABC, abstractmethod

from dataclasses import dataclass, field


@dataclass
class CartItem:
    sku: str
    quantity: int


@dataclass
class Cart:
    cart_id: str
    customer_id: str
    items: list[CartItem] = field(default_factory=list)
    version: int = 0

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    def change_quantity(self, sku: str, quantity: int) -> None:
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        for item in self.items:
            if item.sku == sku:
                item.quantity = quantity
                return
        raise LookupError(f"{sku} is not in the cart")
```

Note what is missing: no repository, no ORM session, no framework annotation. That is the
dependency rule at work, and it is the whole point.

## Repository

```python
from typing import Protocol


class CartRepository(Protocol):
    def get(self, cart_id: str) -> Cart | None: ...
    def save(self, cart: Cart) -> None: ...
```

Use `Protocol` for the domain-side port so the domain defines the contract without importing an
adapter. Then provide an implementation in the infrastructure layer.

Repository rules:

- Per aggregate root.
- Not for reads used to build views. That is the application layer's job.
- No pagination, no sorting preferences, no reporting filters. Those are query concerns.

## Domain service

```python
from dataclasses import dataclass

from domain.money import Money


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    free_shipping_threshold_minor: int
    standard_shipping: Money

    def shipping_for(self, subtotal: Money) -> Money:
        if subtotal.amount_minor >= self.free_shipping_threshold_minor:
            return Money(0, subtotal.currency)
        return self.standard_shipping
```

A domain service that takes a repository, an HTTP client, or the wall clock is an application
service. Put it there.

## Specification

A composable predicate.

```python
from typing import Callable

Predicate = Callable[["Order"], bool]

def has_status(status: str) -> Predicate:
    return lambda order: order.status == status


def min_total(minor: int) -> Predicate:
    return lambda order: order.total.amount_minor >= minor


def all_of(*predicates: Predicate) -> Predicate:
    return lambda item: all(p(item) for p in predicates)
```

Use specifications when a rule needs to be named, reused, and composed. Do not build a
predicate framework for two conditions. In most code bases a plain boolean expression or a named
method on the entity is better.

## Factory

Encapsulating creation when construction is non-trivial.

```python
def order_from_quote(quote: "Quote", customer_id: str) -> "Order":
    order = Order(
        order_id=next_order_number(),
        customer_id=customer_id,
        items=[LineItem(line.sku, line.unit_price, line.quantity) for line in quote.lines],
    )
    order.recalculate_total()
    return order
```

A factory earns its place when the construction rule is a domain rule, when multiple valid
shapes exist, or when creation needs a repository. Otherwise, use the constructor.

## Unit of work

Tracks what changed and commits it once.

```python
class UnitOfWork:
    def __init__(self) -> None:
        self._new: list[object] = []
        self._dirty: list[object] = []

    def register_new(self, entity: object) -> None:
        self._new.append(entity)

    def register_dirty(self, entity: object) -> None:
        self._dirty.append(entity)

    def commit(self) -> None:
        for entity in self._new:
            _persist_insert(entity)
        for entity in self._dirty:
            _persist_update(entity)
        self._new.clear()
        self._dirty.clear()
```

Scope the unit of work to the application layer. It is a transactional concern, and it belongs
to the use case, not to the domain object.

## Which pattern for which problem

| Problem | Pattern |
|---|---|
| Two same-typed arguments getting swapped | Typed identifier value objects |
| Currency mixing bugs | `Money` value object |
| A rule that keeps being bypassed | Move it into an entity method |
| Loading and saving without knowing the store | Repository |
| Logic spanning several aggregates | Domain service |
| A named, reusable rule | Specification |
| Complex valid construction | Factory |
| Commit many changes atomically | Unit of work in the application layer |
| Rules scattered across services | Move them into the model; services call the model |

## Testing the model

The model tests are the fastest tests in the system, and they never touch a database.

```python
import pytest


def test_cannot_cancel_before_start():
    subscription = Subscription(id="s1", customer_id="c1", plan_id="p1", status="active",
                               started_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        subscription.cancel(datetime(2026, 2, 1, tzinfo=timezone.utc))


def test_money_rejects_mixed_currency():
    usd = Money(100, "USD")
    eur = Money(100, "EUR")
    with pytest.raises(ValueError):
        _ = usd + eur
```

If the model needs a database to be tested, the model and the persistence have not been
separated. That is the signal to draw the boundary.