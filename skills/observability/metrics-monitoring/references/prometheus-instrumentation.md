# Prometheus Instrumentation

## Client libraries

| Language | Library | Import |
| --- | --- | --- |
| Go | `prometheus/client_golang` | `github.com/prometheus/client_golang/prometheus` |
| Python | `prometheus_client` | `prometheus_client` |
| Node.js | `prom-client` | `prom-client` |
| Java / JVM | `prometheus/client_java` | `io.prometheus.client` |
| Ruby | `prometheus-client` | `prometheus/client` |
| .NET | `prometheus-net` | `Prometheus` |

Official list: <https://prometheus.io/docs/instrumenting/clientlibs/>

### Go

```go
package metrics

import (
    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promauto"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    "net/http"
)

var (
    Requests = promauto.NewCounterVec(prometheus.CounterOpts{
        Name: "http_requests_total",
        Help: "Total HTTP requests",
    }, []string{"method", "route", "status"})

    Duration = promauto.NewHistogramVec(prometheus.HistogramOpts{
        Name:    "http_request_duration_seconds",
        Help:    "HTTP request duration in seconds",
        Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10},
    }, []string{"method", "route"})

    InFlight = promauto.NewGauge(prometheus.GaugeOpts{
        Name: "http_requests_in_flight",
        Help: "HTTP requests currently being served",
    })
)

func Handler() http.Handler {
    return promhttp.Handler()
}
```

`promauto` registers metrics at init, so a duplicate registration panics at startup. That
is a feature: it catches double registration immediately rather than at scrape time.

### Python

```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server, CollectorRegistry

REQUESTS = Counter("http_requests_total", "Total HTTP requests",
                   ["method", "route", "status"])
DURATION = Histogram("http_request_duration_seconds", "HTTP request duration in seconds",
                     ["method", "route"],
                     buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
IN_FLIGHT = Gauge("http_requests_in_flight", "In-flight HTTP requests", ["method"])

# Process and Python runtime metrics, registered automatically by prometheus_client.
start_http_server(8000)   # serves /metrics on all interfaces by default

# In a multi-process deployment, use the multiprocess mode:
#   PROMETHEUS_MULTIPROC_DIR=/var/run/prometheus_multiproc
# and a MultiProcessCollector in a separate exporter process.
```

Prometheus client libraries for Python and Go install an HTTP handler by default. Bind it
to a management interface, not to the public port.

### Node.js

```javascript
import client from 'prom-client';
import express from 'express';

export const registry = new client.Registry();
client.collectDefaultMetrics({ register: registry });

export const httpRequestDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request duration in seconds',
  labelNames: ['method', 'route', 'status_code'],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
  registers: [registry],
});

const metricsApp = express();
metricsApp.get('/metrics', async (_req, res) => {
  res.set('Content-Type', registry.contentType);
  res.end(await registry.metrics());
});
```

Use `labelNames: ['status_code']` rather than `status` if other metrics in the same
service use `status_code`; inconsistent label names break every query you write against
both.

### Pushgateway

Some jobs are short-lived and cannot be scraped. Push to a Pushgateway, then delete the
metric when the job ends.

```bash
curl -X POST http://pushgateway.internal:9091/metrics/job/backup \
     --data-binary @<(printf 'backup_last_success_timestamp_seconds %s\n' "$(date +%s)")
```

Delete the group when the job is done, otherwise the value is stale forever:

```bash
curl -X DELETE http://pushgateway.internal:9091/metrics/job/backup
```

## Histogram buckets

Buckets are the number one decision that determines whether your latency metric is useful.

```python
# Buckets that put boundaries at a 250ms SLO target and just above it.
DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.5, 0.75, 1, 2.5, 5, 10),
)
```

Guidance:

- Put a bucket boundary at, or just above, your SLO target. A p99 computed from buckets has
  resolution only as fine as the bucket containing it.
- Exponential buckets from 5ms to 10s are a reasonable default for request latency.
- Fewer buckets cost less series memory. Each extra bucket is one more time series per
  label combination.
- For payload sizes, use bytes buckets spanning the range you actually serve.
- Changing buckets mid-life resets the histogram's continuity. Track that as a deliberate
  schema change.

## Recording rules

Recording rules precompute expensive queries so dashboards and alerts stay cheap.

```yaml
# rules/api.yml
groups:
  - name: api-red
    interval: 30s
    rules:
      - record: service:http_requests:rate5m
        expr: sum by (route) (rate(http_requests_total[5m]))

      - record: service:http_requests:error_ratio5m
        expr: |
          sum by (route) (rate(http_requests_total{status=~"5.."}[5m]))
            / clamp_min(sum by (route) (rate(http_requests_total[5m])), 1)

      - record: service:http_request_duration_seconds:p99_5m
        expr: |
          histogram_quantile(0.99,
            sum by (route, le) (rate(http_request_duration_seconds_bucket[5m])))

      - record: service:http_request_duration_seconds:p50_1h
        expr: |
          histogram_quantile(0.50,
            sum by (route, le) (rate(http_request_duration_seconds_bucket[1h])))
```

Load and validate before applying:

```bash
promtool check rules rules/api.yml
curl -X POST http://prometheus.internal:9090/api/v1/rules \
     -H 'Content-Type: application/json' \
     -d '{"name":"api-red"}'
```

Verify an expression interactively before writing it into a rule, in the Prometheus UI's
"Expression" tab or with the HTTP API:

```bash
curl -s 'http://prometheus.internal:9090/api/v1/query' \
     --data-urlencode 'query=histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))'
```

## Relabelling and metric filtering

Relabel rules run before ingestion. Use them to drop unwanted series at the source rather
than carrying them forever.

```yaml
scrape_configs:
  - job_name: 'api'
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ['api.internal:8080']
        labels:
          env: prod
          team: platform

    # Drop series before they are ingested.
    metric_relabel_configs:
      - source_labels: [__name__]
        regex: 'go_memstats_.*'
        action: drop
      - source_labels: [session_id]
        regex: '.+'
        action: labeldrop
      # Rewrite a label value.
      - source_labels: [instance]
        target_label: pod
        regex: '([^:]+):.*'
        replacement: '$1'

    # Keep only what the service is responsible for.
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: 'api'
        action: keep
```

## Kubernetes service discovery

```yaml
scrape_configs:
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      # Use the pod IP as the instance label.
      - source_labels: [__address__]
        regex: '([^:]+)(?::\d+)?'
        replacement: '$1'
        target_label: __address__
      # Map container port 8080 metric paths onto pods.
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: true
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape_port]
        action: keep
        regex: \d+
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
      # Carry pod labels onto every series from this target.
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
```

These are the pod-annotation conventions used by the common Helm charts. If you deploy
Prometheus Operator, the annotations become `PodMonitor` resources instead, and the scrape
job and labels are declared there.

## Push-based metrics with OpenMetrics

The OpenMetrics text exposition adds exemplars, which link a metric point to a trace.

```text
# TYPE http_request_duration_seconds histogram
# HELP http_request_duration_seconds HTTP request duration in seconds
http_request_duration_seconds_bucket{le="0.1"} 1024 # {trace_id="0af7651916cd43dd8448eb211c80319c"} 0.093
http_request_duration_seconds_count 1030
http_request_duration_seconds_sum 84.2
```

In Prometheus, exemplars arrive when the client serves the OpenMetrics content type.
`prometheus-client` Python and the Go client both support exemplar emission. Check your
client library documentation for the current API.

## Pull versus push

| | Pull (Prometheus scrapes) | Push (OTLP / remote write) |
| --- | --- | --- |
| Ownership | Prometheus decides what to scrape | The app decides what to send |
| Short-lived jobs | Hard | Natural |
| Cardinality control | At scrape time | At the receiver |
| Overhead if the target is down | None | You keep retrying |
| Network trust | One-way from the scraper | The app needs credentials |

Pull is the simpler default for services. Push is the right choice for short-lived batch
jobs and for environments where the collector cannot reach the workload.

## Validation

```bash
# Validate a scrape config and rule files.
promtool check config prometheus.yml
promtool check rules rules/*.yml

# Validate exposition text.
curl -s http://api.internal:8080/metrics > /tmp/metrics.txt
promtool check metrics < /tmp/metrics.txt

# Unit-test rules against fixture data.
promtool test rules tests/api.yml

# Inspect what Prometheus actually sees for one metric.
curl -s 'http://prometheus.internal:9090/api/v1/series?match[]=http_requests_total'
```

## Verifying before relying on this

Client library APIs and Prometheus configuration keys change between releases. Confirm
against the documentation for your library version and
<https://prometheus.io/docs/>.
