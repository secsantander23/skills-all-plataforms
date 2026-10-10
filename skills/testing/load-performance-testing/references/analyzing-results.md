# Analyzing and Reporting Load Test Results

A load test produces data. The value of the test is in the interpretation, and in the discipline of reporting conditions alongside numbers.

## Percentiles

Percentiles answer "what does the user experience at position N in the sorted set of requests?"

| Percentile | Reads as |
| --- | --- |
| p50 | Half of users are faster than this |
| p90 | The slow tail users start here |
| p95 | Your common slowest 5 percent |
| p99 | The extreme tail, often the difference between "works" and support tickets |
| max | A single worst case; useful for finding an outlier bug, useless as an SLO |

Rules:

- Never report an average alone. If you must report both, give the histogram.
- Never average percentiles across endpoints or runs. p95 of p95 is meaningless.
- Percentiles over a small sample are noisy. p99 of 100 requests is essentially the maximum. Aim for thousands of samples per percentile you intend to gate on.
- Define the window: "p95 over the last 5 minutes at 400 requests per second", not "p95".

## Histograms

A histogram reveals structure an average destroys.

- **Bimodal** (two peaks): usually two code paths. Find the split condition, then fix the slow one.
- **Long right tail**: one pathological path. Look for a cache miss, a lock, or a retry.
- **Uniform spread**: no clear dominant path; the work is genuinely variable.
- **Sharp cliff at a value**: a timeout or a limit being hit. Count how many requests land exactly at the timeout.

## Saturation analysis

Record these at each step of a ramp:

| Metric | Reading |
| --- | --- |
| Offered rate vs achieved rate | A gap means the generator could not keep up |
| Throughput (requests per second) | Flat throughput plus rising latency is the saturation signature |
| p95 and p99 | The first to degrade is usually p99 |
| Error rate | Timeouts, 5xx, and your own 429s |
| CPU per core | One hot core caps throughput no matter how many you add |
| Memory and GC pause time | Growth over time indicates a leak |
| Pool utilization | Connection pools and thread pools saturating |
| Downstream calls | Rate limits and timeouts from dependencies |

The saturation point is the maximum throughput that still meets your SLO. Report it with the environment description attached, or the number is not comparable later.

## Report template

```
Workload
  Model:      constant-arrival-rate, 400 rps, 10m, 3 concurrent scenarios
  Data:       2,000,000 orders, 200,000 customers, ids sampled uniformly
  Journey:    browse (70%) -> add to cart (20%) -> checkout (10%)

Environment
  Application: build 8f3c2a1
  Instances:   4 x 4 vCPU / 8 GB
  Database:    2 x PostgreSQL primary, 1 replica, data as of 2024-03-01
  Load gen:    same availability zone, 2 m5.xlarge

Results (steady state, last 5 minutes)
  Throughput:  396 rps achieved
  p50:         42 ms
  p95:         210 ms
  p99:         640 ms
  Error rate:  0.2 percent (0.8 percent at peak)
  Saturation:  CPU 78 percent average, 99 percent on the checkout workers

Comparison to previous baseline (build 1a9f0e4)
  p99: 640 ms vs 520 ms (regression, budget is 600 ms) - FAIL
  Throughput: 396 vs 400 rps (within noise)
```

That format makes the result actionable three months later: someone can tell whether it is comparable to a prior run.

## CI integration

Run the load test as a gate, not as an occasional ritual.

1. Keep a small, fixed scenario (a couple of minutes, one journey, realistic data size) in CI on every release candidate.
2. Assert thresholds so the build fails on regression.
3. Run a longer ramp or soak nightly on staging, not on every commit.
4. Compare against the previous run rather than an absolute number where possible, so hardware drift does not cause false alarms.
5. Record the environment description in the artifact automatically, so it cannot be forgotten.

## Overload and degradation

Under overload, a good system degrades gracefully. Test that it does.

- Does it shed load (429, queue depth limits) rather than collapse?
- Do timeouts have deadlines that unwind work, or do they leave orphaned transactions?
- Are connections returned to the pool under failure, or does the pool leak?
- Does the retry policy amplify load into a retry storm? Backoff with jitter is the mitigation.
- Does a slow downstream cause a full backup of the local queue, exhausting memory?

Use k6's `http_req_failed` plus application error classification, and confirm behaviour against the SLO error budget.

## What not to conclude

- That the absolute number transfers to another environment. It does not.
- That a pass at 400 rps means the system is safe at 4000 rps. Extrapolation across a saturation point is speculation.
- That a failure at high concurrency is a bug rather than the intended behaviour of a rate limiter you have not accounted for.
- That a p99 improvement came from the code, when it came from a warmer cache or a smaller dataset.

## References

- k6 metrics reference - https://k6.io/docs/using-k6/metrics/reference/
- k6 thresholds - https://k6.io/docs/using-k6/thresholds/
- Google SRE Book, service level objectives - https://sre.google/sre-book/service-level-objectives/
- Google SRE Book, handling overload - https://sre.google/sre-book/handling-overload/
- Google SRE Workbook, load testing guidance - https://sre.google/workbook/load-testing/
- HdrHistogram, why percentile accuracy matters - https://hdrhistogram.github.io/HdrHistogram/