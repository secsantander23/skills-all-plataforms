# Dashboards and Alerts

## SLO definitions

An SLO is a target you commit to, not a metric you hope improves. Structure:

| Element | Example |
| --- | --- |
| SLI | The proportion of valid requests served successfully |
| Measurement window | Rolling 28 days, or rolling 30 minutes for the fast burn check |
| Objective | 99.9% of valid requests, i.e. an error budget of 0.1% |
| Scope | Which requests count; exclude bots, health checks, and your own synthetic traffic |

Getting the scope right matters more than the number. Counting a synthetic monitor's
requests as failures makes the SLO worse; counting them as successes makes it better.
Decide explicitly and document it.

```promql
# SLI: proportion of successful requests over 28 days.
(
  sum(increase(http_requests_total_total[28d]))
    - sum(increase(http_requests_total_bad_total[28d]))
)
  / sum(increase(http_requests_total_total[28d]))
```

```yaml
# Prometheus rule_file: error budget remaining, as a fraction of budget left.
groups:
  - name: slo
    rules:
      - record: slo:availability:ratio_rate28d
        expr: |
          (
            sum(rate(http_requests_total_total[28d]))
              - sum(rate(http_requests_total_bad_total[28d]))
          ) / sum(rate(http_requests_total_total[28d]))
      - record: slo:error_budget_remaining
        expr: |
          1 - (
            (1 - slo:availability:ratio_rate28d) / 0.001
          )
```

When remaining budget is negative, the SLO is already violated for the window. That is the
signal to stop shipping risky changes, not a signal to panic.

## Burn rates

A burn rate is how fast you are consuming the error budget relative to the period the
budget covers. Burn rate 1 means the budget will be exhausted exactly at the end of the
window; burn rate 14.4 means it will be exhausted in a twenty-fourth of the window.

The SRE Workbook derives alert thresholds from how quickly you want to be paged relative to
how quickly you can respond:

| Burn rate | Long window | Short window | Budget consumed | Response |
| --- | --- | --- | --- | --- |
| 14.4 | 1 hour | 5 minutes | 2% in an hour | Page |
| 6 | 6 hours | 30 minutes | 5% in a day | Page |
| 3 | 1 day | 2 hours | 10% in a day | Page |
| 1 | 3 days | 6 hours | 10% in a month | Ticket |

Use the `and` of the long and short window expressions. The long window prevents paging on a
single spike; the short window makes a genuine sustained burn page quickly. Both conditions
must hold.

```yaml
groups:
  - name: slo-alerts
    rules:
      - alert: SLOBurnRateFast
        expr: |
          (
            sum(rate(http_requests_total_bad_total[5m]))
              / sum(rate(http_requests_total_total[5m])) > (14.4 * 0.001)
          )
          and
          (
            sum(rate(http_requests_total_bad_total[1h]))
              / sum(rate(http_requests_total_total[1h])) > (14.4 * 0.001)
          )
        for: 2m
        labels:
          severity: page
        annotations:
          summary: "Fast error-budget burn on the availability SLO"
          runbook_url: "https://runbooks.internal/api/slo-burn"
          dashboard_url: "https://grafana.internal/d/api"

      - alert: SLOBurnRateSlow
        expr: |
          (
            sum(rate(http_requests_total_bad_total[6h]))
              / sum(rate(http_requests_total_total[6h])) > (6 * 0.001)
          )
          and
          (
            sum(rate(http_requests_total_bad_total[30m]))
              / sum(rate(http_requests_total_total[30m])) > (6 * 0.001)
          )
        for: 15m
        labels:
          severity: page
        annotations:
          summary: "Sustained error-budget burn on the availability SLO"
          runbook_url: "https://runbooks.internal/api/slo-burn"

      - alert: ErrorBudgetExhausted
        expr: slo:error_budget_remaining < 0
        for: 1h
        labels:
          severity: ticket
        annotations:
          summary: "Availability SLO error budget is exhausted; freeze risky changes"
          runbook_url: "https://runbooks.internal/api/error-budget"
```

Adjust `for` durations and thresholds to your actual response time. Read the SRE Workbook
chapter rather than copying these numbers blindly.

## Alert rule structure

```yaml
- alert: ApiHighLatency
  expr: |
    service:http_request_duration_seconds:p99_5m{job="api"} > 0.5
    and
    sum(rate(http_requests_total{job="api"}[5m])) > 1
  for: 10m
  labels:
    severity: page
    team: platform
  annotations:
    summary: "API p99 latency above 500ms (job=api)"
    description: "p99 has been above 500ms for 10 minutes at {{ $labels.route }}."
    runbook_url: "https://runbooks.internal/api/latency"
    dashboard_url: "https://grafana.internal/d/api?var-route={{ $labels.route }}"
```

The `and` with a traffic check is a rate-limit guard: a latency alert on a route receiving
one request an hour is noise. Adding `sum(rate(...)) > threshold` prevents paging on
statistical noise from very low traffic.

Alert annotations must include:

- **What** broke, in plain language, with the labels that fired.
- **A runbook URL** that is reachable without authentication from the pager.
- **A dashboard link** pre-filtered to the firing labels.
- **A severity** consistent with your routing policy.

## Dashboard layouts

### Service overview

Row 1, the four golden signals for the service:

| Panel | Query |
| --- | --- |
| Request rate | `sum by (route) (rate(http_requests_total[5m]))` |
| Error ratio | `sum(rate(http_requests_total{status=~"5.."}[5m])) / clamp_min(sum(rate(http_requests_total[5m])), 1)` |
| Latency p50 / p95 / p99 | `histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))` |
| Saturation | `sum(http_requests_in_flight) / myapp_concurrency_limit` |

Row 2, dependencies: database pool waiters, database latency, cache hit ratio, queue depth,
outbound call latency.

Row 3, context: version label as an annotation, deploy markers, and a config-change marker.

Row 4, diagnostics: per-instance breakdown (to spot imbalance), error types, and
saturation detail.

### Instance imbalance

```promql
# Ratio of the busiest instance's rate to the mean. Near 1 means balanced.
max by (pod) (rate(http_requests_total[5m]))
  / (avg by (pod) (rate(http_requests_total[5m])))
```

An imbalance with `least_conn` load balancing usually means one instance is slower, not
that the algorithm is broken. Follow it to CPU, GC, or connection pool saturation on that
instance.

### Cache effectiveness

```promql
# Hit ratio from a counter of hits and misses.
sum(rate(cache_requests_total{result="hit"}[5m]))
  / sum(rate(cache_requests_total[5m]))
```

```promql
# Time spent loading on a miss, versus time served from cache.
sum(rate(cache_miss_duration_seconds_sum[5m]))
  / sum(rate(cache_miss_duration_seconds_count[5m]))
```

The first tells you whether the cache is being used; the second tells you whether a miss
matters. Both together decide whether the cache is earning its cost.

## Tuning a noisy alert

Procedure:

1. **Find the source.** Is the flapping instance, the deploy, the cron job, or the test
   suite? Check the alert's `for` duration against the noise period.
2. **Widen the window.** If it fires on a five-minute spike, require a longer window or
   add a `for` clause.
3. **Add a traffic guard.** Require enough requests for the signal to be statistically
   meaningful.
4. **Split by severity.** Keep a ticket-level alert for the same condition at a slower
   window.
5. **Fix the underlying noise.** If it is a real flap, fix the cause. Silence is not a
   fix.
6. **Document the decision.** Every tuning decision recorded prevents the alert from being
   re-added and re-disabled next quarter.

Common causes and responses:

| Noise pattern | Likely cause | Response |
| --- | --- | --- |
| Fires at the top of every hour | Scheduled job or batch window | Exclude the job's traffic from the alert, or use a longer window |
| Fires during every deploy | Version change resets some state | Longer `for`, or alert on error ratio over a longer window |
| Fires on one instance repeatedly | One unhealthy instance | Alert per instance only for that metric, with a longer `for` |
| Fires at low traffic only | Percentile over a tiny sample | Add a minimum-rate guard |
| Fires after a dashboard change | Query now includes a new label | Check the recording rule still aggregates correctly |

## Alert routing

```
severity: page     -> on-call rotation, 24/7, requires a runbook
severity: ticket   -> team queue, business hours, no runbook required
severity: info     -> dashboard or chat channel only
```

Rules:

- One page per symptom. If latency and errors are both consequences of one failure, page
  once, on the symptom with the clearest user impact.
- Route by service ownership, so the team that can fix it is the team that is paged.
- Escalation must be automatic and time-bounded; a page that nobody acknowledges for an
  hour is worse than a late page.
- Test the pager itself. A runbook link that 404s at 3am is a broken alerting system.

## Checklist for each alert

- [ ] Fires on a user-visible symptom.
- [ ] Has `severity`, `team`, and a summary that reads well in a chat message.
- [ ] Links to a runbook that is reachable without authentication.
- [ ] Links to a dashboard filtered to the firing labels.
- [ ] Has a traffic or sample-size guard if it uses a ratio or percentile.
- [ ] Uses `for` long enough to avoid firing on normal transients.
- [ ] Has been triggered deliberately in a game day and the response documented.
- [ ] Reviewed after a real fire: did the summary make sense in the notification?

## Verifying before relying on this

Prometheus rule syntax, Grafana dashboard JSON, and alerting provider configuration all
change. Validate rules with `promtool check rules`, validate dashboards by importing them
into a scratch Grafana instance, and consult
<https://prometheus.io/docs/> and <https://grafana.com/docs/grafana/latest/>.
