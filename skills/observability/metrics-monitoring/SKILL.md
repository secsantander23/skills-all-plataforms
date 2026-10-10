---
name: metrics-monitoring
description: Metrics and monitoring design covering the four golden signals, RED and USE methods, counters versus gauges versus histograms, Prometheus exposition format and naming rules, PromQL, cardinality control, dashboards that answer questions, and symptom-based SLO alerting that avoids fatigue. Use when the user asks how to instrument metrics, build a Prometheus or Grafana dashboard, write PromQL, reduce label cardinality, or design alerts.
metadata:
  opencode/autoinvoke: true
---

# Metrics and Monitoring

Metrics are numbers over time: cheap, aggregable, and low in cardinality when you keep them
that way. Metrics answer "how often" and "how bad"; they cannot tell you *which* request
failed or *where* in the call tree the time went. That division of labor — metrics for
aggregates, logs for events, traces for causality — is the whole design.

## When to use this

- You are adding instrumentation to a service and choosing what to measure.
- You need to justify a scaling decision with data.
- You are building or fixing a dashboard for an on-call rotation.
- Prometheus is scraping too much, or the metric store is running out of memory.
- An alert fired at 3am and nobody knew what to do.
- You need to define an SLO and alert on it.

## Quick start

Instrument a service with RED metrics, export them, and alert on symptoms.

```python
# Python: prometheus_client with RED metrics for an HTTP handler.
from prometheus_client import Counter, Histogram, Gauge, start_http_server

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)
DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being served",
    ["method"],
)
EXCEPTIONS = Counter(
    "http_request_exceptions_total",
    "Unhandled exceptions in request handling",
    ["method", "route", "exception_type"],
)

def observe(method, route, status, seconds):
    REQUESTS.labels(method, route, status).inc()
    DURATION.labels(method, route).observe(seconds)
```

```javascript
// Node.js: prom-client.
import client from 'prom-client';
import { Registry, collectDefaultMetrics } from 'prom-client';

export const registry = new Registry();
collectDefaultMetrics({ register: registry });

export const httpRequestsTotal = new client.Counter({
  name: 'http_requests_total',
  help: 'Total HTTP requests',
  labelNames: ['method', 'route', 'status'],
  registers: [registry],
});

export const httpRequestDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request duration in seconds',
  labelNames: ['method', 'route'],
  // Set buckets to your SLO targets rather than accepting the default.
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
  registers: [registry],
});

app.get('/metrics', async (_req, res) => {
  res.set('Content-Type', registry.contentType);
  res.end(await registry.metrics());
});
```

```promql
# Error ratio over 5 minutes, by route.
sum by (route) (rate(http_requests_total{status=~"5.."}[5m]))
  /
sum by (route) (rate(http_requests_total[5m]))

# p99 latency over 5 minutes, by route.
histogram_quantile(
  0.99,
  sum by (route, le) (rate(http_request_duration_seconds_bucket[5m]))
)

# Traffic in requests per second.
sum(rate(http_requests_total[5m]))
```

## The four golden signals

From the Google SRE book, the four things to monitor for any service, whether a single
process or a distributed system:

| Signal | Question | Typical metric |
| --- | --- | --- |
| Latency | How long does it take to serve a request? | Histogram of request duration, per route |
| Traffic | How much demand is there? | Request rate, queue depth |
| Errors | What fraction of requests fail? | Error ratio, plus error count by type |
| Saturation | How full are the constrained resources? | Queue depth, thread/connection pool usage, CPU, memory, disk |

Saturation is the leading indicator. Latency and errors lag it: they are what users see
after saturation has already hurt. Alerting on saturation catches the problem earlier, but
it needs interpretation, which is why symptom-based alerting on latency and errors is what
pages you while saturation goes on the dashboard.

## RED versus USE

Two complementary lenses:

**RED** — for request-driven services (HTTP, RPC, API):
- **Rate** — requests per second
- **Errors** — failed requests per second or ratio
- **Duration** — latency distribution

**USE** — for resource-driven components (servers, databases, caches, thread pools):
- **Utilization** — how busy, in the range where work is linear
- **Saturation** — work queued or waiting, the point past which throughput stops improving
- **Errors** — failures of the resource itself

Apply both: RED on the service, USE on everything it depends on. A service with a perfect
RED dashboard and a database with no USE dashboard gives you no clue when the database is the
problem.

## Metric types

| Type | Semantics | Use for | Aggregation |
| --- | --- | --- | --- |
| Counter | Monotonically increasing total | Requests, errors, bytes sent, retries | `rate()`, `increase()` |
| Gauge | Arbitrary value that goes up and down | Queue depth, pool connections, temperature, in-flight requests | `sum()`, `avg()`, `max()`, `min()` |
| Histogram | Observations bucketed by value, with running sum and count | Latency, payload size | `histogram_quantile()` on `_bucket` |
| Summary | Observations with client-side quantiles | Same as histogram, but quantiles are computed at scrape time and cannot be aggregated | `quantile` label |

Prefer a histogram. Summaries produce quantiles that cannot be merged across instances,
which makes "p99 across the fleet" impossible. Reserve summaries for cases where a
histogram's bucket cost is genuinely unacceptable.

A histogram stores, per label combination, a count in each bucket plus a sum and a count.
That is why bucket count multiplies cardinality: `labels × buckets`.

## Prometheus exposition format

```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET",route="/orders/:id",status="200"} 10432

# HELP http_request_duration_seconds HTTP request duration in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{method="GET",route="/orders/:id",le="0.05"} 9032
http_request_duration_seconds_bucket{method="GET",route="/orders/:id",le="0.1"} 10104
http_request_duration_seconds_bucket{method="GET",route="/orders/:id",le="+Inf"} 10432
http_request_duration_seconds_sum{method="GET",route="/orders/:id"} 1841.2204
http_request_duration_seconds_count{method="GET",route="/orders/:id"} 10432

# HELP http_requests_in_flight HTTP requests currently being served
# TYPE http_requests_in_flight gauge
http_requests_in_flight{method="GET"} 12
```

Rules of the format:

- `HELP` and `TYPE` lines precede each metric family; `TYPE` is one of `counter`, `gauge`,
  `histogram`, `summary`, or `untyped`.
- Metric names may contain letters, digits, underscores, and colons. Colons are reserved
  for recording rules by convention.
- Label values are quoted strings; escape backslash, double quote, and newline.
- A sample line is `name{label="value"} value [timestamp_ms]`.
- Every metric family must have the same HELP and TYPE on every exposition from every
  target. Mismatched HELP text is a common source of rejected scrapes.

```yaml
# prometheus.yml: scrape a service.
scrape_configs:
  - job_name: 'api'
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ['api-1.internal:8080', 'api-2.internal:8080']
```

## Naming conventions

Prometheus naming best practice: `namespace_subsystem_name_unit` (plus `_total` for
counters).

```
prometheus_target_sync_length_seconds          # from the official example: <namespace>_<subsystem>_<name>_<unit>
http_request_duration_seconds                   # unit is seconds, not milliseconds
http_response_size_bytes
process_resident_memory_bytes
myapp_queue_depth                              # unitless, so no unit suffix
```

Rules:

| Rule | Example |
| --- | --- |
| Base units: seconds, bytes, meters, grams | `_seconds` not `_ms`, `_bytes` not `_megabytes` |
| Plurals on unit-suffixed metrics | `bytes`, `seconds`, `requests` |
| Counters end in `_total` | `http_requests_total` |
| No labels in the metric name | Do not put the route or status in the name; use labels |
| Consistent label names across metrics | Always `route`, never sometimes `path` |
| Namespace your metrics | `myapp_` prefix prevents collisions |

Converting units is the scrape layer's job. Prometheus scrapers can convert from the
application's unit using a `meter` mapping (milliseconds to seconds), so instrument in the
unit that is natural for your language and configure the conversion.

## PromQL essentials

```promql
# Instant and range queries.
rate(http_requests_total[5m])
max_over_time(process_resident_memory_bytes[1h])

# Aggregation across instances.
sum by (route) (rate(http_requests_total[5m]))
sum without (instance, pod) (rate(http_requests_total[5m]))

# Error ratio with a guard against division by zero.
sum(rate(http_requests_total{status=~"5.."}[5m]))
  /
clamp_min(sum(rate(http_requests_total[5m])), 1)

# Availability from a counter of good and total requests.
sum(rate(http_requests_total_total[5m]))
  - sum(rate(http_requests_total_bad_total[5m]))

# Latency percentiles from a histogram.
histogram_quantile(0.95,
  sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))

# Average latency (mean is easy and sometimes enough).
sum(rate(http_request_duration_seconds_sum[5m]))
  / sum(rate(http_request_duration_seconds_count[5m]))

# Saturation: in-flight relative to the concurrency limit.
sum(http_requests_in_flight) / 100

# An SLO indicator: fraction of good events, as a percentage.
sum(rate(http_requests_total_total[5m]))
  - sum(rate(http_requests_total_bad_total[5m]))
) / sum(rate(http_requests_total_total[5m]))

# Recording rule to precompute an expensive query.
histogram_quantile(0.99,
  sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))
```

Things that trip people up:

- `rate()` and `increase()` need at least two samples in the range. A range shorter than
  `scrape_interval` × 2 returns nothing.
- `rate()` extrapolates and assumes a counter reset when it sees a decrease. That is
  correct for counters, and wrong for anything you modeled as a counter but treat as a
  gauge.
- `histogram_quantile()` interpolates within a bucket. If most observations land in one
  bucket, the "p99" is an interpolation, not a measurement. Choose buckets so your SLO
  target has a bucket boundary near it.
- Aggregating must include `le` in `by (...)` for histograms, or every bucket collapses
  into one.
- `histogram_quantile` over a low-traffic metric is noisy. Compute percentiles over a
  window with enough samples, or use a recording rule at a coarser granularity.

## Cardinality

Cardinality is the number of unique label-value combinations in a metric. It is the main
driver of Prometheus memory and query cost, and it is the most common cause of a metrics
outage.

Series count, roughly:

```
series = product over labels of distinct values, summed over metrics
```

With `method` (4) × `route` (50) × `status` (10) you get 2000 series per histogram, times
buckets for a histogram. Add an instance per replica and the multiplication continues.

```promql
# Top 20 metrics by series count.
topk(20, count by (__name__) ({__name__=~".+"}))

# Series count for one metric.
count({__name__="http_requests_total"})

# Growth over a day, which shows a slow cardinality leak.
count({__name__="http_requests_total"}) - count({__name__="http_requests_total"} offset 1d)
```

Rules to keep cardinality bounded:

- Never label by user ID, email, request ID, session ID, full URL, raw path, error message,
  or any unbounded value. The label value multiplies into every series.
- Label by route pattern, not by the concrete URL.
- Label by status code class or a small enumerated set, not by an arbitrary string the
  application generates.
- Drop labels at the scrape or relabel stage if a dependency adds them.
- Bound the number of label *values*: if a label can take more than a few dozen values,
  it is a cardinality bomb.

```yaml
# prometheus.yml: drop a noisy label at scrape time.
metric_relabel_configs:
  - source_labels: [__name__]
    regex: '.*'
    action: keep
    # Only keep the series you actually query.
  - source_labels: [pod_template_hash]
    regex: '.+'
    action: labeldrop
```

For unbounded dimensions, use exemplars (attach a trace ID to a histogram bucket) rather
than a label. That gives you the drill-down without the series explosion.

## Dashboards that answer questions

A dashboard is a tool, not a decoration. Each panel should answer a question someone
actually asks during an incident or a review.

Layout that works under pressure:

1. **Top row: the four golden signals for the service.** Rate, errors, duration, saturation.
2. **Second row: the same signals for each immediate dependency** (database, cache,
   queue).
3. **Third row: the build and deploy context** — version, rollout, and config-change
   annotations on the charts.
4. **Below that: diagnostics.** Per-route breakdown, per-instance imbalance, error types.

Rules:

- Annotate deploys on the time series. Most incidents start at a deploy boundary.
- Put the current value *and* the comparison (against an hour ago, or against the SLO)
  on the panel, not just a graph.
- Use consistent units and consistent colors across every dashboard. Red should mean the
  same thing everywhere.
- Every panel links to the runbook for what to do when it is bad.

## Alerting

Alert on **symptoms the user feels**: latency, errors, availability against an SLO.
Alert on **causes** to a dashboard, or to a low-urgency ticket. The reason is that
cause-based alerts fire for conditions that never mattered, which is how alert fatigue
starts.

```yaml
# prometheus.yml: recording rules that compute an SLO indicator.
groups:
  - name: api-slo
    interval: 30s
    rules:
      - record: service:http_requests:rate5m
        expr: sum(rate(http_requests_total[5m]))
      - record: service:http_requests:error_ratio5m
        expr: |
          sum(rate(http_requests_total{status=~"5.."}[5m]))
            / clamp_min(sum(rate(http_requests_total[5m])), 1)
      - record: service:http_request_duration_seconds:p99_5m
        expr: |
          histogram_quantile(0.99,
            sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
```

Multi-window multi-burn-rate alerting, from the SRE workbook: alert on both a short and a
long window so that a spike pages quickly but a slow burn does not page at all, and a
brief recovery does not resolve the page immediately.

```promql
# Short window: 5 minutes. Long window: 1 hour.
(
  sum(rate(http_requests_total_bad_total[5m]))
    / sum(rate(http_requests_total_total[5m])) > (14.4 * 0.001)
)
and
(
  sum(rate(http_requests_total_bad_total[1h]))
    / sum(rate(http_requests_total_total[1h])) > (14.4 * 0.001)
)
```

The burn-rate multipliers and the page versus ticket split come from the SRE Workbook
chapter on alerting on SLOs. Read that chapter for the table rather than copying a single
threshold; the right values depend on your error budget policy.

Alert hygiene checklist:

| Check | Why |
| --- | --- |
| Every alert has a runbook link | At 3am the runbook is the value of the alert |
| Every alert is actionable | If the response is "nothing", it is not an alert |
| No alert fires on a known-benign condition | Tune it out or fix the underlying noise |
| Every alert has been fired in a game day | Untested alerts are broken alerts |
| Page on symptoms; ticket on causes | Keeps the pager meaningful |
| Every alert has an owner | Ownership is per service, not per person, but a named team is required |
| Duplicate alerts on one symptom are merged | Three pages for one outage is worse than one |
| Error-budget-based alerts have a defined burn policy | A page on burn alone causes fatigue; a page on burn plus budget exhaustion does not |

## Alerting on saturation

Saturation alerts are useful as non-paging signals. Queue depth, pool wait, and event-loop
delay rise before user-visible latency does. Route them to a dashboard or a low-urgency
notification, and use them to justify capacity work.

```promql
# Connection pool exhaustion: waiters present.
max_over_time(db_pool_waiters[5m]) > 0

# Event loop delay above a threshold, in seconds.
max_over_time(node_eventloop_lag_seconds[5m]) > 1

# Queue depth growing steadily.
deriv(queue_depth[30m]) > 0 and queue_depth > 100
```

## Checklist

- [ ] RED metrics on every request-driven service; USE metrics on every resource.
- [ ] Counters for totals, gauges for instantaneous values, histograms for latency.
- [ ] Route patterns used as labels, never raw URLs.
- [ ] Metric names follow `namespace_subsystem_name_unit`, counters end in `_total`.
- [ ] Buckets chosen with the SLO target at a bucket boundary.
- [ ] Cardinailty audited; no unbounded label values; series count monitored.
- [ ] Exemplars configured so high-cardinality drill-down is possible without labels.
- [ ] Dashboards structured golden-signals-first, with deploy annotations.
- [ ] Recording rules precompute expensive queries.
- [ ] Alerts are symptom-based, have owners, link to runbooks, and have been tested.
- [ ] Alert thresholds use multi-window burn rates where an SLO exists.
- [ ] Saturation signals are monitored even though they do not page.
- [ ] `/metrics` is protected from public access.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Prometheus out of memory | Cardinality explosion, usually a user or URL label | Find it with `count by (__name__)` and drop the label |
| p99 alert always firing | Buckets too coarse for the SLO, or low-traffic noise | Add buckets near the target; aggregate over a longer window |
| Alert fires, nothing is wrong | Cause-based alerting on a non-issue | Move it to a dashboard; page only on symptoms |
| Nobody looks at the dashboard | Panels do not answer any question | Redesign around the questions an on-call engineer asks |
| Mean latency is fine, users complain | Mean hides the tail | Use percentiles, and check the p99 and p99.9 |
| Metric counts differ between instances | Non-deterministic labeling or duplicate collectors | Ensure exactly one collector per process |
| Scrape failing after a language upgrade | Exposition format or HELP/TYPE mismatch | Compare raw `/metrics` output with `promtool check metrics` |
| Error rate looks fine during an outage | All requests fail, so ratio is 100 percent but a ratio-only panel might be scrolled past | Show rate and ratio together, with absolute counts |
| Cardinality grows over days | A label with a slowly growing value set (build ids, versions) | Drop the label or bound its lifetime |

## Anti-patterns

- **Labeling with user IDs, request IDs, or full paths.** The single most common cause of a
  self-inflicted metrics outage.
- **Alerting on every metric that crosses a threshold.** Volume is not coverage.
- **Averaging percentiles across instances.** Averaging p99s is not the fleet p99. Sum the
  buckets first, then compute the quantile.
- **Using `summary` for latency** and then averaging across instances, which does not work.
- **CPU as the primary scaling signal.** CPU is the last thing to saturate.
- **A dashboard that is a wall of gauges with no hierarchy.** Nobody reads it under
  pressure.
- **Alerting on causes.** Every dependency restart becomes a page.
- **Building custom percentile math from counters.** Use `histogram_quantile`.
- **No SLO, so no alert threshold with meaning.** Thresholds become round numbers picked
  by feel.
- **Exposing `/metrics` publicly.** It leaks internal structure and label values.
- **Instrumenting everything at `DEBUG` level permanently.** Volume grows, and no one reads
  it.

## References

- Prometheus documentation, getting started and querying
  - <https://prometheus.io/docs/introduction/overview/>
  - <https://prometheus.io/docs/prometheus/latest/querying/basics/>
  - <https://prometheus.io/docs/practices/instrumentation/>
  - <https://prometheus.io/docs/practices/naming/>
  - <https://prometheus.io/docs/practices/histograms/>
  - <https://prometheus.io/docs/practices/histograms/#sampling-vs-full-histogram>
- Prometheus metric and label naming conventions
  - <https://prometheus.io/docs/concepts/data_model/>
- Prometheus client libraries
  - <https://prometheus.io/docs/instrumenting/clientlibs/>
- `promtool` for validating exposition and rules
  - <https://prometheus.io/docs/prometheus/latest/command-line/promtool/>
- Google SRE book, "Monitoring Distributed Systems" (four golden signals)
  - <https://sre.google/sre-book/monitoring-distributed-systems/>
- Google SRE Workbook, alerting on SLOs and multi-burn-rate alerts
  - <https://sre.google/workbook/alerting-on-slos/>
- RED method
  - <https://grafana.com/blog/2018/08/02/the-red-method-how-to-instrument-your-services/>
- USE method
  - <https://www.brendangregg.com/usemethod.html>
- Grafana dashboards and alerting
  - <https://grafana.com/docs/grafana/latest/dashboards/>
  - <https://grafana.com/docs/grafana/latest/alerting/>
- OpenTelemetry metrics
  - <https://opentelemetry.io/docs/concepts/signals/metrics/>
- Exemplars
  - <https://prometheus.io/docs/concepts/exemplars/>

## Deeper detail

- `references/prometheus-instrumentation.md` — client library setup per language,
  recording rules, relabelling, service discovery, and exporter configuration.
- `references/dashboards-and-alerts.md` — dashboard layouts, SLO definitions, burn-rate
  alert policy, and tuning a noisy alert.
