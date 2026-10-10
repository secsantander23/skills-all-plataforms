---
name: Load and Performance Testing
description: Covers load, stress, spike and soak testing with k6, JMeter and Locust, modeling realistic load with arrival rates and stages, reading latency percentiles instead of averages, finding saturation points, setting SLOs and performance budgets, profiling results, and capacity planning. Use when the user asks about performance or load testing, when a service is slow under load, when choosing between k6 and JMeter, when interpreting p95 or p99 latency, or when planning capacity.
metadata:
  opencode/autoinvoke: true
---

# Load and Performance Testing

Load testing answers a capacity question: *under a specified workload, on specified hardware, does this system meet its latency and error targets, and where does it stop meeting them?* The core mental model is a controlled experiment. Change one variable at a time, hold the workload realistic, and treat every number as conditional on the environment that produced it.

## When to use this

- Before a launch, a migration, or a traffic-heavy campaign, to find the breaking point in advance.
- When paged for slowness and you need to know whether it is load, a leak, or a dependency.
- When choosing between two implementations and you need evidence rather than intuition.
- When capacity planning: how much headroom before you need another instance.
- After a significant change to the data access path, caching, or serialization.

Not for:

- Micro-optimizations you can settle with a profiler and a benchmark.
- Anything you cannot describe as a workload. "Make it fast" is not a test.

## Test types

| Type | Question | Shape |
| --- | --- | --- |
| Load test | Does it meet its targets at expected production load? | Steady load at expected RPS for a sustained period |
| Stress test | Where does it break, and how does it fail? | Ramp up until latency or errors degrade beyond the target |
| Spike test | Does it survive a sudden burst? | Step change to a much higher load, then back down |
| Soak / endurance test | Does it degrade over time? | Moderate load for hours or longer; watch memory, connections, log growth |
| Breakpoint test | At exactly what load does it saturate? | Incremental ramp with measurement at each step |
| Capacity test | What is the maximum sustainable throughput? | Ramp to saturation, then hold at the highest sustainable point |

Soak tests find the failures that only appear after hours: connection pools that leak, caches that fill, memory that grows without bound, log files that consume the disk, background jobs that pile up. Unit and integration tests never see these.

## Choosing a tool

| Tool | Language | Strengths | Tradeoffs |
| --- | --- | --- | --- |
| k6 | JavaScript | Low overhead, code-as-config, strong threshold and scenario model, integrates with most APM tools | Runs one distributed load from one process unless you use a paid cloud service; browser support is still an experimental module |
| JMeter | GUI + XML, Java under the hood | Mature, huge plugin ecosystem, strong for protocol-level work like JMS and JDBC | Heavy runner, GUI-generated scripts are painful to review, distributed mode is fiddly |
| Locust | Python | Code-as-config, fast iteration for HTTP workloads | Smaller ecosystem for exotic protocols |
| Gatling | Scala/Kotlin | Code-based, good reports, JVM-friendly | Scala learning curve |
| Artillery | JavaScript or YAML | Very low setup cost, easy CI integration, Node-friendly | Less control over advanced load shapes |

Prefer a code-based tool (`k6`, `Locust`, `Artillery`) when the load model will be reviewed and version controlled. Prefer JMeter when you need a plugin that already exists for your protocol.

## Quick start with k6

```javascript
// load/checkout.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const checkoutDuration = new Trend('checkout_duration', true);
const checkoutErrors = new Counter('checkout_errors');

export const options = {
  scenarios: {
    // Open model: keep offering a fixed rate of requests even if the system slows down.
    steady_state: {
      executor: 'constant-arrival-rate',
      rate: 50,             // iterations per timeUnit
      timeUnit: '1s',
      duration: '5m',
      preAllocatedVUs: 100, // memory reserved up front
      maxVUs: 400,          // upper bound if the system needs more
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<400', 'p(99)<800'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
    checkout_duration: ['p(95)<600'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8080';

export default function () {
  const payload = JSON.stringify({
    sku: 'ABC-1',
    quantity: 1,
    customerId: `cus_${__VU}`,
  });

  const response = http.post(`${BASE_URL}/checkout`, payload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { flow: 'checkout' },
  });

  checkoutErrors.add(response.status !== 201);

  check(response, {
    'status is 201': (r) => r.status === 201,
    'has order id': (r) => !!(r.json('orderId')),
  });

  sleep(0.2);
}
```

```bash
k6 run load/checkout.js
k6 run --summary-trend load/checkout.js
k6 run -e BASE_URL=https://staging.example.com load/checkout.js
```

Validate the script without generating load:

```bash
k6 inspect load/checkout.js
```

### Load shapes in k6

Executors model the workload. The one you choose changes what your results mean.

| Executor | Model | Use when |
| --- | --- | --- |
| `constant-vus` | Closed: N virtual users loop as fast as they can | Cheap smoke checks; beware coordinated omission |
| `ramping-vus` | Closed: N users ramp through stages | Load tests and stress ramps |
| `constant-arrival-rate` | Open: fixed request rate regardless of response time | The default choice for realistic load |
| `ramping-arrival-rate` | Open: request rate ramps through stages | Stress tests and finding saturation |
| `per-vu-iterations` | Each VU runs a fixed number of iterations | Script correctness, not performance |
| `shared-iterations` | Total iterations split across VUs | Bounded workloads |

```javascript
export const options = {
  scenarios: {
    stress_ramp: {
      executor: 'ramping-arrival-rate',
      startRate: 20,
      timeUnit: '1s',
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { duration: '2m', target: 100 },
        { duration: '5m', target: 400 },   // hold here; this is your stress plateau
        { duration: '2m', target: 0 },
      ],
    },
  },
};
```

For `ramping-arrival-rate`, `startRate`, `timeUnit`, `preAllocatedVUs`, `maxVUs` and a `stages` array of `{ duration, target }` are required. k6 refuses to run if the configuration cannot satisfy the highest stage, which is a useful early warning that `maxVUs` is too low.

**Closed vs open model matters.** In a closed model, when the system slows down, each virtual user simply issues fewer requests, and the queueing delay is hidden from the measurement. This is *coordinated omission*. Use an arrival-rate executor to hold the offered load constant so queueing appears in the latency numbers.

## Locust quick start

```python
# locustfile.py
import random
import time

from locust import HttpUser, between, task


class CheckoutUser(HttpUser):
    wait_time = between(1, 3)  # think time between requests, in seconds

    @task(3)
    def browse(self) -> None:
        self.client.get("/shop")

    @task
    def checkout(self) -> None:
        sku = random.choice(["ABC-1", "ABC-2", "ABC-3"])
        with self.client.post(
            "/checkout",
            json={"sku": sku, "quantity": 1},
            name="/checkout",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure("unexpected status %s" % response.status_code)


class RampUp(HttpUser):
    wait_time = between(0.5, 1)
    tasks = [task(browse) for _ in range(5)]
```

```bash
locust -f locustfile.py --host http://127.0.0.1:8080        # web UI
locust -f locustfile.py --host http://127.0.0.1:8080 --headless -u 200 -r 20 -t 10m
locust -f locustfile.py --host http://127.0.0.1:8080 --headless -u 200 -r 20 -t 10m --html report.html
```

Locust options change across releases, including the distributed flags. Check `locust --help` and https://docs.locust.io/ before scripting CI.

## JMeter quick start

```bash
# Non-GUI mode is mandatory for real load; the GUI is for design only.
jmeter -n -t checkout.jmx -l results.jtl -e -o report/
jmeter -n -t checkout.jmx -l results.jtl -Jhost=staging.example.com -Jthreads=100 -Jduration=300
```

- `-n` non-GUI mode, `-t` test plan, `-l` raw result file, `-e` generate an HTML dashboard, `-o` output directory for it.
- `-J` sets a JMeter property, typically used to parameterize the plan.
- Distributed mode uses `-r` (remote host list) and `-s` (this host is not the controller). See the JMeter distributed testing guide before using it.

Recording a plan with the GUI "HTTP(S) Test Script Recorder" is convenient, but review the resulting JMX: recordings include cookies, dynamic values, and unchecked assertions that make the plan fragile.

## Latency percentiles, not averages

Averages hide the users who are having a bad time, and they are distorted by the very fast majority.

A distribution with a mean of 50 ms can look like:

- 99 percent of requests at 10 ms, 1 percent at 4550 ms. Mean 55 ms, p99 around 4400 ms.
- 90 percent at 40 ms, 10 percent at 100 ms. Mean 46 ms, p99 around 95 ms.

Both have similar averages. Only one is a user-visible incident.

Practical guidance:

- Track p50 as the typical case, p95 and p99 as the tail, and the error rate separately.
- Define your thresholds against a user journey, not per endpoint. A checkout that calls six endpoints with p99 of 900 ms each has a p99 of well over 900 ms end to end.
- Prefer histograms over averages when comparing runs. k6 uses HdrHistogram, which has bounded relative error and aggregates cleanly.
- Watch for a bimodal distribution. It usually means two code paths (a cache hit and a miss, two regions, two instance types), and it is where your tail latency lives.
- Never average percentiles across runs or across endpoints. Averaging destroys the information.

## Finding the saturation point

A stress test is a controlled ramp. At each step, record throughput, p50/p95/p99, error rate, and saturation signals. Saturation appears in a specific order:

1. Queueing grows: latency rises while throughput stays flat.
2. Latency rises roughly linearly with offered load.
3. Throughput stops increasing with more load.
4. Errors appear: timeouts, 5xx, connection refused, or your own rate limiting.

The saturation point is the highest throughput at which your latency and error targets still hold. Everything above it is overload.

Watch these secondary signals during the ramp, because they explain the primary one:

- CPU utilization per core (one saturated core caps throughput regardless of how many you add).
- Database connection pool utilization and lock waits.
- Thread pool or event loop queue depth.
- Garbage collection pause time.
- Cache hit ratio.
- Downstream dependency rate limits.

Compare the profile to how it behaves in production. A system that saturates at 400 requests per second in a lab may be fine in production if you have 10 instances and the test had 1.

## Environment parity

Performance numbers are only meaningful relative to their environment. Record it every time:

| Variable | Why it matters |
| --- | --- |
| Application code version | Otherwise the result is not reproducible |
| Instance count and size | Throughput scales with both |
| Data volume | Indexes and caches behave differently at 1k rows and 100M |
| Data distribution | Uniform ids give unrealistically good cache locality |
| Database version and configuration | Buffer pool, connection limits, plan choices |
| Network path | Loopback, same host, or across an availability zone |
| Load generator location | Measures the network too if it is remote |
| Environment flags | Debug builds, verbose logging, disabled caches |

Best practice: run the load generator close to the system, but not on the same machine, unless you are measuring a single-process function in isolation.

## SLOs and performance budgets

Tie tests to a service level objective so "slow" is a number.

```
SLI  : proportion of successful requests under 400 ms
SLO  : 99% over a rolling 30-day window
Error budget: 1% of requests may exceed 400 ms or fail
```

Then:

- Set a **performance budget** per request journey, expressed in latency and in resource cost, so a regression is caught in CI before release rather than in production.
- Alert on the SLO burn rate, not on CPU. CPU is a cause; the SLO is the effect.
- Re-run the same load test against every release candidate, and fail the build if a metric regresses beyond your budget.
- Record baseline results in version control next to the script so the diff is reviewable.

## Profiling results

When the aggregate numbers say "slow", find where the time goes.

- **Sampling profilers** show where wall time is spent without changing the program: `py-spy` for Python, Chrome DevTools Performance panel for browser and Node, `async-profiler` for the JVM, `perf` on Linux.
- **Flame graphs** show the stack hierarchy. Wide bars are hot functions; a wide bar with a very tall stack often means a deep call chain worth flattening.
- **Database-side profiling** often beats application profiling. A single unindexed query can dominate everything.
- **Distributed tracing** shows the critical path across services. In a load test, use a trace sample rate, because full tracing distorts the measurement.

Profile under load, not at rest. Idle profiles point at the wrong code.

## Common mistakes

- **Running the GUI load generator for a real test.** JMeter's GUI consumes resources and distorts results. Always `-n`.
- **Using `constant-vus` for a latency measurement.** Closed models hide queueing and understate tail latency. Use an arrival-rate executor.
- **Setting `maxVUs` too low.** Symptom: k6 warns that it cannot reach the requested rate, and your "high load" test quietly ran at a lower load. Symptom in other tools: dropped iterations or a rising "iteration dropped" count.
- **Not pre-allocating VUs.** Symptom: the first seconds of the test are slower while k6 allocates VUs, polluting the results. Use `preAllocatedVUs` sized above your expected peak.
- **Comparing p95 across runs with different warmup.** Symptom: the "regression" is JIT warmup or a cold cache. Add a warmup stage that is excluded from thresholds.
- **Running a 5-minute test against a database with 100 rows.** Symptom: unrealistically good results. Use a realistically sized dataset, and randomize key distribution so indexes are not perfectly cached.
- **Declaring success from a single run.** Symptom: flakiness in both directions. Run at least three times and look at the spread.
- **Measuring from a laptop on hotel wifi.** Symptom: you measured the network.

## Anti-patterns

- **Performance numbers with no environment description.** Unreproducible and unarguable later.
- **A load model copied from a blog post** that does not resemble your traffic mix.
- **Optimizing for the mean.** Leaves the users with the worst experience exactly where they are.
- **Running the load test against production**, which is a denial-of-service test against yourself.
- **Adding retry logic to the test script** and reporting the retried latency as the system's. This hides real failures; retries belong in the client's SLA, not in the measurement.
- **Treating a load test as a security test.** It is not a substitute for authorized penetration testing.
- **Buying more load generators before checking whether the application is even using the CPUs.** Most bottlenecks are a query, a lock, or a serialization step.

## References

- k6 documentation - https://k6.io/docs/
- k6 scenarios and executors - https://k6.io/docs/using-k6/scenarios/executors/
- k6 thresholds - https://k6.io/docs/using-k6/thresholds/
- k6 metrics reference - https://k6.io/docs/using-k6/metrics/reference/
- k6 getting started and examples - https://k6.io/docs/get-started/installation/ and https://k6.io/examples/
- Locust documentation - https://docs.locust.io/
- Apache JMeter user manual - https://jmeter.apache.org/usermanual/
- JMeter distributed testing - https://jmeter.apache.org/usermanual/distributed-test.html
- Google SRE Book, service level objectives - https://sre.google/sre-book/service-level-objectives/
- Google SRE Book, handling overload - https://sre.google/sre-book/handling-overload/
- Gil Tene, "How not to measure latency" (coordinated omission) - https://www.azul.com/presentation/how-not-to-measure-latency/
- HdrHistogram - https://hdrhistogram.github.io/HdrHistogram/
- py-spy - https://github.com/benfred/py-spy