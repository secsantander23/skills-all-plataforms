# k6 Reference

k6 scripts are ES modules or CommonJS run by a Go runtime. The API surface below is stable; verify new options with `k6 inspect` and https://k6.io/docs/ before relying on them.

## Script structure

```javascript
// load/basic.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import exec from 'k6/execution';   // per-VU and per-iteration context

export const options = { /* ... */ };

export function setup() {
  // Runs once per test run. Return a value to pass it to each VU.
  return { token: 'seed-token' };
}

export function teardown(data) {
  // Runs once after all VUs finish.
}

export default function (data) {
  const res = http.get('http://127.0.0.1:8080/health', { tags: { name: 'GET /health' } });
  check(res, { 'health is 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
  // Returns a map of filename -> report body. Overrides the default summary.
  return { 'summary.json': JSON.stringify(data, null, 2) };
}
```

Context helpers in `k6/execution`:

```javascript
import exec from 'k6/execution';

const VU_ID = exec.vu.idInTest;      // unique VU number, useful for unique data
const ITER = exec.scenario.iterationInTest;
const TEST_RUN = exec.testRunId;     // pass to a tag so metrics can be filtered
```

## HTTP API

```javascript
const response = http.get(url, {
  headers: { 'Authorization': `Bearer ${data.token}` },
  tags: { flow: 'checkout' },
  timeout: '30s',
});

const response = http.post(
  url,
  JSON.stringify(body),
  { headers: { 'Content-Type': 'application/json' } },
);

const response = http.request('POST', url, body, params);
```

Useful members: `res.status`, `res.headers`, `res.body`, `res.timings`, `res.json(path)`, `res.error`.

```javascript
res.timings.duration;   // total
res.timings.waiting;    // time spent waiting for the first byte (TTFB)
res.timings.blocked;    // time in a connection pool queue
res.timings.connect;    // TCP + TLS handshake
res.timings.sending;
res.timings.receiving;
```

`res.json('order.items[0].sku')` uses a JSONPath-like selector.

## Metrics

Built-in metrics you will assert on:

| Metric | Meaning |
| --- | --- |
| `http_req_duration` | End-to-end request duration |
| `http_req_waiting` | TTFB |
| `http_req_blocked` | Time blocked in the connection pool |
| `http_req_connect` | Connection and TLS time |
| `http_req_failed` | Whether the request failed (also considers expected statuses) |
| `http_reqs` | Request count |
| `iteration_duration` | One loop of the default function |
| `checks` | Pass rate of `check()` |
| `dropped_iterations` | Iterations k6 could not start (usually insufficient `maxVUs`) |
| `vus` and `vus_max` | Virtual users in use and available |

Custom metrics:

```javascript
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';

const orders = new Counter('orders_completed');
const failures = new Rate('order_failures');
const queueDepth = new Gauge('queue_depth');
const checkoutDuration = new Trend('checkout_duration', true);  // true = keep as time
```

Prefer `Trend` over `Gauge` for anything time-based, so the percentile machinery applies.

## Thresholds

```javascript
export const options = {
  thresholds: {
    http_req_duration: ['p(95)<400', 'p(99)<800', 'max<2000'],
    'http_req_duration{flow:checkout}': ['p(95)<800'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99', 'rate>=0.99'],
    orders_completed: ['count>1000'],
  },
};
```

Threshold grammar: `<metric> <aggregation> <operator> <value>`, where aggregation is one of `avg`, `min`, `med`, `p(95)`, `p(99)`, `max`, `count`, `rate`. Sub-metrics with tags use the `{tag:value}` syntax.

A threshold failure sets a non-zero exit code, which is what makes a threshold usable as a CI gate. You can also add `abortOnFail` to stop the run early.

Thresholds apply to the whole run by default. Per-scenario thresholds are supported via the scenario name as the metric selector; check the current docs for the exact syntax before relying on it.

## Running

```bash
k6 run load/basic.js
k6 run --out json=results.json load/basic.js
k6 run -e BASE_URL=https://staging.example.com -e TAG=canary load/basic.js
k6 run --summary-trend load/basic.js
k6 run --paused load/basic.js        # start paused so you can open the browser UI
k6 inspect load/basic.js             # validate and print the resolved config
k6 archive load/basic.js             # package a single-file script for the cloud
```

Flags change; run `k6 run --help` to confirm what exists in your installed version.

## Output and dashboards

- `--out json=file.json` emits one JSON line per metric sample, which you can post-process.
- `--out influxdb=...` and Prometheus-compatible outputs exist; the Prometheus writers have historically been marked experimental, so confirm status in the docs.
- The web dashboard and the hosted cloud service are separate products; check current availability and pricing before depending on them.

## Data and cleanup

Every VU should create its own data and clean up after itself, otherwise parallel runs collide.

```javascript
export default function (data) {
  const email = `perf-${exec.vu.idInTest}-${exec.scenario.iterationInTest}@example.test`;
  const created = http.post(`${data.baseUrl}/users`, JSON.stringify({ email }), {
    headers: { 'Content-Type': 'application/json' },
  });

  const res = http.get(`${data.baseUrl}/orders`);
  check(res, { 'orders listed': (r) => r.status === 200 });

  http.del(`${data.baseUrl}/users/${created.json('id')}`);
}
```

Seed bulk data once in `setup()` with a direct database client, not through the API, so the warm-up does not distort the measurement.

## Load shape reference

```javascript
export const options = {
  scenarios: {
    warmup: {
      executor: 'ramping-arrival-rate',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 20,
      maxVUs: 100,
      stages: [{ duration: '30s', target: 50 }],
    },
    steady: {
      executor: 'constant-arrival-rate',
      rate: 50,
      timeUnit: '1s',
      duration: '5m',
      preAllocatedVUs: 100,
      maxVUs: 400,
      startTime: '30s',     // run after warmup completes
    },
  },
  thresholds: {
    'http_req_duration{scenario:steady}': ['p(95)<400'],
  },
};
```

The `scenario` tag exists, so per-scenario thresholds and views are possible; verify the exact selector syntax for your version.

## Gotchas

- k6 will not exceed `maxVUs`. If your requested rate needs more, it drops iterations and reports them in `dropped_iterations`. Watch that counter.
- The generator is not a browser. It does not execute JavaScript in a page, and it does not model think time unless you add `sleep`.
- `check()` failures do not fail the run unless a threshold says so.
- Default tag cardinality is limited by config (`systemTags`); add tags only for aggregates you need, because each distinct tag multiplies metric series.
- Do not log from the default function at high rates. Console output becomes the bottleneck.