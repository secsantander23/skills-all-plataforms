# Logger Setups by Ecosystem

## Node.js with pino

```js
// logger.js
import pino from 'pino';

const isProduction = process.env.NODE_ENV === 'production';

export const logger = pino({
  level: process.env.LOG_LEVEL ?? (isProduction ? 'info' : 'debug'),
  base: {
    service: process.env.SERVICE_NAME ?? 'api',
    env: process.env.NODE_ENV ?? 'development',
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  redact: {
    paths: [
      'req.headers.authorization',
      'req.headers.cookie',
      'password',
      '*.password',
      'token',
      '*.access_token',
      'apiKey',
    ],
    censor: '[REDACTED]',
  },
  formatters: {
    level: (label) => ({ level: label }),
  },
});
```

```js
// pino-http: one line per request, with a correlation id and safe headers.
import pinoHttp from 'pino-http';

app.use(pinoHttp({
  logger,
  genReqId: (req, res) => {
    const existing = req.headers['x-request-id'];
    const id = (Array.isArray(existing) ? existing[0] : existing) || crypto.randomUUID();
    res.setHeader('x-request-id', id);
    return id;
  },
  customLogLevel: (req, res, err) => {
    if (err || res.statusCode >= 500) return 'error';
    if (res.statusCode >= 400) return 'warn';
    return 'info';
  },
  customProps: (req) => ({ trace_id: req.headers['traceparent']?.split('-')[1] }),
  serializers: {
    req: (req) => ({ id: req.id, method: req.method, url: req.url, route: req.route?.path }),
    res: (res) => ({ statusCode: res.statusCode }),
  },
  autoLogging: { ignore: (req) => req.url === '/healthz' },
}));
```

The first positional argument to `logger.info` is the message; fields go in an object:

```js
logger.info({ orderId: 'o-123', duration_ms: 812.4 }, 'checkout completed');
logger.error({ err, route: '/orders/:id' }, 'upstream request failed');
```

Child loggers bind fields for a subtree:

```js
const child = logger.child({ requestId: req.id, tenantId: tenant.id });
child.info('starting import');
```

For pretty output during development, use a transport rather than a different library, so
the production format stays structured:

```js
// In development only:
import pino from 'pino';
const logger = pino({
  transport: { target: 'pino-pretty', options: { colorize: true } },
});
```

## Python with structlog

```python
import logging
import structlog
import sys

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,     # merge contextvars.bind_contextvars(...)
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(sys.stdout),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger("checkout")

# Bind request-scoped context so every subsequent line carries it.
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(
    request_id=request_id,
    trace_id=trace_id,
)

log.info("checkout.completed", order_id="o-123", duration_ms=812.4)
log.warning("upstream slow", upstream="payments", duration_ms=4102.0)
log.error("upstream failed", upstream="payments", exc_info=True)
```

To route structlog through the standard library `logging` instead of printing directly, use
`structlog.stdlib.LoggerFactory()` and configure handlers with
`logging.config.dictConfig`. That is useful when other libraries already emit through
`logging` and you want them in the same stream.

Redaction in Python: wrap the renderer or the formatter so it strips keys before
serialization, and do it in one place so no call site can forget.

```python
SENSITIVE = {"password", "token", "secret", "authorization", "cookie", "api_key"}

def scrub(event_dict):
    for key in list(event_dict):
        if key.lower() in SENSITIVE:
            event_dict[key] = "[REDACTED]"
    return event_dict
```

Insert `scrub` into `processors` immediately before the renderer.

## Go with log/slog

```go
package logging

import (
    "log/slog"
    "os"
)

func New() *slog.Logger {
    return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
        Level: slog.LevelInfo,
        ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
            if a.Key == slog.TimeKey {
                return slog.String("ts", a.Value.Time().Format("2006-01-02T15:04:05.000Z07:00"))
            }
            if a.Key == slog.MessageKey {
                return slog.String("msg", a.Value.String())
            }
            if a.Key == slog.LevelKey {
                return slog.String("level", a.Value.String())
            }
            return a
        },
    }))
}
```

Context-based fields:

```go
logger := logging.New()

func Handler(logger *slog.Logger) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        ctx := r.Context()
        ctx = context.WithValue(ctx, ctxKeyLogger, logger.With(
            "request_id", requestID(r),
            "trace_id", traceID(r),
            "route", routePattern(r),
        ))
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

func logFrom(ctx context.Context) *slog.Logger {
    if l, ok := ctx.Value(ctxKeyLogger).(*slog.Logger); ok {
        return l
    }
    return slog.Default()
}
```

Prefer `*slog.Logger` passed explicitly or through context over the package-level `slog.Info`
helpers, because the helpers cannot carry request-scoped fields.

## JavaScript in the browser

Browsers log to the developer console, which is not queryable in production. If you need
client-side observability, ship errors and breadcrumbs to an endpoint explicitly rather than
relying on console output.

```js
// Report errors and breadcrumbs to your own endpoint.
const breadcrumbs = [];
function crumb(message, data) {
  breadcrumbs.push({ t: Date.now(), message, data });
  if (breadcrumbs.length > 50) breadcrumbs.shift();
}

window.addEventListener('error', (event) => {
  navigator.sendBeacon('/client-telemetry', JSON.stringify({
    type: 'error',
    message: event.message,
    stack: event.error?.stack,
    url: event.filename,
    line: event.lineno,
    breadcrumbs,
  }));
});
```

Redact before sending: client telemetry is the most common place user-visible form data
escapes into a logging system. Whitelist the fields you need rather than forwarding
anything.

## OpenTelemetry logging

The OpenTelemetry logs SDK provides a logs data model with a `TraceId`, `SpanId`, and
`TraceFlags` on each record, which lets a backend join logs to traces without you parsing
`traceparent` yourself.

```python
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter

provider = LoggerProvider()
provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))
set_logger_provider(provider)
```

```js
import { logs, SeverityNumber } from '@opentelemetry/api-logs';

const logger = logs.getLogger('api', '1.0.0');
logger.emit({
  severityNumber: SeverityNumber.ERROR,
  body: 'upstream request failed',
  attributes: { err_type: err.name, route: '/orders/:id' },
});
```

Note the package name `@opentelemetry/api-logs`, which is distinct from the metrics and
tracing `@opentelemetry/api` packages. If your setup cannot find it, check the package name
and the version compatibility matrix in the OpenTelemetry documentation.

## Level control

| Mechanism | Effect |
| --- | --- |
| Environment variable read at startup | Standard; keep the library default and override |
| Runtime level change (a signal or an admin endpoint) | Useful in production; guard it, since it is a DoS lever |
| Per-namespace levels | Debug one subsystem without enabling debug everywhere |
| Compilation stripping | Compile-time removal in some languages; brittle across upgrades |

Guard any endpoint that changes log level at runtime. An unauthenticated endpoint that sets
`LOG_LEVEL=debug` is a self-inflicted denial of service and, at `trace`, a disk-fill
primitive.

## Child loggers and correlation

```js
// Node: bind per-request fields once, then call plain methods everywhere.
app.use((req, res, next) => {
  req.log = logger.child({
    request_id: req.id,
    trace_id: req.headers['traceparent']?.split('-')[1],
  });
  next();
});

app.get('/orders/:id', (req, res) => {
  req.log.info({ orderId: req.params.id }, 'fetching order');
});
```

```python
# Python: bind contextvars at the request boundary.
@app.middleware("http")
async def bind_context(request, call_next):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request.headers.get("x-request-id"),
        trace_id=(request.headers.get("traceparent") or "").split("-")[1] if request.headers.get("traceparent") else None,
    )
    return await call_next(request)
```

The important property: correlation fields are attached once, at the boundary, so no
interior function has to remember to pass them.

## Testing your logging

```js
// Assert that a secret never reaches stdout.
import { test, expect } from 'vitest';

test('redacts secrets', async () => {
  const written = [];
  const spy = vi.spyOn(process.stdout, 'write')
    .mockImplementation((chunk) => { written.push(String(chunk)); return true; });

  logger.info({ user: 'ada', password: 'hunter2' }, 'login attempt');

  const line = JSON.parse(written.at(-1));
  expect(line.password).toBe('[REDACTED]');
  expect(written.join('')).not.toContain('hunter2');
});
```

A redaction test in CI is the only reliable control. Code review catches intent; the test
catches regressions.
