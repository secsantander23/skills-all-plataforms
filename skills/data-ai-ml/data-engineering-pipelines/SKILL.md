---
name: Data Engineering Pipelines
description: Covers ETL versus ELT, batch versus streaming, Apache Airflow DAG design for idempotency retries and sensors, dbt staging intermediate and mart layering, Parquet ORC and Avro formats, partitioning and file sizing, data quality and testing, warehouse versus lakehouse tradeoffs, orchestration, and lineage. Use when the user asks about building a pipeline, fixing a flaky or duplicated Airflow DAG, structuring dbt models, choosing a file format or partition layout, or debugging data quality and freshness problems.
metadata:
  opencode/autoinvoke: true
---

# Data Engineering Pipelines

A data pipeline is a promise that the same input produces the same output. Most pipeline bugs
are that promise broken: a task that appends instead of replaces, a job that reads a file while
another writes it, a partition that silently mixes two schema versions. Design for idempotency
first, then for observability, then for speed.

```
source --> extract --> land (raw, immutable) --> transform --> curate --> serve
                                                       |             |
                                                 quality gates   marts, models, BI
```

## When to use this

- You are building or refactoring a scheduled data pipeline.
- A DAG is flaky, double-counts, or silently skips days.
- You are choosing a file format, partition layout, or table format.
- You are setting up dbt and need a project layout that will survive its first quarter.
- You are deciding between a warehouse, a lake, a lakehouse, and streaming.
- Data is arriving late, incomplete, or with schema drift and you need to find out why.

## ETL versus ELT

| | ETL | ELT |
|---|---|---|
| Where transformation happens | Before the warehouse, on a bespoke stack | Inside the warehouse, in SQL |
| Best when | Raw data must be scrubbed, masked, or joined before storage; target is not SQL | Warehouse is the compute you pay for anyway |
| Cost profile | Compute outside the platform, duplicated per destination | One copy in the platform, many consumers |
| Debuggability | Hard: transformations are scattered across scripts | Easy: SQL next to the data |
| Weakness | Ops burden, hard to reprocess | Storage cost, transformation pushed onto warehouse compute |

Most modern stacks land on ELT for analytics, with an E step at the edges for masking, schema
enforcement, and PII handling. Choose per source, not per company.

## Batch versus streaming

| | Batch | Streaming |
|---|---|---|
| Latency | Minutes to hours | Sub-second to seconds |
| Cost profile | Pay per run | Pay per record, continuously |
| Complexity | Low | High: state, windows, replay, ordering |
| Debugging | Easy | Hard |
| Right for | Reporting, marts, ML features | Real-time decisions, event pipelines |

Two architectures worth naming:

- **Lambda**: batch layer plus a speed layer plus a serving layer that merges them. Simple to
  reason about, painful to keep consistent, and it duplicates logic.
- **Kappa**: one streaming pipeline; replay by re-reading the log. Simpler, and it requires the
  log to be retained long enough to rebuild state.

Practical rule: build batch first. Add streaming only for a decision that genuinely cannot wait.
Everything else is a latency requirement you imposed on yourself.

When you do stream, the hard problems are not the transport. They are:

- **Event time vs processing time.** Decide which one your windowing uses, and carry the event
  timestamp in the payload.
- **Watermarks.** A bounded lateness policy, or a watermark never arrives and results never emit.
- **Ordering.** A partition guarantees order within itself and nowhere else. Key by entity id
  if you need per-entity order.
- **Replay.** Every consumer must be replayable from the log, so your state is derived, not
  accumulated in place.

Change data capture out of a database transaction log is the usual bridge from batch to
streaming, and it means you get schema history for free if you keep it.

## Storage layout

```
s3://bucket/warehouse/events/
   _common_metadata/          engine-owned table metadata
   event_date=2026-01-15/    Hive-style partition directories
       part-00000-<uuid>.parquet
   event_date=2026-01-16/
       part-00001-<uuid>.parquet
```

Table format matters:

| Format | Properties | Use when |
|---|---|---|
| Plain files (Parquet/ORC/Avro) | Simple, universally readable, no transactions | Read mostly by one engine, append only |
| Delta Lake | ACID transactions, schema enforcement, time travel | Concurrent writes and updates, mixed engines |
| Apache Iceberg | ACID, hidden partitioning, snapshot isolation | Large tables, many concurrent readers, partition evolution |

If you need updates and deletes, or more than one engine writing the same table, use an
open table format. If you only ever append and only one engine reads, plain files are fine and
much simpler.

### File formats

| Format | Encoding | Strength | Weakness |
|---|---|---|---|
| CSV | text | Universal, human readable | No types, no compression, slow to parse |
| JSON (lines) | text | Flexible schema, nested | No column skipping, bulky |
| Parquet | columnar | Column pruning, predicate pushdown, compression, statistics | Not for row-level updates |
| ORC | columnar | Hive heritage, ACID support in Hive | Less ecosystem support than Parquet |
| Avro | row-oriented, with a schema registry | Schema evolution and record-level updates | Row-oriented, not for analytics scans |

Avro is for record streams and schema evolution. Parquet is for analytical scans. Putting
Parquet where a small lookup used to live is a common mistake: you give up row-level updates to
get column pruning you do not need.

### File sizing

Target **128 MB to 1 GB** per file. Smaller files mean more metadata, more open handles, and
slower parallel reads; larger files mean wasted reads when you cannot split at the right
granularity. Write one output file per task per partition, and size your parallelism so a
single file is written by exactly one writer.

Small files are the classic "everything got slower and nobody knows why" failure. Symptom: job
duration scales with file count rather than data size. Fix: compaction into the same partition,
not into a differently partitioned path, so readers still prune.

### Compression

Snappy for speed, gzip for maximum compression, zstd in between and usually the best default for
analytics. Verify the codec is supported by every reader in your stack; some older readers only
handle snappy or gzip.

### Partitioning

- **Partition on columns you filter by**, usually a date, and keep the number of partitions
  bounded. Too many partitions is the same pathology as too many files.
- **Never partition on a high-cardinality column** such as user id. That is one directory per
  user.
- **Partition sizing target** is roughly 1 GB or larger per file, which implies choosing a date
  grain that keeps partition volume reasonable.
- **Static versus dynamic partitioning.** Static writes to a fixed location. Dynamic computes
  the partition from each record. Pick one deliberately; mixing them produces the same data in
  two directories and a silent double count.
- **Partition pruning only works if the query predicate is on the partition column**, and works
  best when the filter is a literal or a parameter the engine can bind, not a computed function.

```python
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq


def write_partition(rows: list[dict], out_dir: str, task_index: int) -> str:
    """Write exactly one file per task per partition. Never append to an existing file."""
    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("event_id", pa.string()),
                ("event_date", pa.string()),
                ("user_id", pa.string()),
                ("amount_minor", pa.int64()),
            ]
        ),
    )
    path = f"{out_dir}/part-{task_index:05d}-{uuid4().hex}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path
```

## Apache Airflow

### The shape of a good DAG

```python
from datetime import timedelta

import pendulum
from airflow.decorators import dag, task


@task
def delete_prefix(prefix: str) -> None:
    """Delete the whole partition prefix before writing it again."""
    client = storage_client()  # your object store client
    for blob in client.list_blobs(prefix=prefix):
        blob.delete()


@dag(
    dag_id="daily_revenue",
    description="Build the daily revenue mart from raw events.",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=3,
    dagrun_timeout=timedelta(hours=2),
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["finance", "mart"],
    doc_md="Owner: data-platform. SLO: complete by 07:30 UTC.",
)
def daily_revenue():
    @task
    def extract(logical_date: str) -> str:
        day = pendulum.parse(logical_date).format("YYYY-MM-DD")
        return extract_events(day)  # returns a manifest path, not data

    @task
    def transform(manifest_path: str) -> str:
        return transform_events(manifest_path)

    @task
    def load(manifest_path: str) -> str:
        return load_mart(manifest_path)

    @task
    def check(mart_path: str) -> None:
        assert_row_count_and_freshness(mart_path)

    manifest = extract()
    transformed = transform(manifest)
    mart = load(transformed)
    check(mart)


daily_revenue()
```

Design points, each of which fixes a real class of bug:

- **Pass paths, not DataFrames.** Task results are stored in the metadata database. Returning a
  large DataFrame serializes it into that database and will eventually break it. Return a
  manifest path or a partition key.
- **`catchup=False` unless you mean it.** With catchup on, enabling a DAG backfills every
  missed interval at once.
- **`max_active_runs`** bounds concurrency so a backlog does not stampede your source system.
- **`dagrun_timeout`** stops a hung task from occupying a slot forever.
- **`retries` with `retry_delay`** absorbs transient failures. Retrying a non-idempotent task
  is how you get double counts.
- **`doc_md` and `tags`** are documentation that shows up in the UI. Use them.

### Idempotency

A task must produce the same result whether it runs once, twice, or after a partial failure.

| Not idempotent | Idempotent rewrite |
|---|---|
| `INSERT INTO` new rows each run | Overwrite the partition for the run's logical date |
| Append to a table | `INSERT OVERWRITE` semantics, or `MERGE` on a key |
| Create a table if missing | Drop and create, or `CREATE OR REPLACE` |
| Increment a counter column | Recompute from source |
| Send a downstream notification | Send once, keyed by run id, with an idempotency key |

The general technique: **derive every output artifact from a deterministic partition key derived
from the run's data interval, and always replace that partition rather than appending to it.**

```python
from airflow.decorators import task


@task
def load_partition(logical_date: str) -> str:
    day = pendulum.parse(logical_date).format("YYYY-MM-DD")
    target = f"s3://warehouse/revenue/event_date={day}"
    # Replace the whole partition so a retry cannot duplicate it.
    delete_prefix(target)
    write_partition(target, rows=transform(day))
    return target
```

### Sensors

A sensor waits for something to exist. The decision that matters is **poke mode versus reschedule
mode**.

- **Reschedule** releases the worker slot while waiting. Use it for anything longer than a few
  minutes.
- **Poke** holds a slot. Use it only for very short waits.

```python
# Provider import paths move between Airflow releases. Find the current one with:
#   python -c "import airflow.providers, pkgutil; print([m.name for m in pkgutil.iter_modules(airflow.providers.__path__)])"
from airflow.providers.standard.sensors.external_task import ExternalTaskSensor


wait_for_source = ExternalTaskSensor(
    task_id="wait_for_ingest",
    external_dag_id="raw_event_ingest",
    mode="reschedule",
    timeout=60 * 60,
    poke_interval=5 * 60,
)
```

Sensors that never time out are the most common cause of a queue full of running DAGs. Always
set `timeout`.

### Dependencies and triggers

Python call order is dependency order in TaskFlow, but sometimes you need control flow logic.

| `trigger_rule` | Fires when |
|---|---|
| `all_success` (default) | All upstream tasks succeeded |
| `all_done` | All upstream finished, whatever the outcome |
| `none_failed` | No upstream failed |
| `none_failed_min_one_success` | No upstream failed and at least one succeeded |
| `one_failed` | At least one upstream failed |
| `always` | Every upstream finished |

Use `none_failed_min_one_success` for "run only if we have new data". Use `all_done` for cleanup
and alerting that must run even on failure.

### Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Double counts on retry | Task appends | Write by partition, replace, key on a natural id |
| DAG runs before data is ready | No sensor, or an aggressive schedule | Sensor with reschedule mode and a timeout |
| Slots full of waiting tasks | Poke-mode sensors with long timeouts | Switch to reschedule mode |
| Backfill creates thousands of runs | `catchup=True` | Set `catchup=False`, backfill deliberately |
| Task returns a huge object | DataFrames in XCom | Return paths |
| Upstream retries for hours | Retry policy on a permanent failure | Classify errors; do not retry bad data |
| Partial runs never cleaned up | Missing cleanup task | Cleanup task with `trigger_rule="all_done"` |
| Silent skips | `depends_on_past` plus a late schedule | Inspect task instance history before assuming |

Useful commands (verify with `airflow dags --help` for your version):

```bash
airflow dags list
airflow dags list-import-errors
airflow dags test daily_revenue 2026-01-15
airflow dags show daily_revenue
airflow tasks test daily_revenue extract 2026-01-15
```

`dags test` runs a task locally with a real execution context without scheduling. It is the
fastest way to reproduce a broken task.

## dbt

dbt turns SQL files into a dependency graph and runs them in order. The value is the graph, the
incremental logic, and the tests. The value is not the templating.

### Project layout

```
dbt-project.yml
profiles.yml              # connection config, usually outside the repo
models/
  staging/                # one model per source table, renamed, typed, deduplicated
    stg_orders.sql
    stg_customers.sql
  intermediate/           # joins and business logic, no metrics
    int_orders_joined_customers.sql
  marts/                  # the tables and models consumers use
    fct_orders.sql
    dim_customers.sql
seeds/                    # small static CSVs loaded into the warehouse
macros/                   # reusable SQL
tests/                    # singular tests, one assertion each
```

Three layers, one job each:

| Layer | Purpose | Rules |
|---|---|---|
| staging | Normalize a source into a clean table | One model per source, no joins, no business logic |
| intermediate | Join and reshape | Reusable, no consumer-facing names |
| marts | Serve a use case | Named for the consumer, owned by a team |

Discipline that pays: **a staging model is a one-to-one mapping of a source object**. No joins,
no filters beyond what the source guarantees, no renaming that loses meaning. That way, when the
source changes, exactly one file changes.

### Example models

```sql
-- models/staging/stg_orders.sql
{{ config(materialized='view') }}

select
    cast(order_id as text)                      as order_id,
    cast(customer_id as text)                    as customer_id,
    cast(ordered_at as timestamp)                as ordered_at,
    cast(status as text)                         as status,
    cast(amount_minor / 100.0 as decimal(12, 2)) as amount
from {{ source('raw', 'orders') }}
where _deleted_at is null
```

```sql
-- models/marts/fct_orders.sql
{{ config(
    materialized='incremental',
    unique_key='order_id',
    on_schema_change='append_new_columns',
    partition_by={'field': 'order_date', 'data_type': 'date'}
) }}

select
    order_id,
    customer_id,
    order_date,
    status,
    amount
from {{ ref('int_orders_joined_customers') }}
{% if is_incremental() %}
where order_date > (select coalesce(max(order_date), '1900-01-01') from {{ this }})
{% endif %}
```

Rules for incremental models:

- Always provide `unique_key`. Without it dbt appends and duplicates.
- The `is_incremental()` filter must be **stable and monotonic**, otherwise you reprocess rows.
- Handle `on_schema_change` explicitly. The default behavior on a column change is to fail.
- Verify the `partition_by` configuration keys against the dbt adapter documentation for your
  warehouse; adapter support varies.

### Tests

```sql
-- models/marts/schema.yml
version: 2

models:
  - name: fct_orders
    description: One row per order, current status only.
    columns:
      - name: order_id
        description: Primary key.
        data_tests:
          - unique
          - not_null
      - name: status
        data_tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'delivered', 'cancelled', 'refunded']
      - name: customer_id
        data_tests:
          - relationships:
              arguments:
                to: ref('dim_customers')
                field: customer_id
```

Note: newer dbt versions renamed the generic test key from `tests` to `data_tests`. Check your
dbt version's documentation before writing new config, and keep a single convention across the
project.

Singular tests are SQL that returns failing rows:

```sql
-- tests/assert_order_amount_is_positive.sql
select order_id, amount
from {{ ref('fct_orders') }}
where amount <= 0
```

A test passes when it returns zero rows. Write them as one assertion per file, with a name that
says what is broken when it fails.

`dbt build` runs models, tests, seeds, and snapshots in dependency order and stops downstream of
a failure. Use it as the single CI entry point.

### Useful commands

```bash
dbt deps
dbt parse
dbt compile --select fct_orders+
dbt build --target ci
dbt test --select fct_orders
dbt docs generate
dbt run --select +fct_orders --full-refresh
```

`dbt parse` is fast and catches configuration errors without touching the warehouse. Put it in
pre-commit.

## Data quality

Six dimensions to check, and the check belongs in the pipeline, not in a dashboard discovered
three weeks later:

| Dimension | Test |
|---|---|
| Completeness | Required columns present, expected row count for the interval |
| Uniqueness | Natural keys are unique |
| Validity | Values in an allowed set, types and ranges correct |
| Consistency | Cross-source agreement, referential integrity |
| Timeliness | Freshness: newest data is within the SLA |
| Accuracy | Sampled or reconciled against a source of truth |

Where checks live:

| Layer | Check type | Example |
|---|---|---|
| Ingest | Schema and structural | Schema drift alarm, null-key alarm |
| Transform | Row-level tests | dbt tests, singular SQL |
| Publish | Reconciliation | Mart total matches the transactional source |
| Continuously | Statistical monitors | Freshness, volume anomaly, distribution shift |

An anomaly monitor should page only when a human would act. Alert fatigue is the fastest way to
get every quality check ignored.

## Warehouse versus lake versus lakehouse

| | Warehouse | Object storage lake | Lakehouse |
|---|---|---|---|
| Write cost | Highest | Lowest | Low |
| Query performance on large scans | Best | Depends on the engine | Near warehouse, on open tables |
| Schema enforcement | Strong | Weak by default | ACID tables give strong guarantees |
| Best for | Concurred BI, governed metrics | Raw retention, ML training | Both, on open formats |

Decide by access pattern and governance needs, not by fashion. A warehouse over Parquet on
object storage, with the raw data kept in a lake for retention and replay, is a common and
sensible end state.

## Lineage and orchestration

| Concern | Tooling | Note |
|---|---|---|
| Column-level lineage | dbt docs, OpenLineage, DataHub, Marquez | Start at table level, add column level where it pays |
| Cost attribution | Warehouse tagging, per-team projects | Tag every job and every table from day one |
| Scheduling | Airflow, Dagster, Prefect, Argo, Flyte, cloud-native schedulers | Pick on operational fit, not features |
| Long-running workflows as code | Temporal | Strong fit when the workflow is a durable process, not a data pipeline |

Orchestrator selection rule: if your work is data-shaped and time-triggered, use a data
orchestrator. If it is a long-lived business process with signals, timers, and human steps, use a
durable workflow engine. Mixing the two is the usual source of two competing schedulers for one
pipeline.

## Common mistakes

- **Append instead of replace.** Symptom: numbers double after a retry. Cause: the task is not
  idempotent. Fix: write by partition and replace.
- **Poke-mode sensors holding workers.** Symptom: the pool is exhausted, DAGs show running with
  no progress. Fix: `mode="reschedule"`.
- **Returning DataFrames between tasks.** Symptom: the metadata database grows without bound and
  task runs slow down. Fix: pass paths.
- **No `catchup` decision.** Symptom: a scheduler enable triggers thousands of historical runs.
  Fix: set it explicitly.
- **Incremental model with no `unique_key`.** Symptom: duplicate rows accumulate on every run.
  Fix: supply the key and verify it is truly unique.
- **Partitioning by a high-cardinality column.** Symptom: millions of tiny directories and
  metastore strain. Fix: partition by a low-cardinality column, often a date.
- **Reading a file a writer is still producing.** Symptom: truncated or corrupt rows. Fix:
  write to a temp path and rename, or publish a manifest only after the write completes.
- **Tests that only run after the mart is built.** Symptom: a bad transform propagates. Fix: put
  tests where the failure is, and stop downstream work on failure.

## Anti-patterns

- **One mega-DAG.** Dozens of unrelated steps in one DAG means one unrelated failure blocks
  everything and the graph is unreadable.
- **A DAG per table.** Explodes the scheduler's work and the UI. Group by pipeline, not by
  table.
- **Business logic in Airflow Python instead of SQL or in the marts.** Python in the scheduler is
  hard to test and hard to review.
- **Staging models with joins.** They become unreviewable and un-debuggable.
- **`catchup=True` by default.**
- **Poking forever.** Every wait needs a timeout.
- **Testing only the final table.** Failures then have to be traced backward through the whole
  graph.
- **Assume a schema and skip the check.** Sources drift; a contract or at least a schema
  assertion prevents a 3 a.m. page.
- **Treating the pipeline as the backup.** Raw retention is a separate, deliberate decision.

## Checklist

- Every task is idempotent and safe to retry.
- Every output is written to a deterministic partition and replaced, not appended.
- Tasks pass paths and keys, not data.
- Sensors use reschedule mode and have timeouts.
- `catchup`, `max_active_runs`, `dagrun_timeout`, and retry policy are set deliberately.
- Files are 128 MB to 1 GB, written once by one writer, compacted when fragmented.
- Parquet for scans, Avro for record streams, an open table format if you need updates.
- Partitions are low-cardinality and match the filter patterns of real queries.
- dbt has staging, intermediate, and marts layers, and staging is one-to-one with sources.
- Incremental models have a `unique_key` and a monotonic incremental filter.
- `dbt build` runs in CI, with `dbt parse` in pre-commit.
- Freshness and row-count checks exist for every published table.

## References

- Apache Airflow documentation: https://airflow.apache.org/docs/
- Airflow DAG best practices: https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html
- Airflow core concepts and the reference: https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/index.html
- Airflow CLI reference: https://airflow.apache.org/docs/apache-airflow/stable/cli-ref.html
- dbt documentation: https://docs.getdbt.com/docs/introduction
- dbt project structure: https://docs.getdbt.com/docs/introduction/how-dbt-works
- dbt incremental models: https://docs.getdbt.com/docs/build/incremental-models
- dbt best practices: https://docs.getdbt.com/docs/collaborate/govern/best-practices
- Parquet documentation: https://parquet.apache.org/docs/
- Apache Avro specification: https://avro.apache.org/docs/current/spec.html
- Apache ORC site: https://orc.apache.org/specification/
- Delta Lake: https://docs.delta.io/latest/index.html
- Apache Iceberg: https://iceberg.apache.org/
- Kafka documentation: https://kafka.apache.org/documentation/
- Apache Flink documentation: https://nightlies.apache.org/flink/flink-docs-stable/
- Debezium documentation: https://debezium.io/documentation/reference/
- OpenLineage: https://openlineage.io/docs/
- DataHub: https://datahubproject.io/docs/
- Great Expectations: https://docs.greatexpectations.io/docs/core
- Apache Arrow columnar formats: https://arrow.apache.org/docs/python/api/datatypes.html