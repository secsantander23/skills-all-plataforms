---
name: distributed-tracing
description: Distributed tracing with OpenTelemetry covering span creation, W3C traceparent propagation, auto and manual instrumentation of HTTP, RPC, database, and queue calls, head and tail based sampling, trace-log correlation, and overhead tradeoffs. Use when the user asks how to trace requests across services, set up OpenTelemetry, propagate trace context, correlate logs with traces, or find latency inside a distributed call path.
metadata:
  opencode/autoinvoke: true
---

# Distributed Tracing

A trace is a tree of spans: one span per operation, linked by parent-child relationships,
sharing a single trace ID across every service the request touches. It answers the question
metrics and logs structurally cannot: *where inside this request did the time go, and which
downstream call was responsible*. The cost is propagation work in every language and
protocol you use, so instrument the boundaries that matter first.

## When to use this

- A request crosses several services and you cannot tell which hop is slow.
- You need to prove a latency problem is in a dependency rather than in your code.
- You want to correlate logs across services without grepping timestamps.
- You are debugging a latency tail that only affects a fraction of requests.
- You need to see the shape of a call graph to decide where to optimize or to split.

## Quick start

```bash
# Node.js: install the SDK, auto-instrumentation, and the OTLP exporter.
npm install @opentelemetry/api \
            @opentelemetry/sdk-node \
            @opentelemetry/auto-instrumentations-node \
            @opentelemetry/exporter-trace-otlp-http
```

```js
// instrumentation.js - must be loaded BEFORE your application code.
import { NodeSDK } from '@opentelemetry/sdk-node';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { Resource } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from '@opentelemetry/semantic-conventions';

const sdk = new NodeSDK({
  resource: new Resource({
    [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME ?? 'api',
    [ATTR_SERVICE_VERSION]: process.env.SERVICE_VERSION ?? 'dev',
  }),
  traceExporter: new OTLPTraceExporter(),
  instrumentations: [getNodeAutoInstrumentations()],
});

sdk.start();

process.on('SIGTERM', async () => {
  await sdk.shutdown();
  process.exit(0);
});
```

```bash
node --require ./instrumentation.js app.js
# or, if you prefer not to change the start command:
NODE_OPTIONS="--require ./instrumentation.js" node app.js
```

```bash
# Point it at a collector and verify spans arrive.
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.internal:4318
export OTEL_SERVICE_NAME=api
export OTEL_SERVICE_VERSION=1.4.2
export OTEL_TRACES_SAMPLER=parentbased_traceidratio
export OTEL_TRACES_SAMPLER_ARG=0.1
```

```js
// Manual span for an operation the auto-instrumentation does not cover.
import { trace, SpanStatusCode } from '@opentelemetry/api';

const tracer = trace.getTracer('checkout', '1.0.0');

export async function priceOrder(order) {
  return tracer.startActiveSpan('priceOrder', async (span) => {
    try {
      span.setAttributes({
        'order.id': order.id,
        'order.item_count': order.items.length,
      });
      const price = await computePrice(order);
      span.setAttribute('order.total', price.total);
      span.setStatus({ code: SpanStatusCode.OK });
      return price;
    } catch (err) {
      span.recordException(err);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
      throw err;
    } finally {
      span.end();     // always end, on every path
    }
  });
}
```

If spans are not appearing, check that `instrumentation.js` is loaded before the framework
that you want instrumented — auto-instrumentation works by loading before the library
registers itself, so ordering is the most common failure.

## Spans and trace context

| Concept | Meaning |
| --- | --- |
| Span | One timed operation: a name, a start and end, attributes, events, links, and a parent |
| Trace | The tree of spans sharing a trace ID |
| Span context | The four values propagated: trace ID, span ID, trace flags, trace state |
| Parent | The span that was active when this span started |
| Attributes | Key-value metadata on a span |
| Events | Timestamped points inside a span (useful for marking retries or state changes) |
| Links | References to other spans, for causality that is not parent-child (fan-out, batch) |

Span context is serialized as the W3C `traceparent` header (RFC 9115):

```
traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
              |  |                                |                |
              |  trace-id (32 hex, 16 bytes)      span-id (16 hex) flags
              version (2 hex)
```

`flags` is a two-hex-digit bit field; the sampled flag is the low bit (`01` means sampled).
`tracestate` is a comma-separated vendor list such as
`congo=t61rcWkgMzE,rojo=00f067aa0ba902b7`, propagated alongside and preserved across hops.

The trace ID and span ID must be injected into whatever carries the request downstream: an
HTTP header, a message header on a queue, or gRPC metadata. Propagating only over HTTP is
the usual gap that makes a trace die at the message broker.

```js
// Inject and extract explicitly when you are crossing a boundary by hand.
import { propagation, context, trace } from '@opentelemetry/api';

const carrier = { 'traceparent': '...' };        // your headers object
const extracted = propagation.extract(context.active(), carrier);
context.with(extracted, () => {
  const span = tracer.startSpan('consume-message');
  // ... work
  span.end();
});

const carrierOut = {};
propagation.inject(context.active(), carrierOut);
// send carrierOut with the outbound message
```

## Instrumenting boundaries

Priority order: instrument the slowest and least trustworthy hops first.

| Boundary | What auto-instrumentation gives you | What to add manually |
| --- | --- | --- |
| Inbound HTTP | Server span, method, status, route, duration | Business attributes: tenant, account tier, operation name |
| Outbound HTTP | Client span, method, URL, status | The operation's meaning, plus the downstream service name |
| gRPC / RPC | Client and server spans with method names | Payload-level business context |
| Database | Client span with the statement template | A connection-pool wait span, which is often the real cost |
| Message queue | Producer span; consumer span on the other side | A link from consumer to producer, since it is not a child |
| Cache | Nothing, by default | Client span with hit/miss and key (carefully — see cardinality) |
| Queues and workers | Producer span, consumer span | Batch size, queue wait time, attempt number |

Auto-instrumentation packages, one per ecosystem, all published under the
`@opentelemetry/auto-instrumentations-node` umbrella package:

```js
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';

const instrumentations = [getNodeAutoInstrumentations({
  '@opentelemetry/instrumentation-fs': { enabled: false },
  '@opentelemetry/instrumentation-http': {
    ignoreIncomingRequestHook: (req) => req.url === '/healthz',
    headersToSpanAttributes: {
      'x-request-id': 'app.request_id',
    },
  },
})];
```

```python
# Python: distro-style auto-instrumentation, no code changes.
#   pip install opentelemetry-distro opentelemetry-exporter-otlp opentelemetry-instrumentation-fastapi
#   opentelemetry-instrument python app.py
```

```bash
# Install instrumentation into an existing Python environment.
opentelemetry-bootstrap -a install pip install -r requirements.txt
```

Auto-instrumentation gives breadth without code changes. Manual spans give you meaning.
You need both: the auto-instrumented spans tell you the shape, and your manual spans tell
you what the operations mean.

## Attribute naming and semantic conventions

OpenTelemetry publishes semantic conventions so that spans from different languages and
libraries line up. HTTP attributes were renamed in recent revisions: the older
`http.method` and `http.status_code` became `http.request.method` and
`http.response.status_code` in the current HTTP conventions. Older libraries and backends
still show the older names.

```js
span.setAttributes({
  'http.request.method': 'POST',          // current HTTP convention
  'url.path': '/orders',                   // current HTTP convention
  'http.response.status_code': 502,
  'server.address': 'payments.internal',
  'error.type': 'UpstreamTimeoutError',
  'order.id': 'o-123',
});
```

Rules:

- Prefer the published conventions over your own names for anything cross-service.
- Never put high-cardinality values into a span attribute if you export spans to a metrics
  backend. Trace backends handle attributes well; treat the span-to-metrics path carefully.
- Do not put PII or secrets in attributes. Spans are exported to third-party backends and
  often retained for a week.
- Do not put an entire request payload in an attribute. Attribute values should be small
  identifiers.
- Set the span status to `ERROR` when the operation actually failed. Per the specification,
  an HTTP response in the 4xx range on the client side does not by itself make the span an
  error; decide your convention and be consistent.

## Sampling

Sampling exists because tracing every request is more storage and CPU than most systems
need. Sampling decisions must be consistent across services or you get broken traces.

### Head-based

The decision is made at the start of the trace, and propagated to every service.

| Sampler | Behavior |
| --- | --- |
| `always_on` | Trace everything |
| `always_off` | Trace nothing |
| `traceidratio` | Trace a fraction, decided by the trace ID so it is stable |
| `parentbased` | Delegate to the parent's decision, falling back to a root sampler |

`parentbased_traceidratio` with an argument is the usual production default: honour the
caller's decision, and for a new trace sample a fraction of them.

```bash
export OTEL_TRACES_SAMPLER=parentbased_traceidratio
export OTEL_TRACES_SAMPLER_ARG=0.05
```

Head-based sampling is cheap and stateless, and it means you cannot decide to keep a trace
after seeing that it was slow or failed. That is the tradeoff.

### Tail-based

The collector buffers complete traces and then decides, using policies over the finished
spans. You can keep 100% of error traces and 1% of successful ones, and the ratio of
useful traces to stored spans improves dramatically.

```yaml
# OpenTelemetry Collector tail sampling processor.
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
        probabilistic: {sampling_percentage: 2}
```

Tradeoffs: the `decision_wait` adds latency before a trace is exported, buffering memory
is bounded and can overflow under load (the collector drops the oldest traces), and the
policy names and required fields vary by collector version. Validate your config with the
collector's own validation command and check
<https://opentelemetry.io/docs/collector/>.

A hybrid is common: a small head-based sample at the SDK so you never pay to build a trace
you will discard, plus tail-based policies in the collector to enrich what survives.

## Tracing asynchronous work

A consumer is not a child of a producer, because the producer finishes long before the
consumer runs. Model it as a **link**, not a parent-child relationship.

```js
// Producer: put the context into the message headers.
const headers = {};
propagation.inject(context.active(), headers);
await queue.publish({ id: event.id, headers });

// Consumer: extract the context and link it to the consumer span.
const parent = propagation.extract(context.root(), event.headers);
const span = tracer.startSpan('process-order', { links: [{ context: trace.getSpanContext(parent) }] });
```

Record queue wait time as a span attribute or event. "The message sat in the queue for four
minutes" is exactly the kind of fact that a trace can reveal and a metric on handler
duration cannot.

## Trace-log correlation

Pull the active trace context into log fields so a log line and a span are joinable.

```python
from opentelemetry import trace
import structlog

def log_context(logger, method_name, event_dict):
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(processors=[log_context, structlog.processors.JSONRenderer()])
log.info("order persisted", order_id=order.id)
```

```js
// Node.js: read the active span inside your logger.
import { trace } from '@opentelemetry/api';

function traceFields() {
  const span = trace.getActiveSpan();
  const ctx = span?.spanContext();
  return ctx ? { trace_id: ctx.traceId, span_id: ctx.spanId } : {};
}

log.info({ ...traceFields(), orderId: 'o-123' }, 'order persisted');
```

```promql
# In Grafana, link from a log line to the trace: template variable
# ${trace_id} -> TraceID($trace_id), and from a trace to the logs: ${trace_id} -> logs query.
```

Keeping trace ID and span ID on every log line turns logs and traces into one searchable
system rather than two.

## Using traces to find latency

Procedure:

1. Find the slow requests. Either a trace search for duration above a threshold, or a
   metrics query that identifies the affected route, then find traces for it.
2. Sort by duration and open the critical path. Most trace UIs have a "critical path" or
   longest-span view that flattens the tree to the actual sequential path.
3. Look at self time, not total time. A span that contains a 900ms child contributes its
   own overhead, not the 900ms.
4. Check for gaps. A span with children but a start time well after its parent's start
   means something ran between them that is not instrumented.
5. Check for missing spans. An uninstrumented boundary looks exactly like a fast one.
6. Look at the counts. Ten thousand sequential round trips in one trace is a design problem,
   not a latency problem.

Cross-check with the waterfall view: spans that are siblings running in parallel show as
concurrent bars, and the wall-clock cost is the longest bar, not the sum.

## Overhead

| Source | Cost driver | Mitigation |
| --- | --- | --- |
| Span creation | CPU per span; a trace with 1000 spans means 1000 span objects | Sample, and avoid creating spans per loop iteration |
| Context propagation | Header parse and serialize per hop | Cheap relative to a network call, but do not propagate on internal-only calls |
| Export | Network and serialization to the collector | Batch spans (the SDK batches by default) |
| Storage | Bytes per span, times sampled traces | Tail-based sampling, shorter retention |
| Attributes | Larger spans | Keep attributes small and bounded |

Practical guidance:

- Use the batch span processor in production. `SimpleSpanProcessor` exports every span as
  it ends, which is a network call per span.
- Do not create a span per loop iteration or per row. Create spans per operation.
- Sampled decisions propagate through the trace, so a fraction sampled at the edge costs
  roughly that fraction everywhere.
- Export through a local collector (or an agent sidecar) rather than directly to a remote
  backend, so a backend outage cannot block your service.

```js
// Node.js: the SDK uses BatchSpanProcessor by default. Override only if needed.
import { BatchSpanProcessor, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';

spanProcessors: [
  new BatchSpanProcessor(new OTLPTraceExporter({ url: 'http://127.0.0.1:4318/v1/traces' })),
]
```

## Configuration reference

Environment variables understood by the OpenTelemetry SDKs:

| Variable | Purpose |
| --- | --- |
| `OTEL_SERVICE_NAME` | Logical service name; the primary identity field |
| `OTEL_SERVICE_VERSION` | Version, so you can compare across deploys |
| `OTEL_RESOURCE_ATTRIBUTES` | Comma-separated `key=value` resource attributes |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector base URL for OTLP |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Collector URL for the traces signal specifically |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` or `http/protobuf` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Authentication headers for the collector |
| `OTEL_TRACES_SAMPLER` | Sampler name, e.g. `parentbased_traceidratio` |
| `OTEL_TRACES_SAMPLER_ARG` | Sampler argument, e.g. the ratio `0.1` |
| `OTEL_PROPAGATORS` | Comma-separated propagator list, e.g. `tracecontext,baggage` |
| `OTEL_SERVICE_NAMESPACE` | Optional namespace grouping services |
| `OTEL_LOG_LEVEL` | SDK internal log verbosity |

The exact set recognized varies by language SDK and version. Consult the configuration
documentation for the SDK you use rather than assuming a variable is supported everywhere.

## Backends

| Backend | Notes |
| --- | --- |
| Jaeger | Widely deployed, native OTLP support |
| Grafana Tempo | Designed for the Grafana stack, pairs with Loki for log correlation |
| OpenTelemetry Collector | Not a backend, but the standard routing and processing layer |
| Honeycomb, Datadog, New Relic, Elastic, Splunk, AWS X-Ray | Commercial or cloud options |

Do not couple your instrumentation to a specific backend. Export OTLP to the collector and
change backends by changing the collector configuration.

## Checklist

- [ ] Instrumentation module loads before the application and the framework.
- [ ] Service name, version, and environment set on every resource.
- [ ] Inbound and outbound HTTP, database, and queue boundaries covered.
- [ ] Context propagated over HTTP, gRPC metadata, and message headers.
- [ ] Queue consumers link to producers rather than claiming them as parents.
- [ ] `span.end()` in a `finally` block on every path.
- [ ] Errors call `recordException` and set the status.
- [ ] Attribute names follow the current semantic conventions.
- [ ] No PII, secrets, or payloads in span attributes.
- [ ] Sampling is `parentbased` and the ratio is documented.
- [ ] Batch span processor used in production.
- [ ] Trace ID and span ID present on every log line.
- [ ] Span attribute budget set; no per-row or per-loop spans.
- [ ] Exporter points at a local collector, so a backend outage cannot block requests.

## Common mistakes

| Symptom | Root cause | Fix |
| --- | --- | --- |
| No spans at all | Instrumentation loaded after the framework | Load the instrumentation module first |
| Spans from one service only | Context not propagated downstream | Inject `traceparent` on every outbound call |
| Broken traces at the queue | Consumer not linked to producer | Inject on publish, extract on consume, use links |
| Traces are mostly empty | Sampler set to `always_off`, or the ratio is tiny | Check `OTEL_TRACES_SAMPLER` and the collector's own logs |
| Trace says the request was fast, users say slow | Only a fraction of requests traced | Tail-sample errors and slow traces explicitly |
| Huge storage cost | Every request traced at full fidelity | Sample head-based, refine with tail-based policies |
| Span names are all `GET` | Missing route templates | Use the route pattern, not the URL |
| Backend outage caused request errors | Exporting synchronously to a remote backend | Export to a local collector and use the batch processor |
| `404` spans marked as errors | Treating any non-2xx as an error | Follow the specification's status conventions |
| Two traces for one request | Context overwritten instead of injected | Set `OTEL_PROPAGATORS=tracecontext,baggage` and use inject/extract |

## Anti-patterns

- **Tracing everything at 100% in production** without a storage and cost plan.
- **Using `SimpleSpanProcessor` in production.** One export call per span.
- **Creating a span per iteration** of a loop or per row of a batch. Span objects are not
  free and the trace becomes unreadable.
- **Baggage for data that should be a log field or an attribute.** Baggage propagates to
  every hop, including third parties, and is visible in headers on the wire.
- **Renaming convention attributes** per service. Cross-service queries stop working.
- **Sampling independently in each service.** Breaks traces into disconnected fragments.
- **Tracing only the edge.** The interesting spans are in the middle.
- **Putting payloads in span attributes.** Storage cost plus a privacy problem.
- **Tracing with the `traceparent` header stripped at a gateway.** Common, silent, and
  fatal to the whole trace.
- **Using tracing to answer "how often".** That is a metric; a trace backend is the wrong
  place to count.

## References

- OpenTelemetry documentation, getting started and concepts
  - <https://opentelemetry.io/docs/languages/>
  - <https://opentelemetry.io/docs/concepts/signals/traces/>
  - <https://opentelemetry.io/docs/concepts/signals/traces/#span-context
  - <https://opentelemetry.io/docs/concepts/signals/traces/#sampling>
- OpenTelemetry SDK configuration
  - <https://opentelemetry.io/docs/languages/js/getting-started/nodejs/>
  - <https://opentelemetry.io/docs/languages/python/>
- OpenTelemetry semantic conventions
  - <https://opentelemetry.io/docs/specs/semconv/>
- OpenTelemetry Collector
  - <https://opentelemetry.io/docs/collector/>
- RFC 9115, W3C Trace Context
  - <https://www.rfc-editor.org/rfc/rfc9115.html>
- OpenTelemetry protocol (OTLP) specification
  - <https://opentelemetry.io/docs/specs/otlp/>
- Jaeger
  - <https://www.jaegertracing.io/docs/latest/>
- Grafana Tempo
  - <https://grafana.com/docs/tempo/latest/>
- Google SRE book, "Distributed Tracing in Practice"
  - <https://sre.google/sre-book/distributed-tracing/>

## Deeper detail

- `references/otel-setup.md` — per-language SDK setup, collector deployment, exporters,
  and semantic-convention migration notes.
- `references/sampling-and-cost.md` — head and tail sampling configuration, collector
  capacity, retention, and cost modeling.
