---
name: System Design
description: Covers requirements and constraints, capacity estimation, component selection, high level architecture diagrams, data flow, vertical and horizontal scaling, stateless services, load balancing, caching layers, database selection, consistency and availability tradeoffs, availability numbers and failure domains, and presenting tradeoffs for design review. Use when the user asks to design a system, sketch an architecture, estimate capacity, choose between a database or a queue, plan for scale, or prepare for a system design interview or review.
metadata:
  opencode/autoinvoke: true
---

# System Design

Design is the act of choosing what to build now, what to defer, and what to be able to change
later, and being able to say why. A design is not done when the diagram is drawn. It is done
when someone else can read the diagram, the numbers, and the stated tradeoffs, and predict
what breaks first.

## When to use this

- The user asks for an architecture for a new system or a redesign of an existing one.
- You need to size something: instances, storage, bandwidth, database capacity.
- You need to pick between components: SQL or NoSQL, monolith or services, sync or async.
- You are preparing for a design review, a technical proposal, or a design interview.
- An existing system is at a limit and you need to decide what to do next.

## The shape of an answer

```
1. Requirements      functional, non-functional, explicit non-goals
2. Constraints       team, timeline, budget, existing systems, regulations
3. Estimation        traffic, storage, bandwidth, growth
4. Data model        entities, access patterns, volumes
5. High level        components and why each one exists
6. Data flow         the two or three critical paths, drawn
7. Scaling strategy  what happens at 10x, in order
8. Reliability       failure domains, SLOs, degradation
9. Tradeoffs         what you chose against, and the trigger to revisit
```

Skip steps 3 and 9 and the design is an opinion.

## Requirements

Separate three things, explicitly:

| Category | Examples |
|---|---|
| Functional | "Users can upload a document and search it" |
| Non-functional | Latency, availability, durability, consistency, scale, cost |
| Non-goals | "Real-time collaboration is out of scope for v1" |

Write non-goals down. They are the most valuable line in the document, because they are what
stops scope from expanding silently during implementation.

Ask the questions that change the architecture:

- Read or write heavy? Reads dominate most designs and reward caching.
- Users and geography? Region changes data residency and latency requirements.
- Tenant model? Single tenant, shared schema, or isolated per customer changes data
  partitioning, isolation, and cost.
- Growth? Ten times now, or ten times over three years.
- Data lifetime? Hot for hours, warm for months, deleted after N days.
- Correctness? Is a stale inventory count acceptable, or does a double charge matter?

## Capacity estimation

Always do the arithmetic out loud. Approximate numbers, clearly labeled, are better than
confident hand-waving.

```
DAU            1,000,000
requests/user  50 per day
requests/day   50,000,000
peak factor    3x over the daily average
peak RPS       50,000,000 / 86,400 * 3  ~= 1,740 RPS

per request: 200 bytes in, 2 KB out
peak egress  1,740 * 2 KB              ~= 3.4 MB/s  ~= 290 GB/day

state per user: 10 KB
primary store   1,000,000 * 10 KB       ~= 10 GB
plus indexes and replicas: budget 3x    ~= 30 GB
```

Two rules:

- **Do not divide by 24.** Peak is the design point. Average hides the load that causes outages.
- **State your assumptions.** "Assuming 50 requests per user per day and 3x peak" makes the
  number auditable. A bare number is not.

Estimation output that is useful in a review: peak throughput, storage now and at 12 months,
bandwidth, and the single assumption the whole design is most sensitive to.

## Data model first

Most design debates are actually data model debates. Establish:

- Entities and their identities.
- Access patterns, written as queries. "List the 20 most recent orders for this customer,
  ordered by date" tells you the index.
- Cardinality and volume of each table.
- Which queries are on the critical path and must be fast.
- Which relationships must be enforced and which can be eventual.

```
orders
  PK  order_id
  FK  customer_id
      status          enum
      created_at      ts
  IDX (customer_id, created_at DESC)   -- serves "recent orders for customer"
```

Every index has a write cost. Every foreign key has a lock and a migration cost. Say what each
one buys.

## Component selection

| Decision | Common split | Notes |
|---|---|---|
| One database vs several | One primary store plus specialized stores for search, cache, analytics | More stores means more consistency work |
| Monolith vs services | Modularity inside one deployable first | Services cost latency, ops, and debugging |
| Relational vs document | Relational by default; document for genuinely variable, read-by-key shapes | Model the access pattern before the schema |
| Sync vs async | Sync inside the user request; async for everything else | Async needs a retry story from day one |
| Build vs buy | Build the differentiator; buy the commodity | Ops cost is real and ongoing |

Database selection by access pattern:

| Pattern | Good fit |
|---|---|
| Transactional, relational, joins | Relational database |
| Known key, high read rate, no joins | Key-value or wide-column store |
| Full-text search, relevance ranking | Search engine |
| Aggregate by time range, scans | Columnar store or warehouse |
| Append-only events, fan-out | Log or broker |
| Hot sub-second read of derived data | In-memory cache |

## Scaling

**Vertical** is simpler and cheaper to operate: bigger instance, faster disk, more memory. Start
here. It has a ceiling, but you rarely hit it early, and hitting it later is a normal event
rather than an emergency.

**Horizontal** needs stateless services.

```
stateful instance + load balancer
   |  scale up: larger machine, more memory        <- simple, has a ceiling
   |  scale out: more machines                     <- needs statelessness

stateless services behind a load balancer
   |  scale out: add replicas                       <- horizontal
   |  requires: no local session, no local file, no in-process session lock
```

Making a service stateless:

- Session in a signed cookie or a shared store, not in process memory.
- Files and uploads in object storage, not on local disk.
- No coordination through in-process state. If two replicas must coordinate, that
  coordination is in a shared store or it is a bug waiting for a scale event.
- Sticky sessions are a hiding place, not a fix. They work until a replica restarts.

Load balancing:

| Strategy | Use when | Cost |
|---|---|---|
| Round robin | Requests are homogeneous | None |
| Least connections | Requests vary in duration | Needs connection state |
| Weighted | Heterogeneous fleet | Fleet config to maintain |
| Consistent hashing | Caches or sharded state | Rebalancing on node loss |
| Session affinity | Inevitable but undesirable | Masks statelessness bugs |

Sticky routing and least-connections both break the "add a replica any time" property. Treat
them as compromises with a stated cost.

## Caching layers

```
client / CDN  ->  reverse proxy  ->  application cache  ->  database
     L1              L2                   L3                  source of truth
```

Each layer has a different cost and a different invalidation problem.

| Layer | Saves | Risks |
|---|---|---|
| CDN | Origin bandwidth and latency | Stale content, cache-key confusion |
| Reverse proxy | Origin connections | Same |
| Application cache | Database load | Invalidation, stampede on expiry |
| Database-side cache | Query planning and IO | Memory pressure |

Rules:

- **Cache-key design is the hard part.** Include every input that changes the response: tenant,
  locale, auth scope, version. A key missing one of those is an authorization bug.
- **Cache-aside, not write-through**, unless you need read-your-writes and can pay for it.
  Write-through couples cache availability to cache correctness.
- **Bound every entry**: TTL plus a maximum size. Unbounded caches are memory leaks with a
  latency cost.
- **Stampede protection**: on expiry, one request refreshes while others serve stale, or a lock
  serializes refreshers. Without it, a popular key expiring becomes an outage.
- **Never cache a failure**, and do not cache a 5xx as if it were a value.
- Measure the hit rate. A cache at 20 percent hit rate is pure overhead.

```python
import json
import random
import time
from typing import Any

CACHE_TTL_SECONDS = 300
FRESH_WINDOW_SECONDS = 5


def cache_get(cache, key: str) -> tuple[Any, bool]:
    """Return (value, hit). Uses early expiry so one caller refreshes for the rest."""
    raw = cache.get(key)
    if raw is None:
        return None, False
    value, expires_at = json.loads(raw)
    if time.time() < expires_at - FRESH_WINDOW_SECONDS:
        return value, True
    # Stale but usable. Let one caller win the refresh race.
    if random.random() > 0.02:
        return value, True
    return value, False
```

A real implementation must do the refresh under a lock or with compare-and-set so only one
caller refreshes. The pattern above is illustrative; the early-expiry window plus a probabilistic
guard is the shape.

## Consistency and availability

The CAP framing is often misused. What actually matters in most systems is the tradeoff
between **strong consistency** and **availability during a network partition**, expressed per
operation rather than per system.

| Consistency model | What you get | What it costs | Use for |
|---|---|---|---|
| Strong / serializable | One total order, no anomalies | Latency, availability, throughput | Money, inventory, unique constraints |
| Read-your-writes | You see your own update | Session or version tracking | User-visible edits |
| Monotonic reads | Never go back in time | Session token | Feeds and dashboards |
| Eventual | Converges eventually | Stale reads, conflict resolution | Analytics, recommendations, caches |
| Bounded staleness | Converges within N seconds | Slightly more state | Search indexes, profile views |

Pick per operation, not per system. A single database can offer strong consistency for orders and
eventual consistency for profile views, and that is usually the right answer.

Say out loud which operations are strong and which are eventual, and what a user sees in the
window between them. That is the sentence a reviewer will look for.

## Availability numbers and failure domains

Availability is the product of the availability of everything in the request path. Two
components each at 99.9 percent, if both must succeed, compose:

```
0.999 * 0.999 = 0.998001  ->  99.80 percent
```

That is why adding components reduces availability unless you add redundancy too.

Definitions: availability is measured over a window; the number of nines is not a design, it is
a consequence of the redundancy you built. Error budgets make this concrete:

```
SLO 99.9 percent over 30 days  ->  error budget ~= 43 minutes
SLO 99.95 percent over 30 days ->  error budget ~= 21.7 minutes
SLO 99.99 percent over 30 days ->  error budget ~= 4.3 minutes
```

(Exact values depend on the window length; compute yours.)

Failure domains to name explicitly:

| Domain | Example blast radius |
|---|---|
| Process | One replica crashes |
| Instance | One host fails |
| Availability zone | One datacenter zone unavailable |
| Region | Whole region unavailable |
| Dependency | One downstream service |
| Deployment | One bad release |
| Data | One table, one index, one shard |
| Team or config | One operator, one changed flag |

For each component, state: how many failure domains are spanned, and what happens when one is
lost. "Multi-AZ" without a number and a plan is not a design decision.

Degradation is usually the cheapest availability:

| Tier | Behavior when the dependency is down |
|---|---|
| Critical | Fail the request, page |
| Degraded | Serve a reduced response, serve stale, queue |
| Optional | Hide the feature |

## Designing for review

A design document that gets approved usually has these:

- The problem and non-goals, in two paragraphs.
- The numbers, with assumptions stated.
- A diagram with data flow arrows, not just boxes. Boxes are components; arrows are behavior.
- The two or three paths that matter, described step by step, including failure.
- A table of components with **one line each on why it exists and what happens if it is down**.
- Explicit tradeoffs, each with a trigger that would make you revisit it.
- Open questions with owners.

Tradeoff statements are what separate a design from a proposal:

> Chose a relational database for orders because the write path needs transactions over
> inventory. Cost: horizontal write scaling requires sharding later. Revisit if single-writer
> throughput becomes the bottleneck before we need to shard.

The trigger is the part people skip. Without it, the decision is permanent by default.

### ASCII diagram

```
                       +--------------------+
   users  -----------> |   CDN / edge       |  static assets, cache
                       +---------+----------+
                                 |
                       +---------v----------+
                       |  Load balancer     |  health checks, routing
                       +---------+----------+
                                 |
        +-------------+----------+----------+-------------+
        |             |                     |             |
  +-----v-----+ +-----v-----+        +-----v-----+  +-----v-----+
  | API v1    | | API v2    |        | Worker     |  | Scheduler |
  | stateless | | stateless |        | queue      |  | batch     |
  +-----+-----+ +-----+-----+        +-----+-----+  +-----------+
        |             |                    |
        +------+------+--------------------+
               |
      +--------v---------+
      | Primary store    |  orders, inventory (strong consistency)
      | replicas, backups|
      +--------+---------+
               |
      +--------v---------+
      | Cache            |  sessions, hot reads (bounded TTL)
      +--------+---------+
               |
      +--------v---------+        +--------------------+
      | Search index     | <----> | Object storage     | documents
      | (eventual)       |        |                    |
      +------------------+        +--------------------+
```

Label every arrow with what flows and roughly how often. An unlabeled arrow is a place where the
design hides an assumption.

## Common mistakes

- **Designing for the average.** Symptom: works in staging, falls over at peak. Fix: size on
  peak, with headroom.
- **No non-goals.** Symptom: scope grows during implementation, deadlines slip. Fix: write them
  down and reference them in review.
- **Consistency chosen by accident.** Symptom: a "why is the balance wrong" bug report. Fix:
  state per operation which model applies.
- **Cache without a hit-rate metric.** Symptom: added latency, no traffic reduction. Fix: measure
  before and after.
- **Cache key missing the tenant or auth scope.** Symptom: one user sees another's data. This is
  a security incident, not a performance bug.
- **Availability quoted as a component number.** Symptom: the composed number is far worse.
  Fix: compute the product and state the SLO.
- **A dependency called "critical" with no degraded path.** Fix: classify every dependency and
  define the degraded behavior.
- **Sharding before the write path needs it.** Symptom: operational complexity forever, zero
  benefit. Fix: shard when a measured limit requires it, not preemptively.
- **Testing only the happy path in the design.** Fix: walk one failure through each path.

## Anti-patterns

- **Microservices by default.** Distributed systems add latency, partial failure, and debugging
  cost. Split when team boundaries and scaling requirements actually demand it.
- **Event sourcing everywhere.** Powerful, and a large commitment. Use it where history and
  replay matter, not as a default.
- **A distributed cache in front of a database that was never tuned.** Fix the query first.
- **Writing a diagram with no arrows.** Boxes are not a design.
- **Choosing a technology by trend.** Choose by constraint: team skill, operational maturity,
  existing support surface.
- **Abstracting before the second implementation exists.** Premature generality is
  unmaintainable specificity.
- **SLOs at 99.99 with no error budget discipline.** A number nobody spends is decoration.
- **Ignoring the boring parts**: backups that are never restored, alerts nobody reads,
  runbooks that do not match the system.

## Checklist

- Functional requirements, non-functional requirements, and non-goals are written down.
- Capacity numbers show peak, state assumptions, and include growth.
- Access patterns are written as queries and drive the index choices.
- Every component has one line on why it exists and its degraded behavior.
- The design says which operations are strongly consistent and which are eventual.
- Failure domains are named per component with a number of zones or replicas.
- The critical path is drawn with labeled arrows, including one failure path.
- Each tradeoff has a trigger that would make you revisit it.
- Backups exist and a restore has been tested.
- Load shedding and graceful degradation are defined.

## References

- AWS Architecture Center: https://aws.amazon.com/architecture/
- AWS Well-Architected Framework, Reliability pillar: https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html
- AWS Well-Architected Framework, Performance Efficiency pillar: https://docs.aws.amazon.com/wellarchitected/latest/performance-efficiency-pillar/welcome.html
- Google Cloud Architecture Framework: https://cloud.google.com/architecture/framework
- Microsoft Azure Architecture Center: https://learn.microsoft.com/azure/architecture/
- The Site Reliability Book (Google): https://sre.google/books/
- Google SRE Workbook, implementing SLOs: https://sre.google/workbook/implementing-slos/
- Martin Kleppmann, Designing Data-Intensive Applications, publisher page: https://dataintensive.net/
- Jepsen analyses database consistency behavior: https://jepsen.io/analyses
- PostgreSQL documentation, concurrency control: https://www.postgresql.org/docs/current/mvcc.html
- DynamoDB developer guide, eventual consistency: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadConsistency.html
- NIST SP 800-34 contingency planning: https://csrc.nist.gov/pubs/sp/800/34/r1/final