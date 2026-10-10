# Migration Tooling Setup Reference

## Flyway

- Migrations live in a configured location, named by convention.
- Versioned migrations: `V<version>__<description>.sql` with a **double** underscore between
  version and description. Versions can be dotted integers such as `1.2.3`.
- Repeatable migrations: `R__<description>.sql`. They are re-run when their checksum changes.
- Flyway records applied migrations with a checksum and fails on checksum mismatch, which is
  exactly the "do not edit an applied migration" guardrail.
- Command-line entry point is `flyway` with subcommands such as `migrate`, `info`, `validate`,
  and `undo` (undo requires a licence tier in some distributions; verify for your version).

```sql
-- V3__add_orders_shipped_at.sql
ALTER TABLE orders ADD COLUMN shipped_at timestamptz;

-- R__views.sql
CREATE OR REPLACE VIEW order_summary AS
SELECT customer_id, count(*) AS order_count
FROM orders
GROUP BY customer_id;
```

## Liquibase

- A changelog is an ordered list of changesets. Formats: XML, YAML, JSON, or formatted SQL.
- Each changeset has an `id` and an `author`. Running the same changeset twice is a no-op
  unless the checksum changed.
- `include` and `includeAll` build a changelog from many files.
- Contexts and labels let the same changelog target environments differently.

```yaml
databaseChangeLog:
  - changeSet:
      id: 3-add-shipped-at
      author: platform
      preConditions:
        onColumnExists:
          tableName: orders
          columnName: shipped_at
      changes:
        - addColumn:
            tableName: orders
            columns:
              - name: shipped_at
                type: timestamptz
```

Preconditions let a changeset be safely re-run when it may have partially applied.

## Atlas

- Schema is declared in HCL files and the desired state is compared against a database.
- `atlas schema apply` computes and applies a diff.
- Linting can fail a deployment when a change is destructive without an explicit
  acknowledgement, which is a useful CI gate for expand-contract discipline.
- Works with a versioned-migrations workflow as well as declarative.

```hcl
table "orders" {
  schema = schema.public
  column "id" {
    type = bigint
  }
  primary_key {
    columns = [column.id]
  }
  column "shipped_at" {
    type = timestamptz
    null = true
  }
}
```

## Alembic (Python)

```bash
alembic revision -m "add shipped_at to orders"
alembic upgrade head
alembic downgrade -1
alembic current
alembic history --verbose
```

Autogenerate compares models to the database and emits a starting diff. It cannot see
server-side defaults, some constraint renames, or data migrations. Always read the generated
file and replace anything that rewrites a table.

```python
# alembic/versions/9a1f...add_shipped_at.py
from alembic import op
import sqlalchemy as sa

revision = "9a1f2c3d4e5f"
down_revision = "1b2c3d4e5f6a"

def upgrade() -> None:
    op.add_column("orders", sa.Column("shipped_at", sa.TIMESTAMP(timezone=True)))

def downgrade() -> None:
    op.drop_column("orders", "shipped_at")
```

For an index that cannot run inside a transaction:

```python
def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("CREATE INDEX CONCURRENTLY orders_shipped_idx ON orders (shipped_at)")
```

## Prisma

- The Prisma schema is the source of truth for the generated client.
- `prisma migrate dev` creates and applies a migration in development, and regenerates the
  client.
- `prisma migrate deploy` applies pending migrations in production without the interactive
  steps or the client regeneration.
- Prisma supports custom SQL in migrations for anything the generator cannot express.
- Prisma has its own migration table; do not mix it with another tool's history.

## Django

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py showmigrations
python manage.py sqlmigrate <app> <migration_name>   # inspect the SQL before running
```

`RunPython` for data migrations. Always make them idempotent with `apps.get_model` lookups
rather than importing your models directly, so the historical schema is used correctly.

## Rails

```bash
rails generate migration AddShippedAtToOrders shipped_at:datetime
rails db:migrate
rails db:rollback
rails db:migrate:status
```

`add_index ... algorithm: :concurrently` exists in Rails migrations for online index builds, and
it requires `disable_ddl_transaction!` at the top of the migration. Verify the exact method
name against your Rails version's documentation.

## Knex

```bash
knex migrate:latest
knex migrate:rollback
knex migrate:status
knex migrate:make add_shipped_at
```

## Plain SQL scripts

The most controllable option, and the one with the fewest surprises about lock behaviour.

```sql
-- migrations/0003_add_shipped_at.sql
SET lock_timeout = '3s';
ALTER TABLE orders ADD COLUMN shipped_at timestamptz;
```

You then need to write the runner: an ordered list, a bookkeeping table, a checksum per file,
and a lock so two deploys do not race.

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
```

Adopt an advisory lock around your runner so two instances cannot apply concurrently:

```sql
SELECT pg_advisory_lock(4711);   -- fixed key for your migration runner
-- apply migrations
SELECT pg_advisory_unlock(4711);
```

## Choosing between them

| Situation | Choose |
| --- | --- |
| Existing project, framework-owned schema | The framework's tool |
| Polyglot organisation, many services, one schema | Flyway, Liquibase, or a shared plain-SQL convention |
| Schema is reviewed and approved as code | Atlas or a declarative equivalent |
| Strong regulatory change records needed | Liquibase or Flyway with change-set governance |
| Small team, high trust, full control wanted | Plain SQL with your own runner |
