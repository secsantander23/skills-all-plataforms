# Temporal Modeling and Schema Evolution

## Choosing a history pattern

| Requirement | Pattern | Cost |
| --- | --- | --- |
| "What is true now?" | Overwrite (Type 1) | None |
| "What did it used to be, and when did it change?" | Versioned rows (Type 2) | One row per change; validity queries |
| "What did we believe at time T, given what we knew at time S?" | Bitemporal | Four timestamp columns, two exclusion constraints |
| "Show the movement of a value" | Ledger / event table | Append-only, balances are computed or cached |

## Slowly changing dimension, type 2

```sql
CREATE TABLE products (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku         text NOT NULL,
    category_id bigint REFERENCES categories (id) ON DELETE SET NULL,
    name        text NOT NULL,
    price       numeric(12, 2) NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE product_history (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id  bigint NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    category_id bigint,
    name        text NOT NULL,
    price       numeric(12, 2) NOT NULL,
    valid_from  timestamptz NOT NULL,
    valid_to    timestamptz NOT NULL,
    CONSTRAINT product_history_valid CHECK (valid_from < valid_to),
    CONSTRAINT product_history_no_overlap
        EXCLUDE USING gist (product_id WITH =,
                            tstzrange(valid_from, valid_to, '[)') WITH &&)
);

CREATE INDEX product_history_asof_idx
    ON product_history (product_id, valid_from DESC, valid_to DESC);
```

As-of query against history:

```sql
SELECT *
FROM product_history
WHERE product_id = $1
  AND valid_from <= $2
  AND valid_to > $2
ORDER BY valid_from DESC
LIMIT 1;
```

The half-open interval convention `[)` avoids the off-by-one where an event valid from
`12:00:00` to `12:00:00` is an empty interval instead of an instant.

## Bitemporal

Four axes per row: `valid_from`/`valid_to` for when the fact was true, and
`recorded_from`/`recorded_to` for when the system learned it.

```sql
CREATE TABLE facts (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id    bigint NOT NULL,
    attribute     text NOT NULL,
    value         text NOT NULL,
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz NOT NULL,
    recorded_from timestamptz NOT NULL,
    recorded_to   timestamptz NOT NULL,
    CONSTRAINT facts_no_valid_overlap
        EXCLUDE USING gist (subject_id WITH =, attribute WITH =,
                            tstzrange(valid_from, valid_to, '[)') WITH &&),
    CONSTRAINT facts_no_record_overlap
        EXCLUDE USING gist (subject_id WITH =, attribute WITH =,
                            tstzrange(recorded_from, recorded_to, '[)') WITH &&)
);
```

"True as of now, according to what we knew as of last quarter" is then expressible, at the cost
of every read being a two-axis filter. Adopt only when the question is actually asked.

## Ledger and compensating entries

Never update a financial row. Post a reversal.

```sql
CREATE TABLE ledger_entries (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id   bigint NOT NULL REFERENCES accounts (id) ON DELETE RESTRICT,
    amount_cents bigint NOT NULL CHECK (amount_cents <> 0),
    effective_at timestamptz NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    reversal_of  bigint REFERENCES ledger_entries (id),
    memo         text,

    -- A reversal must point at an existing entry that has not already been reversed.
    CONSTRAINT ledger_entries_reversal_once
        UNIQUE (reversal_of)
);

CREATE INDEX ledger_entries_account_idx
    ON ledger_entries (account_id, effective_at, id);

-- Balance.
SELECT account_id, SUM(amount_cents) AS balance_cents
FROM ledger_entries
WHERE account_id = $1
GROUP BY account_id;
```

Balance as a cached column is fine as long as you keep a rebuild query and a way to detect
drift.

```sql
-- Reconcile the cache against the source of truth.
WITH computed AS (
    SELECT account_id, SUM(amount_cents) AS expected
    FROM ledger_entries GROUP BY account_id
)
SELECT a.id
FROM accounts a
LEFT JOIN computed c ON c.account_id = a.id
WHERE a.balance_cents <> COALESCE(c.expected, 0);
```

## Retaining history without unbounded growth

- Partition history tables by time and drop old partitions. Dropping a partition is fast;
  deleting the equivalent rows with a `DELETE` is not.
- Archive to cheaper storage with `COPY` or `pg_dump`, then delete in batches.
- Aggregate old history into rollups at a coarser grain before retention deletes.

## Schema evolution tactics that avoid downtime

Never rename or retype a column in one step. Use the expand-contract sequence:

1. **Expand.** Add the new column or table. Nothing reads it yet.
2. **Dual write.** Application writes both. Old column stays authoritative.
3. **Backfill.** Batch-update the new column from the old one.
4. **Switch reads.** Move readers to the new column.
5. **Enforce.** Add `NOT NULL`, `CHECK`, or a foreign key on the new column.
6. **Contract.** Stop writing the old column, then drop it in a later release.

Full sequence with code is in the `database-migrations` skill.

## Column comments and documentation

```sql
COMMENT ON TABLE ledger_entries IS
    'Append-only. Corrections are compensating entries, never UPDATEs.';
COMMENT ON COLUMN ledger_entries.effective_at IS
    'When the entry takes economic effect, which may be in the future.';
COMMENT ON COLUMN ledger_entries.recorded_at IS
    'When the entry was written. Differs from effective_at for scheduled postings.';
```

Keep comments in the same migration as the change they describe.
