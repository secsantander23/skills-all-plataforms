# dbt Reference

## Layer discipline

| Layer | One job | Forbidden | Reviewer question |
|---|---|---|---|
| `staging_` | 1:1 normalization of a source table | Joins, business rules, multi-source | Does this map exactly one source object? |
| `intermediate_` | Join and reshape for reuse | Consumer-facing names | Is this used by two or more downstream models? |
| `fct_` / `dim_` | Serve a named use case | Reusable plumbing logic | Which consumer does this exist for? |

If a staging model has a join, split it: put each source in staging, join in intermediate.

## Source and ref hygiene

- Declare every source in a `sources.yml` with a `description`, a `freshness` block, and a
  `loaded_at_field` where the source provides one.
- Never write raw table names in SQL. Use `source()` and `ref()` so the graph is complete and
  `dbt build` ordering is correct.
- A model that is not referenced by `ref()` anywhere will still build, and you will not know
  that until you delete the table. Use `dbt ls` to inspect the graph.

## Staging model template

```sql
-- models/staging/stg_payments.sql
{{ config(materialized='view') }}

select
    cast(id as text)            as payment_id,
    cast(order_id as text)      as order_id,
    cast(amount_cents as bigint) as amount_minor,
    cast(state as text)         as state,
    cast(created_at as timestamp) as created_at
from {{ source('raw', 'payments') }}
where deleted_at is null
```

Rules:

- One source object per model.
- Rename to a clear, consistent name. Cast every column to its intended type. Renaming the
  snake_case of the raw column is fine; renaming the meaning is not.
- Do not filter on business conditions. Only remove rows the source guarantees are not real
  (soft deletes, void records).
- A view in most warehouses means no storage cost and always fresh. Use `table` when the query
  is expensive enough that recomputing hurts.

## Intermediate model

```sql
-- models/intermediate/int_payments_with_order.sql
select
    p.payment_id,
    p.order_id,
    p.amount_minor,
    o.customer_id,
    o.ordered_at,
    o.channel
from {{ ref('stg_payments') }} as p
left join {{ ref('stg_orders') }} as o
    on p.order_id = o.order_id
```

## Incremental model correctness

```sql
{{ config(
    materialized='incremental',
    unique_key='payment_id',
    on_schema_change='append_new_columns'
) }}

select ... from {{ ref('int_payments_with_order') }}
{% if is_incremental() %}
where created_at > (select coalesce(max(created_at), '1970-01-01') from {{ this }})
{% endif %}
```

Checklist for an incremental model:

- `unique_key` is a column that is genuinely unique in the source. Verify it, do not assume it.
- The incremental filter is **monotonic**: no row can arrive with a timestamp older than the
  current maximum, or you will drop it. If the source can backfill, add an overlap window and
  make the write idempotent on the key.
- `on_schema_change` is set. The default behavior on a new column is to error out, which is
  sometimes what you want and sometimes not.
- A full refresh path exists and is tested.

`--full-refresh` on a large table is a long, expensive operation. Know the runtime before you
run it on the largest table in production.

## Tests

Generic test keys have been renamed over time (`tests` in older versions, `data_tests` in newer
ones). Check the documentation for your dbt version and use one convention across the project.

Singular tests return the failing rows. One assertion per file.

```sql
-- tests/assert_payments_have_a_known_state.sql
select payment_id, state
from {{ ref('fct_payments') }}
where state not in ('pending', 'captured', 'refunded', 'failed')
```

Useful generic tests beyond the defaults:

- `relationships` for referential integrity
- `accepted_values` for enumerations
- A custom expression test for business rules, defined in `macros/`

## Macros and packages

- Macros are Jinja functions. Keep them small, name them, and test them through models.
- Packages are installed with `dbt deps` and pinned in `packages.yml`. Pin versions and commit
  the lock file.
- Avoid complex Jinja. A macro nobody can read is a bug waiting for the one request that hits
  its untested branch.

## Environments

`profiles.yml` holds connection and target configuration and normally lives outside the repo.
Targets are typically `dev`, `ci`, and `prod`, differing in schema and threads.

Discipline:

- Separate schemas per environment. Never develop in the production schema.
- Run `dbt parse` in pre-commit: fast, and it catches config errors without a warehouse.
- Run `dbt build --target ci` in CI against a warehouse that has the source data.
- Use `dbt docs generate` and serve the catalog so consumers can see the model they should use.

## Performance

- `staging` as views means the join cost is recomputed downstream. If a join is expensive,
  materialize the intermediate as a table.
- Incremental models with a bad filter get slower over time. Watch row counts per run.
- Thread and batch settings are target-level configuration. Verify the keys with your adapter's
  documentation.

## What dbt does not do

dbt is a transformation layer and a documentation layer. It does not orchestrate across
non-SQL systems, it does not manage data quality across sources it does not model, and it does
not give you transactional guarantees between models. If you need updates and concurrency
control, that is a table-format problem.