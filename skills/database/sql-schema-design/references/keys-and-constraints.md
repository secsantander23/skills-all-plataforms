# Keys, Constraints and Relationship Patterns

## Constraint checklist for a new table

```sql
CREATE TABLE subscription_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id bigint NOT NULL
                        REFERENCES subscriptions (id) ON DELETE CASCADE,
    event_type      text  NOT NULL
                        CHECK (event_type IN ('created', 'renewed', 'cancelled', 'expired')),
    occurred_at     timestamptz NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),

    -- A repeated event for the same subscription and type at the same instant is a bug.
    CONSTRAINT subscription_events_natural_key
        UNIQUE (subscription_id, event_type, occurred_at),

    CONSTRAINT subscription_events_metadata_is_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX subscription_events_subscription_idx
    ON subscription_events (subscription_id, occurred_at DESC);
```

## Composite uniqueness and NULL behaviour

`UNIQUE` treats NULLs as equal in most engines, so a nullable column cannot be used to
implement "many rows may have NULL here". Use a partial unique index or a sentinel value.

```sql
-- Only one row per (user_id, day) for non-null days.
CREATE UNIQUE INDEX usage_daily_uniq
    ON usage_daily (user_id, day)
    WHERE day IS NOT NULL;
```

This is the standard technique for "unique among live rows" that soft delete needs, and for
"one row per group per period" without materialising absent periods.

## Deferring constraint checks

```sql
CREATE TABLE org_units (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id   bigint REFERENCES org_units (id)
                    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
    name        text NOT NULL,
    path        text[] NOT NULL
);

-- Reordering and moving a whole subtree in one transaction is now legal.
BEGIN;
UPDATE org_units SET parent_id = 3 WHERE id = 42;
UPDATE org_units SET parent_id = 42 WHERE id = 3;
COMMIT;
```

Deferring only works for foreign keys, unique constraints, and primary keys in PostgreSQL.
`CHECK` constraints are always evaluated immediately.

## Exclusion constraints for validity ranges

```sql
CREATE TABLE room_reservations (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id    bigint NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    guest_id   bigint NOT NULL REFERENCES guests (id) ON DELETE RESTRICT,
    during     tstzrange NOT NULL,
    status     text NOT NULL DEFAULT 'confirmed',

    -- No two overlapping confirmed reservations for the same room.
    CONSTRAINT room_reservations_no_overlap
        EXCLUDE USING gist (room_id WITH =, during WITH &&)
        WHERE (status = 'confirmed')
);

-- Range containment and overlap queries become index-backed.
SELECT guest_id FROM room_reservations
WHERE room_id = 7 AND during && tstzrange(now(), now() + interval '1 day');
```

The `EXCLUDE` constraint needs a GiST or SP-GiST index over the range column, which the
constraint creates for you on the constraint's expression. Exclusion constraints support a
`WHERE` predicate, which is how "only for active rows" is expressed.

## Relationship patterns worth naming

**One-to-one with a required child.** Use the same primary key on both tables and a foreign
key from child to parent, so the child can be proven to exist for every parent.

```sql
CREATE TABLE users (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL UNIQUE
);

CREATE TABLE user_preferences (
    user_id    bigint PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    theme      text NOT NULL DEFAULT 'system',
    locale     text NOT NULL DEFAULT 'en',
    timezone   text NOT NULL DEFAULT 'UTC'
);
```

**One-to-many with a join attribute.** The relationship has its own columns; give it its own
table with its own key.

```sql
CREATE TABLE team_memberships (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id    bigint NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id    bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('member', 'admin', 'owner')),
    joined_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (team_id, user_id)
);
```

**Many-to-many with a cardinality limit** (a user belongs to at most one team at a time):

```sql
-- Partial unique index enforces "one active team per user".
CREATE UNIQUE INDEX team_memberships_one_active_per_user
    ON team_memberships (user_id)
    WHERE left_at IS NULL;
```

**Polymorphic association.** `parent_type` plus `parent_id` cannot be a foreign key. This is
the classic trade: you lose referential integrity. If you can, use a nullable foreign key per
concrete type with a `CHECK` that exactly one is set, which is enforceable.

```sql
CREATE TABLE attachments (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename     text NOT NULL,
    issue_id     bigint REFERENCES issues (id) ON DELETE CASCADE,
    comment_id   bigint REFERENCES comments (id) ON DELETE CASCADE,
    CONSTRAINT attachments_exactly_one_parent
        CHECK ((issue_id IS NOT NULL)::int + (comment_id IS NOT NULL)::int = 1)
);
```

## Foreign key performance notes

- Index the child side of every foreign key you might traverse or cascade from. A missing
  index turns a parent delete into a full scan of the child per parent row.
- The referenced side must have a unique constraint; the primary key provides it.
- Foreign keys add per-row checks on insert and update. On high-volume append tables with no
  parent deletes, that is sometimes the most expensive constraint on the table. Measure before
  removing integrity.
- Self-referencing foreign keys need care with insert order, or a deferrable constraint.

## Audit columns worth having

```sql
CREATE TABLE t (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by bigint REFERENCES users (id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by bigint REFERENCES users (id) ON DELETE SET NULL
);
```

Keep `updated_at` correct with a trigger rather than application code if you have more than one
writer path. Be aware that a trigger fires on every update, including ones that change nothing.
