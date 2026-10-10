# Data Types, Precision and Encoding Reference

Load this when choosing or defending a column type.

## Exact decimals versus floating point

`numeric(p, s)` stores an exact base-10 value with `p` total digits and `s` digits after the
decimal point. It is the right type for money, tax rates, and anything a human reconciles.

```sql
CREATE TABLE invoices (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subtotal      numeric(12, 2) NOT NULL,
    tax_rate      numeric(5, 4)  NOT NULL,  -- 0.2000 means 20%
    total         numeric(12, 2) GENERATED ALWAYS AS
                      (round(subtotal * (1 + tax_rate), 2)) STORED
);

-- Rounding is explicit; never let binary floating point decide it.
SELECT round(10.005::numeric, 2);
```

Accumulating floats produces visible drift:

```sql
SELECT 0.1::double precision + 0.2::double precision;              -- not exactly 0.3
SELECT 0.1::numeric        + 0.2::numeric;                        -- exactly 0.3
```

`double precision` is still correct for scientific measurements, sensor readings, and
statistics where an approximate value is the point. Do not use it for currency.

## Integer width

| Type | Range | Use for |
| --- | --- | --- |
| `smallint` | -32768 to 32767 | Small codes and counts |
| `integer` | about -2.1e9 to 2.1e9 | Row counts and quantities that fit |
| `bigint` | about -9.2e18 to 9.2e18 | Anything that might grow without bound |

Generated identity columns should default to `bigint`. The cost of `bigint` against `int` in a
secondary index is small; the cost of hitting the `int` ceiling is an outage.

## Integer minor units for money

```sql
CREATE TABLE payments (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    amount_minor   bigint NOT NULL CHECK (amount_minor > 0),
    currency       char(3) NOT NULL
);

-- Handle currencies with different exponents explicitly rather than assuming two.
-- JPY has no minor unit; some currencies have three.
```

Store the currency code alongside the amount. An amount without a currency is not data.

## Character types

```sql
CREATE TABLE t (
    a text,          -- unlimited, no trailing-space semantics
    b varchar(200),  -- unlimited storage, length enforced on assignment
    c char(3),       -- blank-padded to fixed width on storage and comparison
    d citext         -- requires CREATE EXTENSION citext
);

-- Validation belongs in CHECK so the intent is visible and the storage stays flexible.
ALTER TABLE t ADD CONSTRAINT b_len CHECK (char_length(b) <= 200);
```

Do not use `char(n)` for short codes expecting it to be efficient. It blank-pads, which makes
comparison surprising (`'US' = 'US '` is true in some engines) and can defeat index use.

For identifiers from external systems, store them as `text` and add a `CHECK` for shape if you
must:

```sql
CREATE TABLE external_refs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    system       text NOT NULL,
    external_id  text NOT NULL,
    UNIQUE (system, external_id)
);
```

A composite uniqueness rule plus `CHECK` on length is usually better than a fixed-width type.

## Timestamps and time zones

```sql
CREATE TABLE t (
    occurred_at   timestamptz NOT NULL,   -- an instant; stored as UTC, rendered in session zone
    local_wall    timestamp,              -- wall-clock reading, no offset
    day           date,                   -- calendar date
    duration_s    interval                -- span of time
);

SELECT now();                       -- timestamptz, transaction start time
SELECT current_timestamp;           -- same value, different spelling
SELECT clock_timestamp();           -- actual wall clock at call time
SELECT statement_timestamp();       -- start of the current statement
SELECT transaction_timestamp();     -- start of the current transaction
```

Two rules that prevent whole classes of bugs:

1. Store `timestamptz` for anything that happened. Store `timestamp` only for a wall-clock
   reading the business explicitly asked for ("the branch opens at 09:00 local").
2. Set the session time zone deliberately and record what it was for any stored wall-clock
   value. Otherwise `timestamp` columns are unreadable.

PostgreSQL's `timestamptz` converts on display, so a report written for one zone can show
different wall-clock numbers for the same rows. Render explicitly:

```sql
SET TIME ZONE 'UTC';
SELECT occurred_at AT TIME ZONE 'America/Sao_Paulo' AS local_time FROM events;
```

## Booleans, enums and lookups

| Option | Pros | Cons |
| --- | --- | --- |
| `boolean` | Type-safe, no surprises | Cannot express a third state; use a nullable boolean carefully or a lookup |
| `text` + `CHECK (col IN (...))` | Portable, self-documenting, easy to extend | No referential integrity to a table of descriptions |
| `CREATE TYPE ... AS ENUM` | Ordered, compact, native | Lifecycle is an `ALTER TYPE`; dropping a value needs a migration |
| Lookup table + foreign key | Attributes, joins, cascade deletes | An extra join |

Prefer the lookup table once you need anything beyond the code itself, such as a label,
sort order, or whether the value is terminal for a state machine.

## JSON columns

JSON is a good fit for genuinely open, read-mostly attributes. It is a bad fit for anything you
filter, join, sort, or aggregate on, because you end up indexing expressions inside it.

```sql
CREATE TABLE documents (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    body       jsonb NOT NULL DEFAULT '{}'::jsonb,
    title      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_body_is_object CHECK (jsonb_typeof(body) = 'object')
);

-- Expression index on a JSONB field.
CREATE INDEX documents_status_idx ON documents ((body ->> 'status'));

-- Containment index.
CREATE INDEX documents_body_gin_idx ON documents USING gin (body jsonb_path_ops);
```

Promote a field out of the JSON column into a real column as soon as you start filtering or
joining on it. That promotion is a normal migration.

## Arrays

Arrays are convenient for small, always-accessed-together lists. They are bad for anything
queried by element, because the element index is not an ordinary index entry.

```sql
CREATE TABLE articles (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tags  text[] NOT NULL DEFAULT '{}'
);

-- GIN makes '@>' and '&&' work on arrays.
CREATE INDEX articles_tags_gin_idx ON articles USING gin (tags);

SELECT id FROM articles WHERE tags @> ARRAY['postgres'];
```

If the elements have identity or need their own rows, use a child table.

## UUIDs and their storage

```sql
-- Random UUID: 16 bytes, no natural ordering, index inserts scatter.
CREATE TABLE a (id uuid PRIMARY KEY DEFAULT gen_random_uuid());

-- Time-ordered UUID versions keep inserts sequential while staying globally unique.
CREATE TABLE b (id uuid PRIMARY KEY);
```

If your database offers a time-ordered UUID generator, prefer it for externally generated
identifiers. If you need the ordering property but not the UUID format, an ordered integer or
a ULID-style text value both work. Verify generator availability and function names against
your engine's documentation.

## Column comments as schema documentation

```sql
COMMENT ON COLUMN ledger_entries.effective_at IS
    'When the entry takes economic effect. May differ from created_at (when we learned it).';
COMMENT ON COLUMN accounts.balance_cents IS
    'Cached sum of ledger_entries.amount_cents. Rebuildable; ledger is the source of truth.';
```

Comments survive in the database where a wiki does not, and they are the first thing a new
engineer reads.
