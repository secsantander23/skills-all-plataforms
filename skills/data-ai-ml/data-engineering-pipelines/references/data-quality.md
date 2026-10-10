# Data Quality and Testing

## Where checks belong

| Stage | Checks | Failure action |
|---|---|---|
| Ingest | Schema present, keys non-null, volume in range, no unexpected new columns | Reject the batch, alert the producer |
| Transform | Row-level assertions per model | Stop downstream work |
| Publish | Reconciliation against a source of truth | Block the publish |
| Continuously | Freshness, volume anomaly, distribution shift | Page or ticket |

A check that does not stop something is a report, not a check.

## The six dimensions, with concrete tests

### Completeness

- Row count for the interval is within an expected band.
- Required columns are non-null above a threshold.
- Every expected file or partition arrived.

```sql
select
    event_date,
    count(*) as rows_loaded
from {{ ref('stg_events') }}
where event_date = (select max(event_date) from {{ ref('stg_events') })
group by event_date
having count(*) = 0;
```

### Uniqueness

```sql
select order_id, count(*) as n
from {{ ref('fct_orders') }}
group by order_id
having count(*) > 1;
```

### Validity

```sql
select order_id, status
from {{ ref('fct_orders') }}
where status not in ('placed', 'shipped', 'delivered', 'cancelled', 'refunded');
```

Types, ranges, formats, and referential integrity all belong here.

### Consistency

Cross-system agreement that no single system can check alone:

```sql
select
    o.order_id,
    o.amount as order_amount,
    sum(p.amount) as paid_amount
from {{ ref('fct_orders') }} as o
join {{ ref('fct_payments') }} as p on p.order_id = o.order_id
group by o.order_id, o.amount
having abs(o.amount - sum(p.amount)) > 0.01;
```

Reconciliation against the transactional system of record is the strongest check you have, and
the one most often skipped. Run it at least daily on the numbers that matter most.

### Timeliness

```sql
select
    max(loaded_at) as latest,
    current_timestamp - max(loaded_at) as lag
from {{ ref('stg_events') }};
```

Compare lag to an SLA. The alert fires on the SLA, not on a fixed arbitrary threshold.

### Accuracy

Accuracy cannot be checked automatically in general. Sample and reconcile, or model it with a
human-labeled set.

## Anomaly detection on volumes

```text
today's rows vs trailing 7-day median
        |
        +-- within band          -> no signal
        +-- above band           -> possible backfill, duplicate ingest, or upstream change
        +-- below band / zero    -> source outage, or a broken partition predicate
```

Use a median and a multiple-based band rather than a mean and a standard deviation, because one
backfill day makes the mean useless. Set the band wide enough that it does not cry wolf; the
cost of an ignored alert is higher than the cost of a reviewed one.

## Schema drift

```text
source schema fingerprint changed?
   |
   +-- new optional column   -> add it, alert at low severity
   +-- new required column   -> add it, alert, check producers
   +-- type change           -> reject the batch, page the producer
   +-- column removed        -> reject, do not silently drop downstream data
```

Fail loudly. A silently dropped column looks like a data-quality incident weeks later, usually
discovered from a business number that stopped moving.

## Data contracts

A contract is an explicit agreement between a producer and a consumer about schema, semantics,
freshness, and change process. Minimum useful content:

- Field names, types, and nullability.
- Semantics: what the field means and its units, which is where most disagreements live.
- Freshness commitment.
- Change process: how breaking changes are announced and by when.

Contracts are enforced by the consumer in CI: reject the batch, or degrade loudly, on a break.
A contract nobody checks is a document.

## Testing strategy

| Layer | What to test | How |
|---|---|---|
| Pure transformation logic | Edge cases, nulls, boundaries | Unit tests on the function, fast, no warehouse |
| Models | Schema, keys, ranges, references | dbt tests, run in CI |
| The pipeline | Partial failure, retry, backfill | Run a backfill range on a copy |
| The number | Reconciliation with the source of truth | Daily check on top metrics |

Test the logic separately from the orchestration. A pure function that takes a DataFrame and
returns a DataFrame can be tested in milliseconds. A DAG that can only be tested by running it
against production is a DAG you will not refactor.

## Alert hygiene

- Page only when a human must act before data is wrong for customers or for a decision.
- Ticket the rest.
- Every alert has an owner and a runbook.
- Track how many alerts fired and were not acted on. If that number grows, the thresholds are
  wrong, not the responders.