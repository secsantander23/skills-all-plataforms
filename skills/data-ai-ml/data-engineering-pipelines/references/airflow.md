# Airflow Design Reference

Airflow's API surface changes between major releases. Import paths in particular have moved.
Before you trust a snippet, check the version you have installed:

```bash
python -c "import airflow; print(airflow.__version__)"
airflow version
airflow dags --help
```

Stable DAG-authoring concepts, described as concepts rather than as a fixed API, are listed
below. Map them to the current API with `--help` and the docs for your version.

## DAG-level parameters worth setting deliberately

| Parameter | Effect if unset | Guidance |
|---|---|---|
| `dag_id` | None | Stable, unique, never reused for a different meaning |
| `schedule` | None | A cron or timedelta string; use a data-driven schedule where possible |
| `start_date` | None | Set it, otherwise backfill behavior is undefined |
| `catchup` | Version-dependent | Set it explicitly on every DAG |
| `max_active_runs` | Unbounded | Cap it so a backlog cannot stampede a source |
| `dagrun_timeout` | Unbounded | Always set it |
| `default_args` | None | Centralize retries, retry delay, and owner |
| `tags` | None | Ownership and filtering in the UI |
| `doc_md` | None | What it does, who owns it, the completion SLO |
| `params` | None | Runtime configuration surfaced to tasks |

## Time in Airflow

Airflow runs a DAG for a **data interval**, identified by a logical date (the start of the
interval). Tasks receive the interval start and end through templated context values such as
`data_interval_start` and `data_interval_end`, and legacy equivalents `ds` and `ds_nodash`.

Rules:

- Derive everything a task writes from the **data interval**, not from `datetime.now()`. A task
  that reads the wall clock cannot be backfilled or re-run correctly.
- `run_id` identifies one run of one interval. Use it for idempotency keys and for downstream
  deduplication.
- When a DAG reads data that another DAG produces, depend on the *interval*, not on wall-clock
  completion. "Yesterday at 6 a.m." is not a dependency, it is a coincidence.

## Passing data between tasks

| Mechanism | Use for | Avoid when |
|---|---|---|
| TaskFlow return values (small metadata) | Paths, counts, keys | Never for datasets |
| Object store | Datasets of any size | Small values, where it adds latency |
| Database table | Structured intermediate data | Very small things |
| TaskFlow XCom (small values) | Flags and identifiers | Anything large or high volume |

XCom values are persisted in the metadata database. A DataFrame returned from a task ends up
serialized into that database and replicated to every webserver. Pass a path.

## Sensors

| Sensor style | Use when | Failure mode if misused |
|---|---|---|
| Poke mode | Very short waits (seconds) | Holds a worker slot for the whole wait |
| Reschedule mode | Anything longer than a minute | Slightly higher latency per check |
| Deferred tasks | Long external waits on supported providers | Requires a trigger config to resume |

Always set a timeout. An unbounded sensor is the single most common cause of a stuck queue.

Provider sensor import paths differ across releases. Locate the current one before copying a
snippet:

```bash
python -c "import airflow.providers, pkgutil; print([m.name for m in pkgutil.iter_modules(airflow.providers.__path__)])"
```

## Deferrable and triggerer-based waits

Newer releases support deferring work to a triggerer so a waiting task occupies no worker at
all. That is the right choice for long external waits. Support and configuration differ by
provider and release, so verify before adopting.

## Task dependencies beyond Python ordering

- Plain Python call order establishes dependencies in TaskFlow.
- Use `>>` or `set_upstream` / `set_downstream` when the order is not expressed by calls.
- `depends_on_past=True` makes a task wait for the same logical date in the previous run. Useful
  for strictly sequential daily processing; harmful when the upstream sometimes has no data,
  because the task then never runs.
- `trigger_rule` controls what upstream states permit a task to run.

## Backfills

Backfills are the reason idempotency matters. Before any backfill:

1. Confirm every task in the DAG is idempotent.
2. Confirm your task can handle a historical data interval whose upstream data may have been
   reprocessed or deleted.
3. Run a small range first, on a copy of production if the writes are not transactional.
4. Add a concurrency guard so the backfill and the normal schedule do not write the same
   partition at the same time.

## Testing DAGs

| Level | Tool | What it catches |
|---|---|---|
| Import check | `airflow dags list-import-errors` | Syntax errors, missing deps, bad DAG files |
| Local run | `airflow tasks test <dag_id> <task_id> <logical_date>` | Task-level logic in a real context |
| Full DAG, no schedule | `airflow dags test <dag_id> <logical_date>` | Cross-task wiring |
| Unit tests | pytest against task functions | Logic without a scheduler |
| Data assertions | In the pipeline itself | Wrong output |

Extract business logic out of DAG files into ordinary functions. Test those with pytest. The
DAG file should be wiring, not logic.

## What Airflow is not

- Not a data processing engine. It orchestrates; your jobs do the work.
- Not a latency-sensitive scheduler. Second-level scheduling is not its strength.
- Not a state machine for long-lived business processes with signals and human steps. That is
  what a durable workflow engine is for.

Mixing the two in one pipeline is the usual reason teams end up with two schedulers fighting
over the same data.