# Pipelines, Sampling, and Retention

## Anatomy of a pipeline

```
application stdout
  -> runtime collector (node/host level)
    -> parse JSON
      -> redact secrets a second time
        -> filter (level, health checks)
          -> transform (rename, drop, enrich)
            -> sample
              -> batch and buffer to disk
                -> remote store (Loki, Elasticsearch, ClickHouse, cloud)
```

Every stage can drop, duplicate, or reorder. Design so that a stage failing means "buffer
locally", never "lose data" and never "block the application".

## Fluent Bit pipeline

```yaml
# fluent-bit.conf
[SERVICE]
    Flush        5
    Log_Level    info
    Daemon       off
    Parsers_File /fluent-bit/parsers.conf

[INPUT]
    Name         tail
    Tag          app.*
    Path         /var/log/containers/*.log
    multiline.parser docker, cri
    Refresh_Interval 5
    DB           /var/lib/fluent-bit/tail.db
    Mem_Buf_Limit 128MB
    Skip_Long_Lines On

[FILTER]
    Name         parser
    Match        app.*
    Key_Name     log
    Parser       json
    Reserve_Data On

# Drop noisy, uninteresting lines before they cost storage.
[FILTER]
    Name         grep
    Match        app.*
    Exclude      log /healthz|/readyz|/metrics

# Keep everything serious; keep a fraction of the rest.
[FILTER]
    Name         modify
    Match        app.*
    Add          service api

[FILTER]
    Name         sampling
    Match        app.*
    Sample       5
    Option       logs

[OUTPUT]
    Name         loki
    Match        app.*
    Host         loki.internal
    Port         3100
    Labels       job=api
```

The tail input uses a state database (`DB`) so a restart does not reread gigabytes. Keep
that database on persistent storage.

```bash
fluent-bit --dry-run -c fluent-bit.conf     # validate before reloading
docker run --rm -v "$PWD":/fluent-bit fluent/fluent-bit --dry-run -c /fluent-bit/fluent-bit.conf
```

Flag names for validation differ across builds; check `fluent-bit --help` for yours.

## Vector pipeline

```toml
# vector.toml
[sources.app]
type = "file"
include = ["/var/log/containers/*.log"]
read_from = "beginning"

[sources.app.codec]
type = "json"

[transforms.redact]
type = "remap"
inputs = ["app"]
source = '''
  .password = "[REDACTED]"
  .token = "[REDACTED]"
  .authorization = "[REDACTED]"
  del(.raw_body)
'''

[transforms.drop_health]
type = "remap"
inputs = ["redact"]
source = '''
  if .route == "/healthz" || .route == "/metrics" {
    abort
  }
'''

[sinks.loki]
type = "loki"
inputs = ["drop_health"]
endpoint = "http://loki.internal:3100"
encoding.codec = "json"
```

```bash
vector validate --no-environment ./vector.toml
```

## Parsing and indexing

Decide what you query before you decide what you store.

| Frequently queried | Field | Index it |
| --- | --- | --- |
| One request across services | `request_id`, `trace_id` | Yes |
| All errors in a window | `level`, `err_type` | Yes |
| All traffic for an endpoint | `route` | Yes |
| Specific user impact | `user_id` (hashed) | Yes, with care |
| Everything else | | No |

Indexed fields cost storage and ingest time proportional to their count. A field like
`request_id` has as many distinct values as you have requests; indexing it is useful for
point lookups and expensive for range scans. Some stores handle high-cardinality indexed
fields better than others — check your store's guidance before indexing a per-request
identifier.

Store long payloads as blobs and keep a truncated preview inline, so a search returns a
match without pulling the entire body.

## Sampling

Three places to sample, with different tradeoffs:

| Location | Method | Pros | Cons |
| --- | --- | --- | --- |
| Application | Level filter and per-event sampling | Cheapest, never ships what you dropped | Cannot see that errors correlate, because you already discarded context |
| Collector (head-based) | Decide per stream at ingest | Simple, predictable volume | Cannot base the decision on the whole trace |
| Collector (tail-based) | Buffer, group by trace, decide after the trace completes | Keeps complete interesting traces; can raise the error rate while lowering total volume | Latency in delivering logs; needs buffering memory |

```yaml
# OpenTelemetry Collector tail sampling: keep all error traces, 5% of the rest.
receivers:
  otlp:
    protocols:
      grpc: {}
      http: {}

processors:
  tail_sampling:
    decision_wait: 10s
    num_traces: 100000
    expected_new_traces_per_sec: 100
    policies:
      - name: errors
        type: status_code
        status_code: {status_codes: [ERROR]}
      - name: slow
        type: latency
        latency: {threshold_ms: 1000}
      - name: baseline
        type: probabilistic
        probabilistic: {sampling_percentage: 5}

exporters:
  otlp:
    endpoint: collector-backend.internal:4317

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [tail_sampling]
      exporters: [otlp]
```

Field names and required options for collector processors change across releases. Validate
with `otelcol-contrib validate --config config.yaml` or the equivalent for your build, and
check <https://opentelemetry.io/docs/collector/>.

Never sample errors away. A volume-saving scheme that occasionally drops an error line is
worse than useless during an incident.

## Retention

Model it as tiers, driven by the questions each tier must answer:

| Tier | Contents | Retention | Typical cost |
| --- | --- | --- | --- |
| Hot | All levels, full fidelity | Days | Highest |
| Warm | `INFO` and above, sampled | Weeks | Medium |
| Cold | `ERROR` and `WARN` only, plus audit records | Months to years | Low per GB |
| Archive | Compliance-required records only | Per policy | Object storage |

Rules:

- Error retention must exceed the longest alert evaluation window plus investigation time,
  or you will page on an error you cannot read.
- Audit records are a separate stream with separate access controls and immutability. Do not
  mix them into application logs.
- Delete by policy, not by hand. Manual deletion during an incident removes the evidence.

## Cost control

Log cost is dominated by ingest bytes and indexed field count. Levers, in order of leverage:

1. Turn off `DEBUG` and `TRACE` in production.
2. Drop health-check and metrics-endpoint lines at the collector.
3. Truncate long strings at the application.
4. Sample `INFO` lines, keeping all errors.
5. Reduce indexed fields.
6. Shorten retention on hot tiers.
7. Compress the storage layer (most stores offer it).

Measure ingest rate directly:

```bash
# Loki
curl -s http://loki.internal:3100/metrics | grep -E 'loki_distributor_bytes_received_total|cortex_request_duration_seconds_count'

# Elasticsearch
curl -s http://es.internal:9200/_cat/indices?v&h=index,docs.count,store.size&s=store.size:desc

# Vector or Fluent Bit: check the collector's own metrics endpoint for
# bytes_in / bytes_out / dropped_records.
```

A metric named like `dropped_records` on the collector is the one to alert on: it means your
sampling or filtering is silently discarding lines.

## Failure modes

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Logs stop arriving, no errors | Collector buffer full or network partition | Check `retries_failed` / `dropped_records`; the app should be unaffected |
| Disk filling on the host | No rotation, or a runaway loop logging | Fix the logger; add rate limiting in the collector |
| Duplicate lines | Multi-line parsing splitting one JSON line, or two agents tailing the same file | Fix the parser; ensure one agent per file |
| Timestamps all identical | Container runtime stamping rather than the app | Log timestamps in the app |
| Sensitive data found in the store | Redaction only in the app, bypassed by a library that logs raw | Add a collector-side redaction stage as a second line of defense |
| Interleaved, truncated JSON | Concurrent writes without serialization | Use a logger that writes whole lines atomically |

## Verifying before relying on this

Configuration keys for these collectors and processors change between releases. Validate
each config with its own validation command before applying, and consult the official
documentation: <https://docs.fluentbit.io/manual/>, <https://vector.dev/docs/>, and
<https://opentelemetry.io/docs/collector/>.
