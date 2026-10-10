---
name: structured-logging
description: Structured JSON logging with levels, correlation IDs, W3C trace context propagation, contextual fields, redaction of PII and secrets, log shipping to aggregators, retention, sampling, and reading logs during debugging. Use when the user asks about logging in JSON, choosing log levels, adding request or correlation IDs, rotating logs, shipping logs to Loki or Elasticsearch, or debugging from log output.
metadata:
  opencode/autoinvoke: true
---

# Structured Logging

A log line is a record of an event. Text logs force a human to parse and a machine to
guess; structured logs make fields queryable, so the question "which requests to user 42
failed with a timeout in the last hour" becomes a filter instead of a regular expression.
The design goal is not "pretty output", it is that every line answers *what happened, to
what, and in what context* without a human parsing prose.

## When to use this

- You are adding logging to a service and choosing a format and library.
- Debugging a distributed flow and need to follow one request across services.
- Logs are unsearchable, expensive, or full of secrets and personal data.
- You need to reduce log volume or cost without losing the lines that matter.
- A log-based alert failed because lines are inconsistent.
- You need to satisfy an audit or data-retention requirement.

## Quick start

Emit one JSON object per line, with a level, a message, and stable field names.

```js
// logger.js - minimal structured logger with request correlation.
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';

const storage = new AsyncLocalStorage();

const REDACT = new Set(['password', 'token', 'authorization', 'cookie', 'secret', 'api_key']);

function redact(value, depth = 0) {
  if (value === null || typeof value !== 'object' || depth > 4) return value;
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    out[k] = REDACT.has(k.toLowerCase()) ? '[REDACTED]' : redact(v, depth + 1);
  }
  return out;
}

function emit(level, msg, fields = {}) {
  process.stdout.write(JSON.stringify({
    ts: new Date().toISOString(),
    level,
    service: process.env.SERVICE_NAME,
    env: process.env.NODE_ENV,
    msg,
    ...storage.getStore(),
    ...redact(fields),
  }) + '\n');
}

export const log = {
  debug: (msg, f) => process.env.LOG_LEVEL === 'debug' && emit('debug', msg, f),
  info:  (msg, f) => emit('info', msg, f),
  warn:  (msg, f) => emit('warn', msg, f),
  error: (msg, f) => emit('error', msg, f),
  child: (fields) => storage.getStore(),   // see middleware below for scoping
};

export function withContext(store, fn) {
  return storage.run({ ...(storage.getStore() ?? {}), ...store }, fn);
}

export const newRequestId = () => randomUUID();
```

```js
// middleware.js - establish correlation for the whole request.
import { newRequestId, withContext } from './logger.js';

export function requestContext(req, res, next) {
  const incoming = req.get('traceparent');
  const traceId = incoming?.split('-')[1] ?? newRequestId();
  const requestId = req.get('x-request-id') ?? newRequestId();

  res.setHeader('x-request-id', requestId);
  withContext({ trace_id: traceId, request_id: requestId }, next);
}
```

```bash
# Grep by field instead of parsing prose.
jq -c 'select(.level == "error" and .route == "/checkout")' app.log
jq -r 'select(.trace_id == "0af7651916cd43dd8448eb211c80319c") | [.ts, .service, .msg] | @tsv' *.log
jq -s 'group_by(.route) | map({route: .[0].route, n: length}) | sort_by(-.n)' app.log
```

## Log levels

| Level | Use for | Examples |
| --- | --- | --- |
| `ERROR` | The request or operation failed and someone may need to act | Upstream returned 500, database unreachable, unhandled exception |
| `WARN` | Something unexpected but handled, or a resource near a limit | Retry succeeded on the second attempt, cache miss rate spike, deprecated API called |
| `INFO` | Normal, meaningful state transitions | Request completed, job started, user created, config reloaded |
| `DEBUG` | Diagnostic detail for development | Query parameters, branch decisions, cache keys |
| `TRACE` | Extremely fine-grained, usually sampled | Loop iterations, per-packet detail |

Two rules make levels useful:

1. **Levels describe impact, not the programmer's mood.** A failure the user sees is an
   `ERROR` even if it was caught. A caught-and-handled condition is a `WARN`.
2. **The production default should be `INFO`.** `DEBUG` in production usually costs money
   and slows the service without answering anything you were not already measuring with
   metrics.

Do not log an error at `ERROR` in a retry loop that eventually succeeds at `INFO`. One
failed attempt that caused no user-visible effect is a `WARN` at most, and even that should
be sampled.

## Correlation IDs

Correlating a request across services is the highest-value thing structured logging buys.

- **Request ID** — unique per request, generated at the edge, propagated downstream. Use it
  as your primary search key.
- **Trace ID** — shared across every span of a distributed trace. Comes from W3C
  `traceparent` when the caller is instrumented, and is the join key with traces.
- **Span ID** — identifies the specific operation within the trace.
- **User or tenant ID** — for questions about a specific user's experience. Treat as PII
  and consider hashing rather than logging raw.

```
W3C Trace Context (RFC 9115), header format:
traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
              ^^ version      ^ trace-id (32 hex)      ^ parent-id (16 hex) ^ flags
tracestate:   congo=t61rcWkgMzE,rojo=00f067aa0ba902b7
```

```js
// Propagate the trace context to downstream calls.
const res = await fetch(url, {
  headers: {
    'traceparent': `00-${traceId}-${spanId}-01`,
    'x-request-id': requestId,
  },
});
```

If you propagate `traceparent` by hand, you are maintaining distributed tracing manually.
Adopt OpenTelemetry instead (see the `distributed-tracing` skill) and extract the trace ID
from the active span for logging. If you are not ready for that, at least propagate
`x-request-id`, but be consistent about header names across every service or the join
fails silently.

## Contextual fields

Fields should be stable and low-cardinality. Good field names:

| Field | Purpose |
| --- | --- |
| `ts` | ISO 8601 timestamp with timezone |
| `level` | Severity |
| `service`, `version`, `env` | Where it came from |
| `msg` or `event` | Short, stable, machine-greppable event name |
| `trace_id`, `request_id` | Correlation |
| `route`, `method`, `status` | HTTP context (use the route pattern, not the URL with IDs) |
| `duration_ms` | Latency |
| `err_type`, `err_code`, `err_msg`, `err_stack` | Error shape |
| `user_id`, `tenant_id` | Subject of the action (hash if PII-sensitive) |
| `attempt`, `outcome` | Retry and result context |

Rules:

- **Never log a value the schema cannot hold.** Use a documented schema or a schema-free
  design; do not invent five names for the same field.
- **Use route patterns, not raw URLs.** `/users/12345/orders` as a label creates unbounded
  cardinality in your log index. Log `/users/:id/orders`.
- **Errors get structure.** `err_type`, `err_code`, and a stack go in separate fields so you
  can group by type and still have the detail.
- **Numbers stay numbers.** Do not format durations into strings; you lose the ability to
  compute percentiles in the log pipeline.

```js
log.error('upstream request failed', {
  route: '/orders/:id',
  method: 'GET',
  status: 502,
  duration_ms: 1204.5,
  upstream: 'payments.internal',
  attempt: 2,
  err_type: err.name,
  err_code: err.code,
  err_msg: err.message,
  err_stack: err.stack,
});
```

## What not to log

Never log:

- Passwords, API keys, bearer tokens, session cookies, private keys, connection strings with
  credentials, `Authorization` headers.
- Full card numbers, CVV, or full bank account numbers. Mask all but the last four digits.
- Unredacted personal data: full names, addresses, dates of birth, government identifiers,
  precise location, medical data.
- Raw request or response bodies from endpoints that carry user data.
- Full payment or health data of any kind.
- Anything that would let someone impersonate a user.

Controls that actually work:

1. **Allowlist fields at the logger, not denylist at the call site.** A denylist fails
   the first time someone logs an entire request object.
2. **Redact by key name** at the serialization boundary (as in the quick start).
3. **Truncate** long strings, with a marker, so a large body cannot silently fill the log.
4. **Review the log schema** as part of code review, the same way you review a database
   migration.
5. **Separate audit logging** where the requirement is explicit, with its own retention,
   its own access controls, and its own schema.

```js
// Truncate rather than allow unbounded strings into the pipeline.
const MAX = 2048;
function safeString(s) {
  if (typeof s !== 'string') return s;
  return s.length <= MAX ? s : `${s.slice(0, MAX)}...[truncated ${s.length - MAX} chars]`;
}
```

Treat log access as production data access: log stores contain emails, tokens that escaped
redaction, and behavioral data. Restrict access, audit reads, and encrypt at rest.

## Shipping and aggregation

Write JSON to **stdout** and let the platform collect it. Files, rotation, and remote
transports inside the application duplicate what the platform already does and add a
failure mode.

```yaml
# Kubernetes: send container stdout to a collector.
apiVersion: v1
kind: Pod
metadata:
  name: api
spec:
  containers:
    - name: api
      image: registry.internal/api:1.4.2
      env:
        - name: LOG_LEVEL
          value: info
        - name: SERVICE_NAME
          value: api
      resources:
        limits:
          memory: 512Mi
```

| Collector | Common sinks |
| --- | --- |
| Fluent Bit / Fluentd | Loki, Elasticsearch, OpenSearch, cloud log services |
| Vector | Many, with transformation and sampling built in |
| Filebeat | Elasticsearch, Logstash |
| Cloud native | CloudWatch, Google Cloud Logging, Azure Monitor, Datadog |

Practical rules:

- **Ship asynchronously and never block the request path on the shipper.** A full disk or a
  slow collector must not become user latency.
- **Ship stdout, not files, in containers.** A sidecar or node-level agent handles it.
- **Parse at the collector, index what you query.** Do not index every field; index the
  correlation IDs, level, service, route, and error type.
- **Retain at different tiers.** Full logs hot for a short window, searchable summaries
  longer, and an archive if compliance demands it.

```bash
# systemd-style rate limiting if you ever must ship files.
# journald.conf:
#   SystemMaxUse=2G
#   RateLimitIntervalSec=30s
#   RateLimitBurst=20000
```

## Retention and sampling

Log volume is a function of traffic times lines per request times bytes per line. Control it
on three axes:

| Axis | Mechanism | Keep |
| --- | --- | --- |
| Which lines | Level filtering in production | `INFO` and above by default |
| How many | Tail-based sampling in the collector | All `ERROR`/`WARN`, a fraction of `INFO`, none of `DEBUG` |
| How long | Retention tiers in the store | Days for `DEBUG`, weeks for `INFO`, longer for `ERROR` |

```yaml
# Fluent Bit sampling: keep all warnings and errors, keep 5% of info, drop debug.
[FILTER]
    Name    sampling
    Match   app.*
    Sample  5
    Option  logs
    # Combined with a level-based record filter that drops debug before this point.
```

Sampling must be decided by a rule, not by chance, and it must never drop errors. Sample
by trace ID rather than by line when you use tracing, so that a sampled trace is complete:
either all its lines are kept or none are, and you can still follow a request end to end.

Correlate `ERROR` lines with metrics: if an error rate metric fires, you need the log lines
that produced it. That means error-line retention must outlive the alert window.

## Reading logs to debug

Order of attack:

1. **Start from a correlation ID**, not from time. One request, every line.
2. **Filter to one service and one level** to reduce noise.
3. **Look for the first anomaly**: a new error type, a latency jump, a missing expected
   event, a retry that did not appear.
4. **Widen to the time window** around the anomaly once you know what you are looking for.
5. **Compare against a known-good request** from the same endpoint.

```bash
# All lines for one request across services.
jq -c 'select(.request_id == "9f1c2d3e-4a5b-6c7d-8e9f-0a1b2c3d4e5f")' /var/log/app/*.jsonl

# Distinct error types in the last slice of a file.
jq -r 'select(.level == "error") | .err_type' app.log | sort | uniq -c | sort -rn

# Slowest routes by p99 approximation from a log sample.
jq -s 'map(select(.route != null and .duration_ms != null))
       | group_by(.route)
       | map({route: .[0].route,
              n: length,
              p50: (map(.duration_ms) | sort | .[length/2|floor]),
              max: (map(.duration_ms) | max)})' app.log

# Find gaps: lines more than 5s apart suggest dropped or buffered output.
jq -r '.ts' app.log | awk '{ if (prev) { cmd = "date -d \"" prev "\" +%s"; cmd | getline p; close(cmd);
       cmd = "date -d \"" $0 "\" +%s"; cmd | getline c; close(cmd);
       if (c - p > 5) print prev " -> " $0 " (" c-p "s gap)" } prev = $0 }'
```

Logs answer "what happened in this request". They cannot answer "how often" — that is
metrics — or "where in the call tree" — that is tracing. If you find yourself grepping
across a whole hour to count something, you need a metric instead.

## Logging libraries

| Ecosystem | Libraries | Notes |
| --- | --- | --- |
| Node.js | `pino`, `bunyan`, `winston`, built-in `console` | `pino` is fast and structured by default; `pino-http` adds request logging |
| Python | `structlog`, standard `logging` with a JSON formatter | `logging.config.dictConfig` for configuration |
| Go | `log/slog` (standard library), `zerolog`, `zap` | `slog` ships JSON and text handlers in the standard library |
| Java | SLF4J plus Logback, or Log4j2 | JSON via `logstash-logback-encoder` or the built-in JSON layout |
| Rust | `tracing` plus `tracing-subscriber` | Structured fields and spans are the core abstraction |

```python
# Python: structlog to stdout as JSON.
import logging, structlog, sys

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(sys.stdout),
)

log = structlog.get_logger()
log.info("checkout.completed", order_id="o-123", duration_ms=812.4)
```

```go
// Go: log/slog with a JSON handler writing to stdout.
logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
}))
logger.Info("checkout completed",
    "order_id", "o-123",
    "duration_ms", 812.4,
)
```

Use one library per service. Mixing libraries produces inconsistent schemas and duplicated
configuration.

## Checklist

- [ ] One JSON object per line, one library, one schema.
- [ ] `ts`, `level`, `msg`, `service`, `env` present on every line.
- [ ] Logs go to stdout, not to files inside the container.
- [ ] Level chosen by impact, not by mood; production default is `INFO`.
- [ ] Request ID generated at the edge and propagated to every downstream call.
- [ ] Trace ID extracted from the active span and included, joining logs to traces.
- [ ] Field names are stable and documented.
- [ ] Routes logged as patterns, not raw URLs with identifiers.
- [ ] Redaction is allowlist-based at the logger, not denylist-based at call sites.
- [ ] Strings truncated so bodies cannot flood the pipeline.
- [ ] Error logs carry `err_type`, `err_code`, `err_msg`, and a stack in fields.
- [ ] Retention tiers by severity; errors retained longer than the alert window.
- [ ] Sampling keeps every error and completes whole traces.
- [ ] Log access restricted and audited, because logs contain sensitive data.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Cannot find a request's logs | Request ID not propagated, or generated per service | Generate once at the edge, propagate on every call |
| Log index huge and slow | Raw URLs or user IDs used as field values | Log route patterns; hash or omit high-cardinality IDs |
| Secrets found in logs | Object logged wholesale | Allowlist fields at the logger; add redaction tests |
| Too many log lines in production | `DEBUG` left enabled | Set level from the environment; default to `INFO` |
| Lines lost under load | Synchronous, blocking log writes | Use a non-blocking logger and let the platform ship |
| Two services, two field names for the same thing | No shared schema | Agree on a schema; add it to a contract test |
| Log-based alert never fired | Inconsistent `level` values | Normalize levels to a fixed set of strings |
| Logs show the request completed but the user saw an error | Only the happy path logged | Log failures with the same request ID |
| Container restarts and logs vanish | Writing to files in the container | Write to stdout |
| Cannot reconstruct what happened | No timestamps with timezone | Log ISO 8601 with an explicit offset |

## Anti-patterns

- **Logging as a substitute for metrics or traces.** Counting occurrences by grepping logs
  is slow, expensive, and unreliable.
- **`console.log` of an object.** Field names become unstable, secrets leak, and schema
  drifts within a day.
- **Logging the same event at multiple levels in one flow.** One event, one line, one
  level.
- **Error logs without a structured error.** "Something went wrong" is unactionable.
- **Logging every request body.** Volume, cost, and privacy risk with no debugging benefit.
- **Logging inside tight loops.** Volume explodes and the profiler will show the logger as
  the hotspot.
- **Ad-hoc redaction at call sites.** The first forgotten call site leaks a token.
- **Sampling errors for cost.** You need the line that caused the incident.
- **Storing logs with no retention policy.** Storage cost grows until someone deletes them
  during an incident.
- **Assuming stdout is enough for audit.** Audit requirements need immutable, access
  controlled, separately retained storage.

## References

- MDN, structured data logging concepts and `console` guidance
  - <https://developer.mozilla.org/en-US/docs/Web/API/console>
- RFC 5424, the syslog structured data format
  - <https://www.rfc-editor.org/rfc/rfc5424.html>
- RFC 9115, W3C Trace Context `traceparent` and `tracestate`
  - <https://www.rfc-editor.org/rfc/rfc9115.html>
- OpenTelemetry logs data model and SDK
  - <https://opentelemetry.io/docs/specs/otel/logs/>
- pino, Node.js logger
  - <https://getpino.io/>
- structlog, Python
  - <https://www.structlog.org/en/stable/>
- Go `log/slog`
  - <https://pkg.go.dev/log/slog>
- Fluent Bit
  - <https://docs.fluentbit.io/manual/>
- Vector
  - <https://vector.dev/docs/>
- OpenTelemetry Collector, processors including `filter` and `tail_sampling`
  - <https://opentelemetry.io/docs/collector/>
- OWASP Logging Cheat Sheet, including what not to log
  - <https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>
- GDPR guidance on logging personal data
  - <https://gdpr-info.eu/art-30-gdpr/>

## Deeper detail

- `references/logger-setups.md` — pino, structlog, slog, and OpenTelemetry logging setup,
  plus level control and child loggers.
- `references/pipelines-and-retention.md` — collectors, parsing, indexing strategy,
  sampling, retention tiers, and cost control.
