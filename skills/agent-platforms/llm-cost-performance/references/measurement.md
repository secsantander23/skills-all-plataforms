# Measurement: tokens, latency, cost, capacity

## What to record per request

| Field | Type | Notes |
| --- | --- | --- |
| `request_id` | string | Joins every log line for this call |
| `task_id` | string | Joins the calls belonging to one user-visible task |
| `route` | string | Feature or endpoint that made the call |
| `model` | string | Exact model id, not a family name |
| `tier` | string | Your routing decision |
| `input_tokens` | number | Uncached input |
| `cache_read` | number | Prefix served from cache |
| `cache_write` | number | Prefix written to cache |
| `output_tokens` | number | Generated, including reasoning tokens where applicable |
| `ttft_ms` | number or null | Time to first token; null for non-streamed calls |
| `total_ms` | number | End to end |
| `outcome` | enum | `ok`, `error`, `aborted`, `timeout` |
| `retries` | number | Retries count toward cost and latency |
| `subject` | string | Tenant or user, from authenticated context only |

Usage field names differ per provider. Map them once in the client wrapper. Nothing
outside that wrapper should know the provider's field names.

## Streaming: measuring TTFT correctly

With a streamed response, TTFT is the gap between the request being accepted and the
first content-bearing chunk. A role-only chunk or a keepalive is not content.

```typescript
export async function callWithTiming(
  send: (onFirstContent: (t: number) => void) => Promise<unknown>,
): Promise<{ result: unknown; ttftMs: number; totalMs: number }> {
  const started = performance.now();
  let ttftMs: number | null = null;
  const result = await send(() => {
    ttftMs ??= performance.now() - started;
  });
  const totalMs = performance.now() - started;
  return { result, ttftMs: ttftMs ?? totalMs, totalMs };
}
```

Do not average TTFT and total latency together and call it "latency". Users experience
TTFT. Saturation and retries show up in total.

## Percentiles, not means

A mean latency hides everything that matters. Report p50, p90, p99, and the maximum.
Break them down by route and by tier, because a fast tier and a frontier tier have
different distributions and averaging them is meaningless.

```typescript
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (p / 100) * (sorted.length - 1);
  const low = Math.floor(rank);
  const high = Math.ceil(rank);
  if (low === high) return sorted[low];
  return sorted[low] + (sorted[high] - sorted[low]) * (rank - low);
}
```

## Cost attribution

Compute cost per task, not per request:

```text
cost per task = sum(tokens_including_cache_writes and outputs for every request in the task)
               / number of tasks that reached a successful outcome
```

The denominator matters. If you drop failed tasks from the denominator, a system that
fails most requests looks free.

## Pricing data

Prices come from the provider. Two workable patterns:

1. Fetch and cache the pricing page or the provider's pricing endpoint at runtime, and
   attach a retrieved-at timestamp so stale data is visible.
2. Keep prices in configuration with a source URL and a review date, and add a job that
   alerts when the source changes.

Do not transcribe prices into source. Do not present a number you have not read today.

## Capacity

Concurrency against a provider is usually limited by tokens per minute, requests per
minute, and sometimes a per-model rate. Build a budget object per deployment rather than
discovering limits by getting throttled:

```typescript
export interface RateBudget {
  requestsPerMinute: number;
  tokensPerMinute: number;
  maxConcurrent: number;
}

export class TokenBucketLimiter {
  private tokens: number;
  private lastRefill = Date.now();

  constructor(
    private readonly capacity: number,
    private readonly refillPerSecond: number,
  ) {
    this.tokens = capacity;
  }

  async acquire(cost: number): Promise<void> {
    for (;;) {
      const now = Date.now();
      this.tokens = Math.min(
        this.capacity,
        this.tokens + ((now - this.lastRefill) / 1000) * this.refillPerSecond,
      );
      this.lastRefill = now;
      if (this.tokens >= cost) {
        this.tokens -= cost;
        return;
      }
      const waitMs = Math.ceil(((cost - this.tokens) / this.refillPerSecond) * 1000);
      await new Promise((resolve) => setTimeout(resolve, Math.min(waitMs, 1000)));
    }
  }
}
```

Verify actual limits with the provider for the specific account and tier. Treat the
numbers you configure as your own safety margin, not as the provider's published limit.

## Retries

Retries are invisible in mean latency and visible in cost. Record the retry count, cap
retries per task, and add jitter:

```typescript
export function backoffDelay(attempt: number, baseMs = 250, capMs = 8000): number {
  const exponential = Math.min(capMs, baseMs * 2 ** attempt);
  const jitter = Math.random() * exponential * 0.25;
  return Math.round(exponential + jitter);
}
```

Cap total attempts per task. An agent loop that retries forever on a failing tool is
both a cost problem and an availability problem.

## Dashboards to keep

1. Tokens per task by route, over time, split by model and tier.
2. Cache read and write tokens, and derived hit rate.
3. TTFT p50 and p99 by route.
4. Cost per successful task by route.
5. Error, timeout, and abort rates, including retries.
6. Context length distribution, so a growing context shows up before the bill does.

Alert on the derived metrics (cost per successful task, hit rate) rather than only on
raw totals, which move too slowly to be useful.