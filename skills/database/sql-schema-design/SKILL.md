---
name: Relational Schema Design
description: Covers normalization versus denormalization, surrogate versus natural primary keys, foreign keys and referential actions, data type and precision choices, NULL semantics, CHECK constraints, table splitting, temporal and effective-dated modeling, and the tradeoffs of soft deletes. Use when the user asks how to model a table or constraint, when choosing key types, when debugging NULL or precision bugs, when a foreign key cascade deleted too much, or when designing audit history and history tables.
metadata:
  opencode/autoinvoke: true
---

# Relational Schema Design

A schema is the part of your system you cannot cheaply change. Application code is written
every day; a renamed column that 40 services depend on is a quarter of work. This skill is
about decisions that are expensive to reverse, and the failure modes behind each one.

## When to use this

- Designing a new table, or reviewing a schema change someone else proposed.
- Choosing a primary key type and deciding whether the key is surrogate or natural.
- Deciding what happens when a parent row is deleted.
- Debugging a `NOT NULL`, `NULL`, or numeric precision surprise.
- Storing history, effective dates, or slowly changing dimensions.
- Adding a soft-delete column, or regretting one.
- Modelling money, timestamps, or identifiers that come from outside systems.

## Quick start

A table with the decisions made explicitly rather than by default.

```sql
CREATE TABLE accounts (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_ref    text NOT NULL,
    email           text NOT NULL,
    balance_cents   bigint NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    currency        char(3) NOT NULL DEFAULT 'USD',
    status          text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'suspended', 'closed')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT accounts_email_key UNIQUE (email),
    CONSTRAINT accounts_external_ref_key UNIQUE (external_ref)
);

CREATE INDEX accounts_status_created_idx ON accounts (created_at DESC);

CREATE TABLE ledger_entries (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      bigint NOT NULL
                        REFERENCES accounts (id) ON DELETE RESTRICT,
    amount_cents    bigint NOT NULL CHECK (amount_cents <> 0),
    currency        char(3) NOT NULL,
    effective_at    timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ledger_entries_account_effective_idx
    ON ledger_entries (account_id, effective_at DESC);
```

Every line above is a decision: money in integers, timestamps with a zone, status constrained
to a known set, parent deletion restricted rather than cascaded.

## Normalization and denormalization

Normalization removes redundancy so that each fact lives in one place. It is correct by
default, and the cost shows up in joins.

| Form | Rule | Practical meaning |
| --- | --- | --- |
| 1NF | Atomic values | No `tags` column holding a comma-separated list. |
| 2NF | No partial dependency on a composite key | Every non-key column depends on the whole key. |
| 3NF | No transitive dependency | Non-key columns depend on the key, not on other non-key columns. |
| BCNF | Every determinant is a candidate key | The stricter, better-behaved variant of 3NF. |

Denormalize deliberately, in this order of increasing risk:

1. **Add an index that already covers it.** An `INCLUDE` covering index or a well-ordered
   composite index removes the join cost without changing the model.
2. **Add a stored generated column.** Computed once at write time, guaranteed consistent.
3. **Add a materialized view.** Refreshable on demand or on a schedule; the base tables stay
   normalized.
4. **Duplicate a counter column.** Requires you to maintain it. Make it rebuildable.
5. **Duplicate a whole nested structure.** Only for read paths that are demonstrably hot and
   that you are prepared to reconcile.

```sql
-- Generated column: always consistent, zero application logic.
CREATE TABLE invoices (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subtotal_cents  bigint NOT NULL,
    tax_cents       bigint NOT NULL,
    shipping_cents  bigint NOT NULL DEFAULT 0,
    total_cents     bigint NOT NULL
        GENERATED ALWAYS AS (subtotal_cents + tax_cents + shipping_cents) STORED
);

-- Materialized view: consistent, but only as fresh as the last refresh.
CREATE MATERIALIZED VIEW account_balances AS
SELECT a.id AS account_id,
       COALESCE(SUM(l.amount_cents), 0) AS balance_cents
FROM accounts a
LEFT JOIN ledger_entries l ON l.account_id = a.id
GROUP BY a.id;

CREATE UNIQUE INDEX account_balances_account_id_idx
    ON account_balances (account_id);

REFRESH MATERIALIZED VIEW account_balances;
```

`REFRESH MATERIALIZED VIEW CONCURRENTLY` needs a unique index on the view and does not take a
full lock; it cannot run inside a transaction block.

## Primary key strategy

| Choice | Pros | Cons |
| --- | --- | --- |
| Surrogate (`bigint` identity or sequence) | Small, stable, immutable, never collides, allows narrow clustered storage | Needs a uniqueness rule on the natural key too, and leaks row counts |
| Natural (business key) | No extra column, meaningful on its own | Mutable in practice, can exceed index key width, invites accidental renumbering |
| UUID | Distributed generation, no coordination, safe to expose externally | 16 bytes, random values scatter index inserts badly, no natural ordering |
| ULID / UUIDv7 | Time-ordered prefix keeps inserts local, still globally unique | Non-standard format; you implement the encoding |
| Composite natural key | Often the true key | Wider indexes, more verbose joins |

The overwhelmingly safe default: a `bigint` surrogate primary key plus a `UNIQUE`
constraint on the business key. Both rules enforced, no ambiguity.

```sql
CREATE TABLE customers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id uuid NOT NULL,
    slug        text NOT NULL,
    CONSTRAINT customers_external_id_key UNIQUE (external_id),
    CONSTRAINT customers_slug_key UNIQUE (slug)
);
```

**Why `bigint` and not `int`** for anything that grows: `int` tops out around 2.1 billion. An
`int` identity column on a high-churn table is a latent outage.

**Why monotonic keys help**: rows are appended, so index pages stay dense. Random UUIDs
insert into random leaf pages, which splits pages and defeats read-ahead. If you need globally
unique identifiers that are also insert-ordered, use a time-ordered UUID version or a
ULID, and store it as `uuid` or `text` depending on your engine.

If you choose a natural primary key, make it immutable and enforce it:

```sql
CREATE TABLE countries (
    code    char(2) NOT NULL PRIMARY KEY CHECK (code = upper(code)),
    name    text NOT NULL UNIQUE
);
```

## Foreign keys and referential actions

A foreign key is the only mechanism that makes the database refuse to store an orphan. Skipping
it means the invariant lives only in application code, and every future writer must remember.

| `ON DELETE` action | Behaviour | Use when |
| --- | --- | --- |
| `NO ACTION` | Reject the delete if children exist | Default. Safe choice. |
| `RESTRICT` | Reject the delete, checked immediately rather than at end of statement or transaction | Same intent, slightly stricter timing |
| `CASCADE` | Delete children too | Ownership is strict: an order row has no meaning without its order |
| `SET NULL` | Null the child's foreign key | Optional relationship, and the column must be nullable |
| `SET DEFAULT` | Reset the child to its column default | Rare; use a sentinel default if you use it at all |

The difference between `NO ACTION` and `RESTRICT` only shows up inside a transaction with
deferred constraint checking. In PostgreSQL you can also defer a constraint deliberately:

```sql
CREATE TABLE employees (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manager_id  bigint REFERENCES employees (id)
                    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    name        text NOT NULL
);
```

Deferring lets you reorder, delete and reinsert a whole management tree in one transaction
without tripping intermediate states.

**CASCADE is a data-loss decision, not a default.** The classic failure is a cascade chain:
delete `users`, which cascades to `orders`, which cascades to `order_items`, which cascades to
`shipments`, and the finance team loses the shipment records. Use `RESTRICT` by default and
`CASCADE` only where the child genuinely cannot exist alone.

```sql
-- Ownership: cascade is correct.
CREATE TABLE order_items (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    sku         text NOT NULL,
    quantity    integer NOT NULL CHECK (quantity > 0)
);

-- Shared reference: cascade is wrong.
CREATE TABLE invoices (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    total_cents bigint NOT NULL
);
```

## Data types and precision

| Concept | Use | Avoid |
| --- | --- | --- |
| Money | `bigint` minor units, or `numeric(p, s)` with an explicit scale | `float`; the `money` type, whose formatting is locale-dependent |
| Counts | `integer`, or `bigint` when it can exceed 2.1 billion | `int` where growth is unbounded |
| Ratios | `numeric` for exact decimal, `double precision` for approximate measurements | `float` for money |
| Text | `text` when the length is genuinely unbounded | `varchar(n)` used as validation, which is a `CHECK` job |
| Timestamps | `timestamptz` always | `timestamp` (without zone) for events that happened in the real world |
| Dates | `date` for calendar dates with no time component | `timestamptz` for a birthday |
| Identifiers | `bigint` identity, or `uuid` when generated off-box | Text identifiers that are really integers |
| Booleans | `boolean` | `char(1)` `'Y'`/`'N'` |
| Statuses | `text` plus a `CHECK`, or a lookup table | Unconstrained `text` that only the application understands |

```sql
-- Exact decimal. Scale is part of the type, so 19.99 fits and 19.999 raises an error.
CREATE TABLE products (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    price         numeric(12, 2) NOT NULL CHECK (price >= 0),
    tax_rate      numeric(5, 4) NOT NULL DEFAULT 0.2000
);

-- Overflow is an error, not silent wrapping.
SELECT 9999999999.99::numeric(12, 2);
-- ERROR:  numeric field overflow
```

Timezone handling deserves care. `timestamptz` stores an absolute instant and renders it in
the session time zone. A `timestamp` stores a wall-clock reading with no offset, so the same
stored value means different instants for users in different regions.

```sql
CREATE TABLE events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at  timestamptz NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    -- A local wall-clock reading the business asked for, kept deliberately separate.
    local_time   timestamp
);
```

`text` versus `varchar(n)` in PostgreSQL specifically: `text` has no length limit and
`varchar(n)` has a length check that is enforced on assignment. The performance difference is
not meaningful. Use `CHECK (char_length(name) <= 200)` when you want validation on a `text`
column so the intent is visible.

Case-insensitive lookup without changing storage:

```sql
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email citext NOT NULL UNIQUE
);
```

## NULL semantics

SQL uses three-valued logic. `TRUE`, `FALSE`, and `UNKNOWN`, where `UNKNOWN` comes from any
comparison involving `NULL`.

```sql
SELECT NULL = 1;        -- NULL, not FALSE
SELECT NULL IS NULL;    -- TRUE
SELECT NULL <> NULL;    -- NULL
SELECT NULL AND FALSE;  -- FALSE  (FALSE dominates)
SELECT NULL OR TRUE;    -- TRUE   (TRUE dominates)
SELECT NOT NULL;        -- NULL

-- WHERE keeps only TRUE.
SELECT id FROM users WHERE deleted_at = NULL;        -- returns nothing, always
SELECT id FROM users WHERE deleted_at IS NULL;       -- correct
```

Three traps that generate real incidents:

```sql
-- Trap 1: NOT IN with a NULL in the list returns no rows at all.
SELECT id FROM users WHERE status NOT IN ('active', 'suspended');          -- fine
SELECT id FROM users WHERE status NOT IN ('active', NULL);                -- zero rows

-- Fix: filter out the NULLs first.
SELECT id FROM users
WHERE status IS DISTINCT FROM 'suspended';

-- Trap 2: NOT IN against a subquery that can emit NULL.
SELECT id FROM users
WHERE id NOT IN (SELECT customer_id FROM orders WHERE status = 'open');  -- zero rows if any NULL

-- Fix: use NOT EXISTS, which compares with IS NOT DISTINCT FROM semantics.
SELECT u.id
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM orders o
    WHERE o.customer_id = u.id AND o.status = 'open'
);

-- Trap 3: aggregates skip NULLs.
SELECT COUNT(*) FROM users;          -- counts every row
SELECT COUNT(middle_name) FROM users; -- counts non-NULL only
SELECT SUM(amount) FROM orders;      -- ignores NULL amounts entirely
```

`COALESCE` and `NULLIF` are the two functions that fix most of it:

```sql
SELECT COALESCE(nickname, display_name, 'anonymous') FROM users;
SELECT amount / NULLIF(denominator, 0) FROM ratios;
```

Design rule: a `NULL` in a nullable column means one thing and only that thing. Do not use
`NULL` for "unknown", "not applicable", "zero", and "deleted" simultaneously in the same
column. That is the actual cause of most NULL-related bugs.

## CHECK constraints

`CHECK` is the right place for anything you know to be true about a single row, because it
cannot be bypassed and it cannot be forgotten.

```sql
CREATE TABLE subscriptions (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_code          text NOT NULL,
    seats              integer NOT NULL CHECK (seats > 0),
    billing_email      text NOT NULL,
    trial_ends_at      timestamptz,
    cancelled_at       timestamptz,
    CONSTRAINT trial_before_cancel CHECK (trial_ends_at IS NULL
                                          OR cancelled_at IS NULL
                                          OR trial_ends_at <= cancelled_at)
);
```

Two semantics to remember:

- A `CHECK` that evaluates to `NULL` is **satisfied**, not violated. `CHECK (col > 0)` does not
  stop `col = NULL`. Add `NOT NULL` when that matters.
- `CHECK` is evaluated again on `UPDATE`, so it also guards updates.

For values from a known set, a `CHECK` on `text` is portable and self-documenting. A native
`ENUM` type is a schema object with ordering and lifecycle of its own, which makes adding and
removing values an `ALTER TYPE` and can block on locks. A lookup table with a foreign key is the
third option and is the right one when the values carry attributes.

```sql
-- Lookup table.
CREATE TABLE order_statuses (
    code        text PRIMARY KEY,
    description text NOT NULL,
    is_terminal boolean NOT NULL DEFAULT false
);

CREATE TABLE orders (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code text NOT NULL REFERENCES order_statuses (code)
);
```

## When to split a table

Split when one of these is true, not because the table "feels big".

| Signal | Split |
| --- | --- |
| Row is too wide and you read a few small columns | Vertical split: hot columns in a narrow table joined by key |
| Two column groups have different update rates | Vertical split avoids rewriting the cold group |
| One column group grows without bound while the rest is fixed | Vertical split, or move to a child table |
| Rows have genuinely different optional sub-structures | Optional-one-to-one child table |
| A single tenant, region or category is orders of magnitude larger | Partitioning, or a separate table |

Vertical split, and how it reads:

```sql
CREATE TABLE users (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Rarely read, large, and rewritten on every profile save.
CREATE TABLE user_profiles (
    user_id     bigint PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    bio         text,
    avatar_url  text,
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX user_profiles_avatar_idx ON user_profiles (avatar_url)
    WHERE avatar_url IS NOT NULL;
```

Do not split because "the table is getting big". Row count alone is not a reason; access
patterns and update rates are.

## Temporal and effective-dated modeling

Four patterns cover most history requirements.

**SCD Type 1 — overwrite.** No history. Cheapest. The audit trail lives elsewhere.

**SCD Type 2 — versioned row.** Add validity columns and insert a new row per change.

```sql
CREATE TABLE customer_terms (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
    payment_terms text NOT NULL,
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz NOT NULL,
    CONSTRAINT customer_terms_valid_range CHECK (valid_from < valid_to),
    CONSTRAINT customer_terms_no_overlap
        EXCLUDE USING gist (customer_id WITH =, tstzrange(valid_from, valid_to) WITH &&)
);

CREATE INDEX customer_terms_lookup_idx
    ON customer_terms (customer_id, valid_from DESC);

-- Current terms.
SELECT * FROM customer_terms
WHERE customer_id = 1 AND valid_from <= now() AND valid_to > now();
```

The `EXCLUDE` constraint is the part people skip and then regret: it is what makes "no two
overlapping validity periods" a database guarantee instead of an application convention.

**Bitemporal.** Two time axes: when the fact was true, and when we knew it. Four columns per
axis plus two exclusion constraints. Worth the cost only for regulated reporting where "what
did we believe at the time" must be answerable.

**Event/ledger tables.** Append-only rows with `effective_at` and `recorded_at`. Best for
financial and audit data because corrections are new compensating rows rather than updates.

```sql
CREATE TABLE account_entries (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id     bigint NOT NULL REFERENCES accounts (id) ON DELETE RESTRICT,
    amount_cents   bigint NOT NULL,
    effective_at   timestamptz NOT NULL,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    reversal_of    bigint REFERENCES account_entries (id),
    CONSTRAINT amount_not_zero CHECK (amount_cents <> 0)
);

CREATE INDEX account_entries_account_effective_idx
    ON account_entries (account_id, effective_at);
```

## Soft delete tradeoffs

A `deleted_at` column looks free and is not.

| Problem | Detail |
| --- | --- |
| Uniqueness | `UNIQUE (email)` still blocks re-registering a deleted account. PostgreSQL needs `UNIQUE (email) WHERE deleted_at IS NULL` (a partial unique index) to model "unique among live rows". |
| Constraints | Foreign keys point at deleted rows, so `ON DELETE CASCADE` never fires and children are orphaned. |
| Queries | Every read needs `WHERE deleted_at IS NULL`. Miss one and deleted data leaks. |
| Performance | Hot rows accumulate in a small fraction of pages; bloat grows because updates to `deleted_at` touch the same pages. |
| Undelete and audit | Restores need to know what else changed while the row was "gone". |
| GDPR | Deletion still requires actual deletion. A soft delete is a contradiction of a hard-delete obligation. |

```sql
CREATE TABLE users (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email      text NOT NULL,
    deleted_at timestamptz,
    -- Uniqueness among live rows only.
    CONSTRAINT users_email_live_key UNIQUE (email) DEFERRABLE INITIALLY IMMEDIATE
);

-- PostgreSQL partial unique index for the live-only rule:
CREATE UNIQUE INDEX users_email_live_uidx ON users (email) WHERE deleted_at IS NULL;
```

If you only need "hide archived rows", prefer a status column over a timestamp, or a separate
archive table. If you need true deletion to be irreversible, do not add a soft-delete flag.

## Checklist

- Primary key is a narrow, monotonic, immutable surrogate, plus a `UNIQUE` on the business key.
- Every relationship has a foreign key with an explicit, reasoned `ON DELETE` action.
- No `CASCADE` chains more than one level deep without a written justification.
- Money is `bigint` minor units or `numeric(p, s)` with a stated scale. No floats.
- Every timestamp column that records a real-world event is `timestamptz`.
- Every `NULL` in a column means exactly one thing, documented in the column comment.
- `NOT NULL` wherever the column is genuinely required; `CHECK` for single-row invariants.
- Indexes exist for foreign keys on the child side and for every access path you can name.
- History requirements are classified as Type 1, Type 2, bitemporal or ledger, and modelled
  accordingly rather than improvised.
- Soft delete, if used, has a documented uniqueness strategy and a purge policy.

## References

- `CREATE TABLE`: https://www.postgresql.org/docs/current/sql-createtable.html
- `ALTER TABLE`: https://www.postgresql.org/docs/current/sql-altertable.html
- Constraints (CHECK, UNIQUE, EXCLUDE): https://www.postgresql.org/docs/current/ddl-constraints.html
- Foreign key constraints: https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-FK
- Default values: https://www.postgresql.org/docs/current/ddl-default.html
- Identity columns: https://www.postgresql.org/docs/current/ddl-identity-columns.html
- Generated columns: https://www.postgresql.org/docs/current/ddl-generated-columns.html
- Row security policies: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- Data types overview: https://www.postgresql.org/docs/current/datatype.html
- Numeric types: https://www.postgresql.org/docs/current/datatype-numeric.html
- Character types: https://www.postgresql.org/docs/current/datatype-character.html
- Date and time types: https://www.postgresql.org/docs/current/datatype-datetime.html
- Enum types: https://www.postgresql.org/docs/current/datatype-enum.html
- JSON types: https://www.postgresql.org/docs/current/datatype-json.html
- The `money` type and its caveats: https://www.postgresql.org/docs/current/datatype-money.html
- `uuid` type: https://www.postgresql.org/docs/current/datatype-uuid.html
- Arrays: https://www.postgresql.org/docs/current/arrays.html
- Range types: https://www.postgresql.org/docs/current/rangetypes.html
- `CREATE TYPE`: https://www.postgresql.org/docs/current/sql-createtype.html
- Collations: https://www.postgresql.org/docs/current/sql-createcollation.html
- `CREATE VIEW` and materialized views: https://www.postgresql.org/docs/current/sql-createview.html
- Table partitioning: https://www.postgresql.org/docs/current/ddl-partitioning.html
- `citext`: https://www.postgresql.org/docs/current/citext.html
- Comparison and conditional expressions (three-valued logic): https://www.postgresql.org/docs/current/functions-comparison.html
- SQLite foreign key support: https://www.sqlite.org/foreignkeys.html
- SQLite `WITHOUT ROWID` tables: https://www.sqlite.org/withoutrowid.html
- SQLite partial indexes: https://www.sqlite.org/partialindex.html
- MySQL reference manual: https://dev.mysql.com/doc/refman/en/
