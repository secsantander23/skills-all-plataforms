# Context Mapping and Boundaries in Practice

## Finding the boundaries

Concrete moves, in the order that works.

### 1. Make the implicit explicit

Write down every place two teams use the same noun with different meanings. These are your
boundary candidates, in the order of how much damage they are doing.

| Word | Team A meaning | Team B meaning | Damage |
|---|---|---|---|
| Customer | A buyer with credit terms | A person who opened a ticket | Shared table, contested migrations |
| Account | A login | A contract | Confusing support conversations |
| Order | An intent to buy | A fulfilled shipment | Status fields fight |

### 2. Look at change coupling

Two modules that always change together are probably the same context. Two that never change
together and never need a consistent view are probably separate contexts, even inside one
repository.

### 3. Look at the translation code

A function that is mostly conditionals converting between two shapes is an undeclared boundary.
Every branch is a semantic mismatch someone has papered over. Those branches are where bugs live,
because nobody remembers why the third case exists.

### 4. Look at ownership

Blurry ownership is a signal. If two teams both claim a table and both avoid changing it, the
table is the boundary and neither team owns it.

## Scoring whether to split

| Question | If yes |
|---|---|
| Do the two models need different invariants? | Separate contexts |
| Do they change for different reasons, independently? | Separate contexts |
| Do they need different data shapes or retention? | Separate contexts |
| Can one tolerate the other's inconsistency? | Possibly separate, eventually consistent |
| Do they always need to agree atomically? | Probably one context |

The last row is the one people get wrong. If two things must agree in the same transaction, they
are in the same bounded context. Splitting them and reconciling later is a saga, which is a real
design, not a free choice.

## Data ownership

A context owns its data completely.

| Approach | Description | Cost |
|---|---|---|
| Separate store | Own database or schema | Cross-context joins need an API call |
| Shared read model | Consume events into a read-optimized view | Eventual consistency, more moving parts |
| Shared table, one owner | Others read through an API or view | Owner becomes a bottleneck for schema changes |

What never works: two contexts writing the same table. You get two writers, no shared
invariants, and schema changes that break one side silently.

Reading across a boundary is fine through an API, a view, or a published event. It is only
writing that has to be exclusive.

## Event-driven context integration

```
  Sales context                    Billing context
  +--------------+                 +--------------+
  | Order        |  OrderPlaced   | Invoice      |
  |              | -------------> |              |
  +--------------+   (event)      +--------------+
```

The producer publishes the fact. The consumer maintains its own model. Neither reads the
other's tables.

What this costs, stated honestly:

- The consumer's data is eventually consistent. Write it down.
- Contracts between the events need version management.
- Local transactions no longer span both contexts. Use a saga or accept the inconsistency.
- Debugging spans two stores. You need correlation ids.

## Migrating an existing codebase toward boundaries

Do not rewrite. Draw the boundary where the code already is.

1. Pick one context with real ambiguity. Small.
2. Introduce an interface in front of the shared data. Only that interface knows about the table.
3. Route all reads and writes through the interface.
4. Move the model behind the interface, one rule at a time.
5. Only when the model is clean, consider moving the data.

Steps 1 to 3 are usually enough to stop the bleeding. Step 5 is the expensive one and is often
not needed.

## Anti-corruption layer in detail

An anti-corruption layer sits at a boundary and translates between vocabularies. Its job is to
keep the foreign model's concepts out of yours.

```text
external system                      our model
+--------------+      +-------------+  +--------------+
| CrmContact   | ---> | translation |->| Account      |
| ContactId    |      | + rule map  |  | AccountId    |
| lifecycle[]  |      +-------------+  +--------------+
+--------------+
```

The translation layer holds:

- Name and concept mapping.
- Rule mapping, when the external system enforces a rule differently.
- A place to absorb external weirdness, so the weirdness stops at the boundary.
- Retry, caching, and error handling for the external call.

Test it hard. It is the layer that keeps working quietly while everything above it changes, which
means nobody notices when it breaks until production data is wrong.

## When contexts must stay separate

Split and stay separate when:

- The models are genuinely unrelated and change for unrelated reasons.
- Team ownership is independent.
- The data residency or compliance boundaries differ.
- One side can tolerate staleness.

Merge back, or at least re-evaluate, when:

- Every cross-boundary read needs a join.
- The translation layer grows conditionals faster than either model.
- Teams keep coordinating on the same decision.
- You cannot name a single decision that needs both models to agree atomically.

That last one is the test. If nothing must be atomic across the boundary, the split is earning
its cost. If something must be atomic, you have created a saga and should own it deliberately.