---
name: Background Jobs and Task Queues
description: Covers asynchronous job processing architecture: choosing between SQS, Redis, RabbitMQ, and Kafka; at-least-once versus exactly-once delivery; idempotent handlers; retries with exponential backoff and jitter; dead letter queues; scheduling and cron in containers; visibility timeouts; and poison message handling. Use when the user asks to add a background worker, choose a queue, implement retries or a DLQ, schedule periodic jobs, or debug duplicate and stuck messages.
metadata:
  opencode/autoinvoke: true
---

# Background Jobs and Task Queues

A queue turns a synchronous request into a durable promise. That promise is only as strong as your delivery semantics: the broker generally promises at-least-once delivery, so your handler must be idempotent, your retries must be bounded, and your failures must land somewhere a human can inspect. Everything else in this skill follows from those three facts.

## When to use this

- Moving slow or unreliable work (email, image processing, third-party calls) off the request path.
- Choosing between a managed queue (SQS), Redis, RabbitMQ, and Kafka.
- Fixing duplicate jobs, jobs that run twice, or double charges from a worker.
- Adding retry with exponential backoff and jitter, or tuning retry counts and caps.
- Setting up a dead letter queue and a process for draining it.
- Scheduling recurring jobs (cron), including running cron inside containers.
- Tuning visibility timeouts, prefetch, or concurrency.
- Handling poison messages that fail regardless of retry count.

## Quick start

A worker with idempotency, bounded retries with jitter, and a dead-letter path:

```ts
import { createHash } from "node:crypto";
import { SQSClient, ReceiveMessageCommand, DeleteMessageCommand } from "@aws-sdk/client-sqs";

const sqs = new SQSClient({ region: process.env.AWS_REGION });
const QUEUE_URL = process.env.JOBS_QUEUE_URL!;

// --- Handler must be idempotent -----------------------------------------
type Job =
  | { type: "send_email"; jobId: string; to: string; templateId: string }
  | { type: "resize_image"; jobId: string; assetId: string; width: number };

declare const mailer: { send(to: string, templateId: string): Promise<void> };
declare const images: { resize(assetId: string, width: number): Promise<string> };
declare const dedupe: {
  // Atomically claim this jobId. Returns false if another worker already did.
  claim(jobId: string, ttlSeconds: number): Promise<boolean>;
  markDone(jobId: string): Promise<void>;
  release(jobId: string): Promise<void>;
};

// --- Backoff with full jitter -------------------------------------------
function backoffMs(attempt: number, baseMs = 1000, capMs = 60 * 60 * 1000): number {
  const exp = Math.min(capMs, baseMs * 2 ** attempt);
  // Full jitter: uniform in [0, exp). Avoids synchronized retry storms.
  return Math.floor(Math.random() * exp);
}

async function handle(job: Job): Promise<void> {
  // Dedupe: if a previous attempt already completed this jobId, return.
  const claimed = await dedupe.claim(job.jobId, 900);
  if (!claimed) return;

  try {
    switch (job.type) {
      case "send_email":
        await mailer.send(job.to, job.templateId);
        break;
      case "resize_image":
        await images.resize(job.assetId, job.width);
        break;
    }
    await dedupe.markDone(job.jobId);
  } catch (err) {
    // Release so a retry can claim it again.
    await dedupe.release(job.jobId);
    throw err;
  }
}

async function runWorker(): Promise<void> {
  for (;;) {
    const received = await sqs.send(
      new ReceiveMessageCommand({
        QueueUrl: QUEUE_URL,
        MaxNumberOfMessages: 10,
        // How long this worker owns the message before it becomes visible again.
        // Must exceed the handler's p99 duration or you get double processing.
        VisibilityTimeout: 120,
        WaitTimeSeconds: 20, // long polling, avoids empty responses and API cost
      }),
    );

    if (!received.Messages?.length) continue;

    for (const msg of received.Messages) {
      const attempt = Number(msg.Attributes?.ApproximateReceiveCount ?? "1") - 1;
      let job: Job;
      try {
        job = JSON.parse(msg.Body!) as Job;
      } catch {
        // Unparseable body: retrying cannot help. Delete and log.
        console.error("poison message, unparseable", msg.MessageId);
        await sqs.send(new DeleteMessageCommand({ QueueUrl: QUEUE_URL, ReceiptHandle: msg.ReceiptHandle! }));
        continue;
      }

      try {
        await handle(job);
        await sqs.send(new DeleteMessageCommand({ QueueUrl: QUEUE_URL, ReceiptHandle: msg.ReceiptHandle! }));
      } catch (err) {
        // Do not delete: leave it. After MaxReceiveCount it moves to the DLQ.
        // Log the attempt count so operators can see the poison trajectory.
        console.warn("job failed", { jobId: job.jobId, attempt, error: String(err) });
        // Optionally, for faster feedback, re-publish with a delay via
        // a delay-capable queue and delete the original.
        void backoffMs(attempt);
      }
    }
  }
}

void runWorker();
```

This worker is deliberately simple. Real systems add a circuit breaker on downstream calls, structured logging, and metrics per job type. The four things above, however, are not optional: dedupe key, unparseable-body handling, visibility timeout sizing, and "do not delete on failure".

## Queue architectures

| | SQS (and equivalents) | Redis list/Streams | RabbitMQ | Kafka |
| --- | --- | --- | --- | --- |
| Model | at-least-once queue | queue or stream | queue with ack/nack | partitioned append-only log |
| Ordering | no (FIFO mode: per message group) | per stream/list | per queue | per partition |
| Retention | message TTL / until consumed | until trimmed or acked | until acked or TTL | time-based, independent of consumption |
| Replay from a point | no | limited (consumer group state) | no | yes, by resetting offsets |
| Multiple independent consumers | competing consumers | competing | competing | yes, consumer groups |
| Routing/fanout | needs SNS or a fanout design | publish/subscribe | rich exchanges, routing keys | topics and partitions |
| Operational burden | managed, lowest | low if Redis is already required | medium | highest (partitions, rebalancing, retention) |
| Delivery guarantee | at-least-once | at-least-once | at-least-once (publisher confirms) | at-least-once end-to-end; effectively-once with idempotent producers and read-committed isolation |
| Best for | task queues on AWS, decoupling | simple background jobs, rate limiting, caching by-product | flexible routing, work queues, RPC-style | event streaming, high-throughput logs, replay |

Decision guide:

- **Task queue, AWS-native, least ops:** SQS standard. Add a DLQ. Move to FIFO only when ordering per entity matters more than throughput flexibility.
- **Simple job processing, Redis already required:** Redis with a dedicated job library (BullMQ and similar for Node, RQ/Celery-with-Redis for Python). Streams give consumer groups and acknowledgment.
- **Complex routing, work distribution, RPC, or non-AWS:** RabbitMQ. Exchanges with routing keys and quorum queues give durable fanout and safer failure modes than classic mirrored queues.
- **Event log, replay, many independent consumers, high throughput:** Kafka. Do not choose Kafka for a simple "email this user" job; its operational model is designed for streaming data, not ad hoc task dispatch.

### At-least-once versus exactly-once

"At-least-once" means the broker will redeliver a message until it learns you handled it, so a crash after your side effect but before the ack causes a duplicate. This is the default and the honest guarantee everywhere.

"Exactly-once" is achievable only in narrow senses:

- **Kafka's transactional producer** with `enable.idempotence=true` gives exactly-once for writes to Kafka (no duplicates or reordering from producer retries).
- **Idempotent consumers** achieve effectively-once *effects* by making the effect conditional on a dedupe key. This is the pattern you actually want: dedupe in the datastore, not in the broker.
- Kafka's read-process-write with exactly-once semantics (idempotent producer plus read-committed consumers plus Kafka transactions) covers the case where the producer and the datastore are both Kafka-connected. It does not cover a Postgres or HTTP side effect.

For any queue where the side effect is external (charge a card, send an email, write Postgres), choose at-least-once transport plus an idempotent handler with a durable dedupe key. Do not chase a broker-level exactly-once promise you do not control.

### Idempotency key design

Every job should carry a stable key that identifies the logical operation, not the delivery attempt:

```ts
// The key is derived from the business operation, so a retry produces the same key.
function orderFulfilledKey(tenantId: string, orderId: string): string {
  return `order.fulfilled:${tenantId}:${orderId}`;
}

function sendEmailKey(userId: string, templateId: string, dedupeWindow: string): string {
  return `email.sent:${userId}:${templateId}:${dedupeWindow}`;
}
```

For partitioned-order-sensitive work, include an entity id in the message-group key so all events for one entity land in order (SQS FIFO `MessageGroupId`, Kafka message key). This is how you get per-entity ordering without giving up parallelism across entities.

## Retries

Retry only what is transient. Classify errors:

| Error class | Examples | Action |
| --- | --- | --- |
| Transient infrastructure | connection reset, 503, timeout | Retry with backoff. |
| Rate limited | 429, throttling | Retry with backoff, often with a longer cap. |
| Transient data conflict | deadlock, serialization failure | Retry. |
| Permanent client error | 400, validation failure, missing record | Do not retry; send to DLQ or drop with a reason. |
| Bug in your code | `TypeError`, null reference | Do not retry blindly; fix the code. Route to DLQ after a small attempt budget. |

Retrying a permanent error wastes capacity and delays the failure signal. Cap attempts and route exhausted messages to a DLQ.

Backoff must include jitter. Exponential backoff without jitter causes a thundering herd: every failed job retries at the same instant and re-creates the overload.

```ts
function fullJitter(attempt: number, baseMs = 500, capMs = 300_000): number {
  const exp = Math.min(capMs, baseMs * 2 ** attempt);
  return Math.floor(Math.random() * exp);
}

function equalJitter(attempt: number, baseMs = 500, capMs = 300_000): number {
  const exp = Math.min(capMs, baseMs * 2 ** attempt);
  return Math.floor(exp / 2 + Math.random() * (exp / 2));
}

// Decorrelated jitter also works and spreads well across many workers.
function decorrelatedJitter(prevMs: number, baseMs = 500, capMs = 300_000): number {
  return Math.min(capMs, Math.floor(Math.random() * (prevMs * 3)));
}
```

Prefer full jitter as the default. Equal jitter guarantees some minimum delay (useful when you do not want instant retries); decorrelated jitter spreads best when you have many workers sharing one queue.

Respect the downstream's rate limit. If the third-party API returns `Retry-After`, use it rather than your own schedule.

## Dead letter queues and poison messages

A dead letter queue (DLQ) is where exhausted or unprocessable messages go so that they stop consuming capacity and become inspectable. Without one, a poison message retries forever and holds a worker.

Poison message categories:

- **Unparseable payload.** Retrying cannot help; a JSON parse failure is permanent. Log it with the message id and delete or route to a "bad messages" queue.
- **Valid payload, permanent rejection.** Validation fails, referenced record is gone. Route to DLQ with a reason after 1 or 2 attempts, not 10.
- **Code bug.** Retries all fail identically. A small attempt budget (2 or 3) surfaces the problem fast; a large budget hides it for hours.
- **Flaky downstream.** Legitimately transient. Use the full retry budget and the DLQ as the final backstop.

Always attach a reason when moving to a DLQ: attempt count, last error class, and timestamp. An unexplained DLQ is a black hole. Alert on DLQ depth above zero, and build a replay tool so operators can re-drive a message after a fix.

For SQS, configure a redrive policy with `maxReceiveCount` on the source queue so the broker handles redelivery count and DLQ routing automatically, rather than tracking attempt counts in your worker. For RabbitMQ, quorum queues have built-in delivery limits and dead-lettering to a configured target; check the current quorum queue documentation for the exact policy keys.

## Visibility timeouts and prefetch

**Visibility timeout (SQS) / unacked message / prefetch** is the core tuning knob: it defines how long a message is hidden from other consumers after delivery.

- Set visibility timeout above the handler's p99 runtime. If it is shorter, the message becomes visible again while the first handler is still running, and a second worker processes it concurrently. This is the single most common cause of "why did this job run three times".
- Set it above your maximum expected runtime including downstream waits, or renew the timeout during long work. SQS supports extending the visibility timeout by re-receiving the message with a longer value.
- Prefetch (RabbitMQ `prefetch_count`, or SQS `MaxNumberOfMessages`) controls how many unacked messages a worker holds. High prefetch improves throughput but reduces fairness: one slow job can stall a worker holding many messages. Prefer a moderate prefetch (roughly your worker concurrency) for long-running handlers.

Acknowledge (delete the message) only after the side effect is durably committed, not after validation or before the work.

## Cron and scheduling

Recurring jobs have a well-known problem in distributed systems: **a cron trigger running on every replica runs the job N times.** Solutions, in order of preference:

1. **Managed scheduler.** A single scheduler service (EventBridge Scheduler on AWS, Cloud Scheduler on GCP, a Kubernetes CronJob) fires once and pushes to the queue. The queue, not the trigger, handles distribution.
2. **Leader election.** All replicas schedule, but only the leader executes. Use a distributed lock with a TTL (etcd, Redis `SET NX PX`, a database advisory lock) and a fencing token so a paused leader cannot act after losing the lease.
3. **Queue-based dedupe.** Every replica enqueues the run, and the handler dedupes on a key like `cron:daily-report:2026-04-01` so only one execution proceeds. Simplest when idempotent execution is easy.

Rules for cron jobs generally:

- Make every cron job idempotent for its logical run key (usually the scheduled time). Retries and duplicate triggers are normal, not exceptional.
- Cron in containers: a plain `cron` daemon inside each container replicates the problem. If you must, add a leader-election or Redis lock around the job body.
- Avoid overlapping runs. A slow job that outlasts its interval must skip or queue, not start a second concurrent copy. Use the run key or a lock with the job duration as the lock TTL.
- Run one long-lived worker loop for scheduled jobs rather than restarting the container every minute; container churn loses in-flight state.

Scheduling libraries (Celery beat, BullMQ repeatable jobs, node-cron, Airflow, Temporal schedules, Kubernetes CronJobs) handle the trigger layer; you still own idempotency and overlap prevention.

## Rate limiting and concurrency control

Downstream APIs and databases have limits your workers must respect. Enforce them in the worker:

```ts
// Token bucket limiting concurrent calls per downstream.
class Semaphore {
  private permits: number;
  private waiters: Array<() => void> = [];

  constructor(permits: number) {
    this.permits = permits;
  }

  async acquire(): Promise<void> {
    if (this.permits > 0) {
      this.permits -= 1;
      return;
    }
    await new Promise<void>((resolve) => this.waiters.push(resolve));
  }

  release(): void {
    const next = this.waiters.shift();
    if (next) next();
    else this.permits += 1;
  }
}

const downstream = new Semaphore(20); // at most 20 in-flight calls

await downstream.acquire();
try {
  await callDownstream();
} finally {
  downstream.release();
}
```

Separate queues per priority or per tenant so one noisy tenant or a flood of low-priority work cannot starve critical jobs. Rate limits at the producer (who enqueues) plus concurrency limits at the consumer (who executes) work together: the producer controls arrival, the consumer controls pressure on downstream.

## Ordering and idempotency together

If you need both, partition by entity. Consumers process one partition at a time per key, guaranteeing per-key order while running many keys in parallel:

```ts
// Kafka: partition by entity so all events for one entity share a partition.
await producer.send({
  topic: "order-events",
  messages: [{ key: orderId, value: JSON.stringify(event) }], // key -> hash -> partition
});

// SQS FIFO: set MessageGroupId to the entity; only one message per group is
// in flight at a time, which is what gives per-entity ordering.
await sqs.send(
  new SendMessageCommand({
    QueueUrl: FIFO_QUEUE_URL,
    MessageBody: JSON.stringify(event),
    MessageGroupId: orderId,
    MessageDeduplicationId: createHash("sha256").update(JSON.stringify(event)).digest("hex"),
  }),
);
```

Per-entity ordering with a global queue still lets unrelated entities proceed in parallel, which is the property you actually want. Global ordering is almost never required and always costs throughput.

## Checklist

- [ ] Every job payload carries a stable, business-derived idempotency key.
- [ ] Handlers are idempotent via a durable dedupe store with a TTL, not an in-memory `Set`.
- [ ] The side effect is committed before the message is acknowledged.
- [ ] Visibility timeout or prefetch exceeds the handler's p99 runtime, with renewal for long jobs.
- [ ] Retries are bounded, use exponential backoff with jitter, and honor `Retry-After`.
- [ ] Permanent errors are not retried; they go to a DLQ immediately or after a tiny budget.
- [ ] A DLQ exists, is monitored with an alert on non-zero depth, and has a replay path.
- [ ] Poison/unparseable messages are detected and routed out, not retried forever.
- [ ] Overlapping scheduled runs are prevented via a single managed scheduler, a leader lock, or a run-key dedupe.
- [ ] Cron jobs are idempotent per scheduled time.
- [ ] Concurrency and rate limits to downstream systems are enforced in the worker.
- [ ] High-priority work is on a separate queue/priority so it is not starved.
- [ ] Per-entity ordering uses partitioning (message key / group id), not a single global queue.

## Common mistakes

- **Deleting the message before the work succeeds.** Symptom: failures vanish silently, no retry, no DLQ, jobs just disappear. Fix: acknowledge only after the durable effect is committed.
- **Visibility timeout shorter than the handler.** Symptom: the same job runs on two workers concurrently, producing duplicate emails or double charges. Fix: size the timeout above p99 runtime and renew during long work.
- **Retry loop with no jitter.** Symptom: a downstream outage produces a synchronized retry storm that keeps the downstream down after recovery. Fix: full jitter on exponential backoff.
- **Retrying permanent errors.** Symptom: validation failures burn the full retry budget and delay the error signal; DLQ fills with messages that will never succeed. Fix: classify errors and fail fast on permanent ones.
- **In-memory dedupe set.** Symptom: after a deploy or on a second worker, previously completed jobs run again. Fix: durable dedupe store keyed by the job key with a TTL longer than the retry window.
- **Cron on every replica.** Symptom: the daily job runs once per pod, sometimes dozens of times. Fix: managed scheduler, leader election, or run-key dedupe.
- **No DLQ.** Symptom: a poison message retries forever, tying up a worker and never surfacing. Fix: configure a DLQ with `maxReceiveCount`/delivery limits and alert on it.
- **Prefetch set to the max.** Symptom: one worker hoards thousands of unacked messages, others idle, and a single slow job stalls a large share of throughput. Fix: moderate prefetch matched to handler concurrency.
- **Unbounded job payload.** Symptom: large payloads bloat the broker and break message size limits. Fix: put large data in object storage and pass a reference.
- **Global ordering requirement by default.** Symptom: throughput collapses to a single-threaded pipeline because every event must be strictly ordered. Fix: order per entity only, which is almost always the real requirement.

## Anti-patterns

- **Using a queue as a synchronous call in disguise.** Symptom: a request enqueues a job and blocks on the result via polling or a websocket anyway; you have added a broker and lost traceability. Fix: if the caller needs the result, make it synchronous; if not, return `202` with a status resource.
- **Retrying forever with an increasing delay and no cap.** Symptom: a permanent failure becomes an invisible, unbounded cost that never reaches an operator. Fix: cap attempts, then DLQ.
- **Exactly-once claimed without a dedupe key.** Symptom: duplicate charges under load because the code assumed the broker would not redeliver. Fix: dedupe in the datastore; brokers do not protect external side effects.
- **A "queue" that is really a `sleep` in the web process.** Symptom: a request handler holds a worker thread waiting on a job, so background work still caps request throughput. Fix: hand off to a real queue and free the request.
- **One queue for everything.** Symptom: a burst of low-priority reports starves password-reset emails behind hours of backlog. Fix: separate queues by priority or workload and scale workers independently.
- **Encoding business logic in the queue name** (`queue_new_user_signup_v2_final`). Symptom: routing depends on string matching and the topology is undocumented. Fix: put a typed, versioned payload in a small number of well-named queues.
- **Passing secrets in the job payload.** Symptom: secrets land in broker storage, logs, and dead letter queues where they persist. Fix: pass identifiers and fetch secrets at execution time from a secret store.
- **Fan-out workers that share the job's database connection pool limits unconsciously.** Symptom: job workers and the web server starve each other because they share the same pool. Fix: separate pools and, ideally, separate database roles for job workers.

## References

- AWS SQS Developer Guide — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/
- SQS visibility timeout — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- SQS dead letter queues — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
- SQS at-least-once delivery (standard queues) — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html
- SQS FIFO message group id (per-group ordering) — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/using-messagegroupid-property.html
- SQS short and long polling — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-short-and-long-polling.html
- RabbitMQ reliability guide — https://www.rabbitmq.com/docs/reliability
- RabbitMQ quorum queues (delivery limits, dead lettering) — https://www.rabbitmq.com/docs/quorum-queues
- Kafka documentation (delivery semantics, producer configs) — https://kafka.apache.org/documentation/
- Kafka consumer configs (ack and commit behavior) — https://kafka.apache.org/documentation/#consumerconfigs_enable.auto.commit
- Kafka producer configs (idempotence) — https://kafka.apache.org/documentation/#producerconfigs_enable.idempotence
- Redis Streams — https://redis.io/docs/latest/develop/data-types/streams/
- BullMQ (Node job queue: retries, repeatable jobs) — https://docs.bullmq.io/
- Celery tasks and configuration — https://docs.celeryq.dev/en/stable/userguide/tasks.html
- Celery configuration reference (retry policy) — https://docs.celeryq.dev/en/stable/userguide/configuration.html
- Temporal documentation (durable execution) — https://docs.temporal.io/
- AWS Builders' Library, Timeouts, retries, and backoff with jitter — https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- Google SRE Book, handling overload and retry budgets — https://sre.google/sre-book/handling-overload/