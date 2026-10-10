# Capacity and Sizing Worksheet

Work the numbers explicitly. A design review can disagree with a number; it cannot disagree
with a number that was never written down.

## Traffic

```
peak RPS = daily requests / 86,400 * peak factor
```

Peak factor is the ratio of peak second to average second. For consumer products with a daily
cycle it is often in the range of 3 to 10; for business products with working hours it is
higher. Do not assume. Look at your own traffic shape, or use a conservative assumption and say
you did.

Worked example:

```
DAU                    1,000,000
requests per user/day          50
requests per day     50,000,000
peak factor                     3
peak RPS   50,000,000 / 86,400 * 3  ~= 1,736 RPS
mean RPS                                               ~= 579 RPS
```

Headroom: size the steady state at roughly half of provisioned capacity so one instance can fail
without an immediate incident. So provision for about 2x peak RPS if you want one node's worth
of headroom.

## Storage

```
primary bytes = entities * bytes per entity
index overhead ~= 1.3x to 2x the table, depends on the engine and index count
replication     = primary * replica count
history / logs  = events per day * retention days
```

Worked example:

```
users              1,000,000 *   10 KB   =  10 GB
orders       50,000,000/day *   4 KB   = 200 GB/day
orders retained 730 days                    ~= 146 TB
indexes and replication at 2.5x            ~= 365 TB
```

Always write the retention assumption. Storage cost is linear in retention and the number most
often left out of the estimate.

## Bandwidth

```
egress = peak RPS * average response bytes
ingress = peak RPS * average request bytes
```

Peak egress of 3.4 MB/s in the worked example is about 290 GB/day. If responses include images
or video, per-request bytes dominate everything else in this document.

## Database connections

```
max connections on the database
   >= (service replicas * connection pool size) + (admin and batch headroom)
```

Budget from the database side, not the application side. A database with a low connection limit
plus autoscaling application instances is a design that falls over when it scales. Reserve a
fraction of connections for operators, and put a proxy or pooler in front of anything that
cannot be configured.

## Fan-out

```
origin requests = user requests * downstream calls per request
```

A service that fans out to five dependencies multiplies its origin load by five, and inherits
five failure modes. Draw the fan-out on the diagram and label it, because it is invisible
otherwise.

## What to record in the design

| Number | Sensitivity |
|---|---|
| Peak RPS | Sets instance count and load balancer capacity |
| Storage now, and at 12 months | Sets storage tiering, partitioning, cost |
| Peak egress | Sets CDN need and network cost |
| Database connection budget | Sets pooling and replica limits |
| Downstream fan-out | Sets aggregate load on dependencies |
| Growth rate | Sets the date the design runs out |

## Sensitivity

Name the one assumption the design is most fragile to. Examples: requests per user, payload
size, retention period, write-to-read ratio, growth rate. If that assumption is wrong by 2x, the
design does not degrade gracefully, and you now know where to look first.

## Back-of-envelope constants worth keeping in mind

These are order-of-magnitude reminders for framing, not specifications. Measure your own.

- A 32-bit integer is about 4 bytes of storage and two decimal digits.
- A row of roughly ten nullable columns with indexes lands in the hundreds of bytes.
- Random 4 KB reads are latency-bound; sequential reads are bandwidth-bound.
- Ordered inserts and append-only logs are far friendlier to storage than random updates.
- Hash-based indexes grow in memory with the data; ordered indexes support range scans but cost
  more to maintain on writes.

Treat every one of these as a prompt to measure, not as a number to put in the design.