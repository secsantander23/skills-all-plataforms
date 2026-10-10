---
name: Domain-Driven Design
description: Covers bounded contexts, ubiquitous language, aggregates and invariants, entities versus value objects, domain services, repositories, domain events, context mapping with anti-corruption layers, subdomain classification, and pragmatic adoption including when DDD is overkill. Use when the user asks about modelling a complex domain, splitting a bloated model, defining aggregate boundaries, naming things in code, integrating with a legacy or external system, or diagnosing a model that has become tangled.
metadata:
  opencode/autoinvoke: true
---

# Domain-Driven Design

DDD is a way of putting the business model in the code, so that a change in the business is a
change in the model rather than a change scattered through services. It earns its cost in
complex domains. In a simple one it is overhead, and pretending otherwise is how teams end up
with ceremony they cannot maintain.

The two halves are:

- **Strategic** — where are the boundaries between parts of the business, and how do they
  relate. This is the part that pays.
- **Tactical** — entity, value object, aggregate, repository, domain service. This is the part
  people pattern-match on, and it is the part that gets cargo-culted.

## When to use this

- The domain has real rules, and those rules are leaking into controllers, jobs, and SQL.
- The same word means different things to different teams, and that causes bugs.
- A model class has grown past a hundred fields and a hundred reasons to change.
- You are integrating with an external system whose model is not yours to change.
- Changes to one part of the system keep breaking an unrelated part.
- You want to know where to put a piece of logic so it has exactly one home.

## Ubiquitous language

Ubiquitous language is a shared, precise vocabulary between domain experts and developers, used
identically in conversation, in documentation, and in code.

Test it directly: read a method name out loud to a domain expert. If they do not recognize the
words, or if the words mean something else, the model is wrong.

| Smell | Fix |
|---|---|
| `data`, `info`, `item`, `record`, `obj` | Name the concept |
| `isActive` on three classes meaning three things | Three names, or three contexts |
| `type: int` with a comment explaining the codes | Named type or enum with domain names |
| `getUserData()` | A verb and a noun: `getActiveSubscriptions()` |
| Class `OrderStatusEnum2` | Work out why you needed a second one |

Practical steps:

- Write a glossary of the nouns and the verbs, with definitions and one clarifying example each.
- Keep it next to the code and update it with the model.
- Name things identically across code, database, UI, and API. `Subscription` in the UI and
  `CustomerPlan` in the database produces translation layers that nobody understands.
- Delete a concept when it stops being meaningful. Vestigial vocabulary is a symptom of a model
  that has not kept up.

## Bounded contexts

A bounded context is a region of the model where a particular meaning of a word is valid and the
model is consistent. Outside the boundary, that meaning does not apply.

The classic demonstration: `Customer` in Sales means a lead-to-cash buyer with credit terms. In
Support it means a person with tickets. In Billing it means a payer with payment methods. Trying
to make one `Customer` serve all three produces a class with credit limit, ticket history, and
payment tokens, and a reason for every team to change it.

```
  Sales context        Support context        Billing context
  +---------------+     +---------------+     +---------------+
  | Customer      |     | Customer      |     | Payer         |
  |  credit_limit |     |  tickets[]    |     |  methods[]    |
  |  tier         |     |  satisfaction |     |  invoices[]   |
  +---------------+     +---------------+     +---------------+
        |                      |                     |
        +---- distinct models, distinct tables, distinct services ----+
```

Practical consequences of a boundary:

- Each context owns its model and its data. Sharing a database table across contexts couples
  them invisibly.
- The same physical table can back two contexts, but only one owns the schema.
- A term is only ambiguous across a boundary. Inside a context, `Customer` means one thing.

How to find boundaries:

1. List the domain nouns and group them by who uses them.
2. Look for the same word with different definitions, or different words for the same thing.
3. Look for code that changes together with code in a distant part of the model.
4. Look for where a team's ownership is fuzzy.
5. Look for where a translation function is full of conditionals. That is a boundary nobody
   declared.

## Subdomains

Classify each area by how much is worth investing in it.

| Type | Definition | Investment |
|---|---|---|
| Core | The reason your product exists | Model it deeply, own it |
| Supporting | Necessary, but not differentiating | Model it, do not over-invest |
| Generic | Someone else solves it well | Buy or adopt |

An explicit decision that a subdomain is generic is permission not to build it. Teams that
skip this classification build a custom inventory system and then wonder why the roadmap is
stuck.

## Aggregates and invariants

An **invariant** is a rule that must always be true. An **aggregate** is the smallest
consistency boundary that can enforce a set of invariants.

The design question is not "what does this object contain" but "which rules must hold together
atomically". The answer is the aggregate.

Ordering rules for an aggregate:

```python
from dataclasses import dataclass, field


@dataclass
class LineItem:
    sku: str
    unit_price: "Money"
    quantity: int


@dataclass
class Order:  # aggregate root
    order_id: str
    customer_id: str  # referenced by identity, not by object
    items: list[LineItem] = field(default_factory=list)
    version: int = 0  # optimistic concurrency on the root

    def add_item(self, sku: str, unit_price: "Money", quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.items.append(LineItem(sku, unit_price, quantity))

    @property
    def total(self) -> "Money":
        if not self.items:
            raise ValueError("cannot total an empty order")
        result = self.items[0].unit_price * self.items[0].quantity
        for item in self.items[1:]:
            result = result + item.unit_price * item.quantity
        return result
```

- **One aggregate per transaction.** Changing two aggregates in one transaction means they were
  probably one aggregate, or you need a saga.
- **Reference other aggregates by identity, not by object.** Holding a `Customer` instance inside
  an `Order` means loading it and risking a stale, conflicting in-memory copy.
- **The root owns access.** Call methods on the root, not on the children it holds. A setter that
  can break an invariant from outside is a missing method.
- **Smallest consistent boundary.** A large aggregate holds more locks and more contention, and
  every update rewrites more rows.
- **Cross-aggregate invariants need a different mechanism**: a process manager, a saga, a
  database constraint that spans both, or an eventual repair job.

Contradiction detection across aggregates happens eventually. Design for it rather than pretending
it does not.

### Concurrency on aggregates

Use optimistic concurrency: the aggregate root carries a version, the update is conditional on
it, and a failed version check means someone else changed the aggregate.

```sql
UPDATE orders
SET status = 'paid', version = version + 1
WHERE id = :order_id AND version = :expected_version;
```

Zero rows updated means a concurrent modification. Decide what happens: reload and retry, or
surface the conflict. Never silently overwrite.

## Entity versus value object

| | Entity | Value object |
|---|---|---|
| Identity | Has one, across time | None |
| Equality | By identity | By all attributes |
| Mutability | Usually mutable | Immutable |
| Lifecycle | Created, changed, sometimes deleted | Created, used, discarded |

```python
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if len(self.currency) != 3:
            raise ValueError("currency must be an ISO 4217 alphabetic code")

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"cannot add {self.currency} to {other.currency}")
        return Money(self.amount_minor + other.amount_minor, self.currency)


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("end must not precede start")

    def contains(self, moment: date) -> bool:
        return self.start <= moment <= self.end
```

Value objects are the highest-leverage tactical pattern and the least risky to adopt. They push
validation into construction, remove entire categories of bug, and make equality correct by
default. `Money` above makes a currency-mixing bug unrepresentable rather than merely
discouraged.

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Subscription:
    id: str
    customer_id: str
    plan_id: str
    status: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def cancel(self, at: datetime) -> None:
        if self.status == "cancelled":
            return  # already cancelled; cancelling twice is not an error
        if at < self.started_at:
            raise ValueError("cannot cancel before the subscription started")
        self.status = "cancelled"
```

## Domain services

Some logic belongs to no single entity: `Money` conversion, pricing across a basket, a policy
that spans two aggregates. Put it in a **domain service**, stateless, named as a verb phrase,
with no persistence and no infrastructure.

The test for a domain service: it operates on domain concepts only. If it needs a database
handle, an HTTP client, or the current time, it is not a domain service, it is an application
service.

## Repositories

A repository is a collection-like abstraction over aggregates. It exists so the domain can load
and save without knowing the storage.

Rules:

- Repositories are for **aggregates**, not for tables, and not for reading.
- No repository method that encodes a query concern ("find the top 10 by revenue"). That is the
  application's job, using whatever store it likes.
- The interface lives with the domain, the implementation with the infrastructure.
- One repository per aggregate root. Not one per entity.

```python
from abc import ABC, abstractmethod


class OrderRepository(ABC):
    @abstractmethod
    def get(self, order_id: str) -> "Order | None": ...

    @abstractmethod
    def save(self, order: "Order") -> None: ...

    @abstractmethod
    def next_order_number(self) -> str: ...
```

The last method is included deliberately. Sequence generation is a domain rule about how order
numbers work, not an implementation detail of the database, and hiding it behind an auto-increment
makes the rule unexpressible and unreportable.

## Domain events

A domain event records a fact that already happened, in the language of the domain, in the past
tense.

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    order_id: str
    customer_id: str
    total: "Money"
    occurred_at: datetime
```

Naming rules:

- Past tense, and a fact: `OrderPlaced`, `SubscriptionCancelled`. Not `PlaceOrderCommand` or
  `OrderEvent`.
- Past tense because it already happened and cannot be refused.
- Include the identifiers a consumer needs, and nothing it does not. An event carrying the whole
  aggregate invites every consumer to depend on your internals.

The distinction that gets confused:

| | Command | Event |
|---|---|---|
| Tense | Imperative | Past |
| Can be refused | Yes | No |
| Who produces | The caller | The thing that happened |
| Meaning | An intention | A fact |

## Context mapping

When two contexts integrate, you are choosing a relationship. Name it deliberately.

| Relationship | Meaning | Use when |
|---|---|---|
| Conformist | Downstream adopts upstream's model | Upstream is stable and you have no better option |
| Anti-corruption layer | You translate to protect your model | Upstream is complex, unstable, or hostile |
| Customer-supplier | Upstream commits to the consumer | Formal collaboration exists |
| Open host service | You expose a stable published API | Many consumers |
| Published language | A shared, documented interchange format | Multiple consumers |
| Separate ways | Both translate to their own third format | Neither will accommodate the other |

An anti-corruption layer is a translating model that exists only at the boundary. It is the
single most valuable tactical pattern for integrations, and the cheapest one to adopt later.

```text
  External CRM (Customer, Deal, Activity)
            |
            v
  +-----------------------+
  | anti-corruption layer |   CRMCustomer  ->  Account
  | (our vocabulary)     |   CrmDeal       ->  Opportunity
  +-----------+-----------+
              |
              v
        Our domain model
```

The layer's job is to make the foreign vocabulary stop at the boundary. If your domain objects
start growing fields named after the external system's concepts, the layer has leaked.

## Data model and the ORM

If you persist the domain model directly, be explicit about the coupling:

- Identity map: one object per identity in memory, so identity-based equality holds.
- Lazy loading inside aggregates only; never across a boundary.
- Map relationships, not associations. A `@ManyToOne` in the domain is a design decision, not a
  convenience.
- Be honest about when you split the model. A pragmatic middle: map the aggregate root to a
  table, store value objects as columns or as child tables, and let the application layer own
  reads and cross-aggregate queries.

The middle is what most teams should ship. Full domain-persistence purity is a large commitment
and rarely the highest-value work available.

## Pragmatic adoption

Start here and stop when you have what you need:

1. **A glossary.** Cheap, immediate, and it forces the conversation.
2. **Value objects for the rules that keep being violated.** Currency, units, ranges, identifiers.
3. **Invariants into methods, not into controllers.** The rule lives in one place.
4. **One aggregate root per business concept**, with an explicit consistency boundary.
5. **Domain events at the places where two teams already disagree.**
6. **Bounded contexts only where the ambiguity is real.**

That is a large fraction of the benefit at a fraction of the cost. Add repositories, factories,
and specifications only when the code asks for them.

## When DDD is overkill

| Situation | Better |
|---|---|
| The app is CRUD over a form | A framework and a schema |
| Business rules are simple and stable | Keep them in the application layer |
| One team, one bounded context, weeks old | Do not introduce the vocabulary ceremony |
| Reporting over a large dataset | A warehouse and SQL |
| The complexity is technical, not business | Normal architecture and good engineering |
| Integration-heavy, domain-light | Anti-corruption layers and clean architecture |

The test: if the difficulty is in the *rules*, model the rules. If the difficulty is in
*distribution, latency, or data volume*, it is a systems problem, and DDD will not touch it.

## Common mistakes

- **One `User` class for every team.** Symptom: it accumulates roles from three contexts and
  every team fights over it. Fix: separate models, translate at the boundary.
- **Aggregate too large.** Symptom: unrelated updates conflict, and contention shows up as
  retries. Fix: shrink to the smallest consistency boundary that holds the invariants.
- **Entity with no identity check.** Symptom: two aggregates each think they own the same row.
  Fix: optimistic concurrency on the root version, always.
- **Repository used for reads.** Symptom: report queries leak into the domain interface. Fix:
  read models belong to the application layer.
- **Value object made mutable.** Symptom: a hash-set lookup misses because the hash changed. Fix:
  frozen and replaced, not mutated.
- **Events named as commands.** Symptom: consumers retry things that already happened. Fix:
  past tense, no retry semantics inside the event.
- **Anti-corruption layer that leaks.** Symptom: `CrmContactId` appears in your aggregates. Fix:
  translate fully at the boundary.
- **Ubiquitous language ignored for "code reasons".** Symptom: `ProcessingFlag` and
  `PendingStatus2`. Fix: the code reasons are the smell.

## Anti-patterns

- **Applying DDD to a simple domain.** Ceremony without payoff, and it burns the team's belief in
  the method for when it would help.
- **Domain model with setters that break invariants.** The encapsulation is cosmetic.
- **Domain services doing I/O.** Anemic domain plus a service layer with all the logic, which is
  the transaction script pattern with extra vocabulary.
- **One context per table, or one context per team, by default.** Boundaries follow the model.
- **Repository per entity.** Persistence abstractions should mirror the consistency boundary.
- **Model objects with framework annotations on everything.** The model now depends on the
  framework, so you cannot load it without one.
- **Deep object graphs returned to callers.** Callers mutate aggregates behind your invariants.
  Return ids or read models.
- **Modeling the database and calling it DDD.** A schema with one class per table and no
  invariants is an ORM tutorial, not a domain model.

## Checklist

- A glossary exists and matches the code names.
- Every ambiguous term has been resolved into named contexts.
- Each context owns its model and its data.
- Subdomains are classified as core, supporting, or generic.
- Every aggregate root has a named invariant and a concurrency strategy.
- Cross-aggregate invariants have a named mechanism.
- Value objects are immutable and validate on construction.
- Repositories exist for aggregate roots only.
- Domain events are past tense facts carrying only what consumers need.
- Every external integration has a named context mapping relationship.
- I/O is in the application layer, not the domain layer.

## References

- Eric Evans, Domain-Driven Design, publisher page: https://www.domainlanguage.com/
- Eric Evans, Domain-Driven Design Quickly: https://www.infoq.com/articles/domain-driven-design-quickly/
- DDD reference site: https://domainlanguage.com/
- Microsoft guidance on DDD aggregates: https://learn.microsoft.com/azure/architecture/microservices/model/tactical-ddd
- Microsoft guidance on domain modeling patterns: https://learn.microsoft.com/azure/architecture/microservices/model
- Microsoft DDD microservice patterns: https://learn.microsoft.com/azure/architecture/microservices/model/tactical-ddd
- Martin Fowler, Anemic Domain Model: https://martinfowler.com/bliki/AnemicDomainModel.html
- Martin Fowler, Domain Model: https://martinfowler.com/bliki/DomainModel.html
- Martin Fowler, Repository: https://martinfowler.com/eaaCatalog/Repository.html
- Martin Fowler, Unit of Work: https://martinfowler.com/eaaCatalog/UnitOfWork.html
- Vaughn Vernon, Implementing Domain-Driven Design, publisher page: https://www.oreilly.com/library/view/implementing-domain-driven/9780138544425/