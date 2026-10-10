---
name: Microservice Contracts and Resilience
description: Covers service-to-service contracts: choosing synchronous versus asynchronous boundaries, API and event versioning for compatibility, consumer-driven contracts with Pact, anti-corruption layers, bulkheads, circuit breakers, saga patterns for distributed transactions, and idempotency keys across services. Use when the user asks to split a service boundary, version an internal API, coordinate a multi-service transaction, add a circuit breaker or bulkhead, or test service contracts.
metadata:
  opencode/autoinvoke: true
---

# Microservice Contracts and Resilience

Splitting a monolith creates distributed transactions, partial failures, and contracts that must survive independent deployment. The tools that keep a distributed system coherent are: an explicit boundary, a versioned contract that evolves additively, a consumer-driven test proving the contract, an anti-corruption layer translating between models, and a compensating strategy for work that spans services.

## When to use this

- Deciding where to draw a service boundary, and whether a call should be sync or async.
- Evolving an internal API or event contract without breaking deployed consumers.
- Setting up consumer-driven contract testing so a producer cannot silently break consumers.
- Introducing an anti-corruption layer to isolate a legacy or third-party model.
- Adding bulkheads, circuit breakers, or timeouts so one failing dependency cannot cascade.
- Coordinating a workflow across services (saga) instead of a distributed transaction.
- Making cross-service operations idempotent with an idempotency key.

## Quick start

A consumer with bulkhead isolation, circuit breaking, deadline propagation, and idempotency keys:

```ts
import { createHash, randomUUID } from "node:crypto";

export type Order = { id: string; customerId: string; totalCents: number; status: string };

// --- Bulkhead: bound concurrency per dependency --------------------------
class Bulkhead {
  private active = 0;
  private queue: Array<() => void> = [];

  constructor(private readonly capacity: number, private readonly maxQueue: number) {}

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.active >= this.capacity) {
      if (this.queue.length >= this.maxQueue) {
        // Fail fast rather than queue without bound.
        throw new Error("bulkhead saturated");
      }
      await new Promise<void>((resolve) => this.queue.push(resolve));
    }
    this.active += 1;
    try {
      return await fn();
    } finally {
      const next = this.queue.shift();
      if (next) next();
      else this.active -= 1;
    }
  }
}

// --- Circuit breaker -----------------------------------------------------
type BreakerState = "closed" | "open" | "half_open";

class CircuitBreaker {
  private state: BreakerState = "closed";
  private failures = 0;
  private openedAt = 0;

  constructor(
    private readonly failureThreshold: number,
    private readonly cooldownMs: number,
    private readonly halfOpenProbes = 1,
  ) {}

  private canAttempt(): boolean {
    if (this.state === "closed") return true;
    if (this.state === "open") {
      if (Date.now() - this.openedAt >= this.cooldownMs) {
        this.state = "half_open";
        return true;
      }
      return false;
    }
    return true; // half_open: allow probes
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (!this.canAttempt()) throw new Error("circuit open");
    try {
      const result = await fn();
      this.state = "closed";
      this.failures = 0;
      return result;
    } catch (err) {
      this.failures += 1;
      if (this.state === "half_open" || this.failures >= this.failureThreshold) {
        this.state = "open";
        this.openedAt = Date.now();
      }
      throw err;
    }
  }
}

// --- Client --------------------------------------------------------------
export class PaymentsClient {
  private readonly bulkhead = new Bulkhead({ capacity: 20, maxQueue: 50 });
  private readonly breaker = new CircuitBreaker({ failureThreshold: 10, cooldownMs: 5_000 });

  constructor(
    private readonly baseUrl: string,
    private readonly deadlineMs: number,
  ) {}

  async charge(input: { orderId: string; amountCents: number }, deadlineMs?: number): Promise<{ chargeId: string }> {
    // Propagate a fraction of our own budget; never exceed it.
    const budget = Math.min(deadlineMs ?? this.deadlineMs, this.deadlineMs);
    // Stable key per logical operation: retries reuse it, distinct charges do not.
    const idempotencyKey = createHash("sha256")
      .update(`${input.orderId}:${input.amountCents}`)
      .digest("hex");

    return this.bulkhead.run(() =>
      this.breaker.run(async () => {
        const res = await fetch(`${this.baseUrl}/charges`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "idempotency-key": idempotencyKey,
          },
          body: JSON.stringify(input),
          signal: AbortSignal.timeout(budget * 0.8),
        });
        if (res.status === 503) throw new Error("payments unavailable");
        if (!res.ok) throw new Error(`payments error ${res.status}`);
        return (await res.json()) as { chargeId: string };
      }),
    );
  }
}
```

The pattern in that file is the one to reuse: every outbound call passes through a bulkhead (bounded concurrency) and a circuit breaker (bounded failure propagation), carries a deadline that is a fraction of the caller's remaining budget, and carries a stable idempotency key so retries are safe.

## Sync versus async boundaries

| | Synchronous (REST/gRPC) | Asynchronous (events/messages) |
| --- | --- | --- |
| Coupling | caller knows callee exists and is up | caller does not know consumers |
| Availability | caller's availability is bounded by callee's | decoupled; consumers catch up later |
| Failure mode | synchronous error propagation | requires durable inbox/outbox |
| Latency | adds callee latency to caller | caller returns immediately |
| Data ownership | caller may query callee | data replicated to consumers |
| Use for | queries needing immediate answers, validation | state propagation, notifications, fan-out, long work |

Default to async for state propagation. A synchronous call as an "event" is a distributed transaction wearing a disguise: the caller blocks, learns about downstream failures at the worst moment, and couples its availability to a service it does not control.

Keep synchronous calls for reads where the caller needs the answer to proceed, and for validations where proceeding without the answer would be wrong. Use async events for "this happened" facts.

## Contract evolution

A contract is a promise to consumers you do not deploy. Additive change is the rule.

Backward-compatible changes (safe to deploy producer-first):

- Adding an optional field to a request or response.
- Adding a new endpoint or event type.
- Adding an enum value to a response enum, if consumers are documented to ignore unknown values.
- Relaxing validation on a request.
- Adding an optional query parameter.

Breaking changes (require a new version or coordinated migration):

- Removing or renaming a field or endpoint.
- Changing a field's type, or required to optional.
- Making an optional request field required.
- Removing an enum value.
- Changing the meaning of an existing field (same name, new semantics). This is the worst one: it compiles, passes schema diffing, and silently corrupts data.
- Tightening response validation that consumers depend on.

Strategy choices:

| Strategy | Mechanism | Cost |
| --- | --- | --- |
| Additive only + v1 forever | Never break; add fields, keep old ones | Schema grows; eventually unwieldy |
| Parallel version | `/v2/...` or new event types; dual-publish, migrate consumers, retire v1 | Two versions live during migration |
| Consumer-driven contract | Contract test fails CI if producer changes incompatibly | Requires provider-broker in CI |
| Database-per-service + events | Consumers read their own store | Eventual consistency, harder debugging |

For events, the rule is stricter than for APIs: you cannot force consumers to redeploy. Only add fields, never remove or retype, and consumers must tolerate unknown fields. If a field's meaning changes, add a new field with a new name.

A semantic change to an existing field is the failure that schema tooling cannot catch. Guard it with a contract test that asserts specific values, not just shapes.

## Consumer-driven contracts

A provider publishes a contract; consumers write expectations against it; CI verifies the provider still satisfies them. This turns "we broke a consumer" from a production incident into a failed build.

Pact is the established tool for this. In the consumer's test you declare the interactions you rely on; Pact generates a contract file; in the provider's CI you verify the pact against the real provider.

```ts
// Consumer-side test (using the Pact JS DSL, api-style).
import { PactV3 } from "@pact-foundation/pact";

const pact = new PactV3({ consumer: "orders", provider: "inventory" });

pact.given("inventory has 3 units of SKU-1")
  .uponReceiving("a stock check for SKU-1")
  .withRequest({
    method: "GET",
    path: "/inventory/SKU-1",
    headers: { accept: "application/json" },
  })
  .willRespondWith({
    status: 200,
    headers: { "content-type": "application/json" },
    body: { sku: "SKU-1", available: 3, reserved: 0 },
  });

// Write the contract and run it as part of the consumer's test suite.
```

```bash
# In the provider's CI, verify the contract against the running provider.
# Exact flags can vary by version; check `pact-broker --help` or the Pact CLI docs.
pact-broker can-i-deploy --pacticipant orders --version "$(git rev-parse --short HEAD)" --to-environment=test

# Optional: point a provider build at a tagged pact version.
pact-broker publish-verification-results --provider-version "$(git rev-parse --short HEAD)"
```

Design rules for contracts:

- Consumer-driven, so the consumer defines what it actually uses, not what the provider thinks.
- Verify in both directions: the provider must satisfy all consumer contracts before deploying, and a consumer should verify before it deploys too (via can-i-deploy).
- Contracts must be versioned per consumer and per environment; never verify against "latest".
- Contracts catch shape and status-code breaks. They do not catch semantic changes; add explicit assertion tests for behavior you depend on.

## Anti-corruption layers

An anti-corruption layer (ACL) is a translation service that keeps an external or legacy model from leaking into your domain.

```
Legacy CRM model          Your domain model
Customer { fname, lname, status_code: 7 }
                      -->  Customer { fullName, tier: "gold", active: true }
```

The layer is worth adding when:

- A third-party API or legacy system will change on its own schedule.
- You do not want a vendor's vocabulary in your codebase.
- You are strangling a monolith: the ACL lets new code speak your model while the old system keeps running.

The ACL is where you absorb changes in the external model. When the vendor changes `status_code` semantics next quarter, you change the translator, not every service that consumes the CRM.

Rules:

- The ACL exposes your domain types, never the external ones.
- Keep the translation logic in one place; scattering it across consumers defeats the purpose.
- The ACL may call the external system synchronously, but your services should depend on your model, not on the ACL's availability at request time. Where possible, replicate external data into your own store via events and treat the external system as the source of truth only for writes.
- Do not let the external model's identifiers become your primary keys; store a mapping.

## Resilience patterns

**Bulkhead.** Isolate pools (threads, connections, memory) per dependency so one slow or failing dependency cannot consume all resources and starve unrelated calls. The value of a bulkhead is that failure in dependency A does not become failure in B. Implement it as bounded concurrency plus a bounded queue, with fail-fast when both are full. Fail fast is essential: an unbounded queue converts an overload into unbounded latency, which is an outage with extra steps.

**Circuit breaker.** After a failure threshold, stop calling a failing dependency for a cooldown window, then probe with a small number of requests. Open circuit rejects immediately (fail fast, return a fallback or an error); half-open allows probes to test recovery; closed passes traffic normally. This prevents retry storms against a struggling dependency and gives it room to recover.

**Timeout.** Every call has a deadline, always shorter than the caller's own deadline. A timeout without a deadline on the caller's side means a hung dependency hangs the caller forever. Propagate deadlines across services so the whole chain respects the original budget.

**Retry with backoff and jitter.** Retry only transient failures, only idempotent operations, and only a bounded number of times. See the background-jobs skill for the backoff mechanics; the same rules apply to synchronous calls. Retrying inside a circuit breaker defeats the breaker: the circuit exists to stop calling a sick dependency.

**Load shedding.** When a service is overloaded, reject some requests deliberately (a `429` or a fast fallback) so the remaining requests get acceptable latency. Prefer shedding optional and background work first.

**Fallback and graceful degradation.** Define what the service does when a dependency is down: cached last-known-good data, a reduced response, a queued write for later, or a clear error. Choose deliberately per dependency; the default of "return a 500" is usually the worst option.

## Saga pattern

A distributed transaction across services without two-phase commit uses a saga: a sequence of local transactions, each with a compensating action.

Two flavors:

| | Orchestration | Choreography |
| --- | --- | --- |
| Control flow | A central saga coordinator issues commands and awaits replies | Each service reacts to events; no central coordinator |
| Coupling | Coordinator knows all steps | Services know only their own reaction |
| Debugging | easier: single flow to trace | harder: trace across event flows |
| Scalability | coordinator is a single point to scale | scales naturally |
| Use for | multi-step, ordered workflows with branching | simple fan-out reactions |

Use choreography for simple, stable flows (three services reacting to an event). Use orchestration when the workflow has branching, compensations, or is hard to follow.

```ts
// Orchestrated saga for "place order".
type SagaState = {
  orderId: string;
  steps: string[];
  compensations: Array<() => Promise<void>>;
};

export async function placeOrder(orderId: string): Promise<void> {
  const state: SagaState = { orderId, steps: [], compensations: [] };

  try {
    // Step 1: reserve inventory.
    const reservation = await inventory.reserve(orderId);
    state.steps.push("inventory.reserved");
    state.compensations.push(() => inventory.release(reservation.id));

    // Step 2: charge payment.
    const charge = await payments.charge(orderId);
    state.steps.push("payment.charged");
    state.compensations.push(() => payments.refund(charge.id));

    // Step 3: create shipment.
    await shipping.create(orderId);
    state.steps.push("shipment.created");

    await sagas.markCompleted(orderId);
  } catch (err) {
    // Run compensations in reverse order, best effort, then mark failed.
    for (const compensate of state.compensations.reverse()) {
      try {
        await compensate();
      } catch (e) {
        // A failed compensation is itself a saga needing intervention.
        await sagas.recordCompensationFailure(orderId, e);
      }
    }
    await sagas.markFailed(orderId, String(err));
    throw err;
  }
}
```

Rules that make sagas workable:

- **Every forward step needs an idempotent compensating action** (refund, release, cancel). Compensations are themselves retried and must be safe to run more than once.
- **Compensations run in reverse order**, like an undo stack.
- **Compensation is not rollback.** A refund does not un-send an email. Compensations are business actions that approximate undo; design them knowing the side effects already happened.
- **Persist saga state.** If the process restarts mid-saga, it must resume from the last completed step, not restart from the beginning (which double-charges). Persist after each step, before the next begins.
- **Use the outbox pattern** to publish step events atomically with the state change, so a committed step always emits its event.
- **A failed compensation escalates.** Log it, alert it, and give an operator a way to finish or accept it. Silently ignoring a failed compensation leaves money or inventory in limbo.
- **Prefer avoiding sagas** when a single-service transaction or an eventually-consistent async workflow suffices. A saga is a lot of machinery; do not build one for two steps.

## Outbox and inbox

The dual-write problem: writing to your database and publishing an event are two operations and cannot be atomic without distributed transactions. The outbox pattern solves it by making the event write part of the local transaction and publishing separately.

```ts
// In the same transaction as the business write:
await db.transaction(async (tx) => {
  await tx.insert(orders).values(order);
  await tx.insert(outbox).values({
    id: randomUUID(),
    topic: "order.placed",
    payload: JSON.stringify({ orderId: order.id }),
    createdAt: new Date(),
    publishedAt: null,
  });
});

// A separate relay polls the outbox and publishes, marking rows published.
```

The inbox pattern is the receiver's dedupe: insert the event id with a unique constraint in the same transaction as processing. Together they give at-least-once transport with effectively-once processing. See the webhooks skill for the receiver side and the background-jobs skill for the relay.

## Checklist

- [ ] Every service boundary has an explicit owner and a documented contract (API schema or event schema).
- [ ] Contracts are versioned and evolve additively; breaking changes go through a new version or coordinated migration.
- [ ] Consumers tolerate unknown fields and unknown enum values in events.
- [ ] Provider-side CI verifies consumer-driven contracts before deploy.
- [ ] Any third-party or legacy dependency is behind an anti-corruption layer, not translated ad hoc in every consumer.
- [ ] Every outbound call has a deadline shorter than the caller's own, propagated downstream.
- [ ] Every outbound dependency has a bulkhead with bounded concurrency and a bounded queue that fails fast when full.
- [ ] Every outbound dependency has a circuit breaker.
- [ ] Retries are bounded, jittered, idempotent, and suppressed while the breaker is open.
- [ ] Load shedding exists for overload; optional work is shed first.
- [ ] A documented fallback exists for each critical dependency's failure mode.
- [ ] Any cross-service transaction uses a persisted saga with reverse-order, idempotent compensations.
- [ ] Saga state is persisted after each step; failed compensations are alerted and replayable.
- [ ] Events published alongside state changes use the outbox pattern.
- [ ] Event consumers dedupe with an inbox unique constraint.

## Common mistakes

- **Synchronous call chains for what should be events.** Symptom: checkout latency equals the sum of every downstream service, and one slow dependency makes every caller slow. Fix: publish events for state propagation; keep sync only for reads and validations that must complete.
- **No bulkhead.** Symptom: one slow dependency exhausts the connection pool or thread pool, and unrelated endpoints start failing with pool timeouts. Fix: per-dependency bounded concurrency.
- **Retrying inside a circuit breaker.** Symptom: the breaker never trips because every failure is retried, and the sick dependency receives multiplied load. Fix: circuit first, retry only for transient errors with the breaker open.
- **No deadline on the outbound call.** Symptom: a hung dependency holds the request thread until the client times out, and threads accumulate until the service is down. Fix: every call gets `AbortSignal.timeout` / a context deadline, shorter than the caller's.
- **Unbounded queue in the bulkhead.** Symptom: overload converts into unbounded latency; requests wait minutes and time out, still consuming resources. Fix: bounded queue with fail-fast.
- **Breaking event change.** Symptom: a field is removed or retyped, deployed consumers crash or silently read the wrong value, and you cannot force them to redeploy. Fix: additive-only events; new field names for new meaning.
- **Semantic change to a field with the same name.** Symptom: nothing fails; data is silently wrong in every consumer. Fix: change the name or version the payload; add value-level assertions in tests.
- **Saga without persisted state.** Symptom: a coordinator restart mid-saga replays completed steps, double-charging. Fix: persist after each step and resume.
- **Compensation that is not idempotent.** Symptom: a retried compensation refunds twice. Fix: idempotent compensating actions keyed by the original step.
- **Two writes without an outbox.** Symptom: the database commits but the event is never published (process crash between), and consumers never learn. Fix: outbox pattern.

## Anti-patterns

- **The "shared database" microservice.** Symptom: services share tables, so a schema change in one breaks deploys everywhere and the "boundary" is fictional. Fix: each service owns its data; others go through APIs or events.
- **A distributed transaction via two-phase commit across services.** Symptom: a coordinator is a single point of failure and availability drops to the product of participants'. Fix: sagas with compensations, or design to avoid the distributed transaction (make steps async and eventually consistent).
- **Treating a synchronous call as an event.** Symptom: the caller blocks on a downstream that may be down, coupling availability for no reason. Fix: publish an event; the caller's job is done.
- **A circuit breaker that never opens because errors are swallowed upstream.** Symptom: requests fail slowly instead of failing fast, and the breaker provides no protection. Fix: do not catch-and-return-nil in the client layer; let failures reach the breaker.
- **Versioning internal APIs by URL path with no consumer inventory.** Symptom: `v2` exists but nobody migrated from `v1`, and both must be maintained indefinitely. Fix: consumer-driven contracts give you the inventory; retire versions based on that data.
- **Building a saga for a workflow that a single transaction or an async fan-out could handle.** Symptom: a large amount of orchestration machinery for two trivial steps. Fix: prefer a local transaction plus events; escalate to a saga only when real compensation is needed.
- **Retrying all errors uniformly.** Symptom: validation errors and 400s retried, amplifying load and delaying the failure signal. Fix: classify errors, retry only transient ones.

## References

- Martin Fowler, Microservices (boundaries, sagas, circuit breakers) — https://martinfowler.com/articles/microservices.html
- Martin Fowler, Circuit Breaker — https://martinfowler.com/bliki/CircuitBreaker.html
- Microsoft Azure Architecture Center, Circuit Breaker pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
- Microsoft Azure Architecture Center, Bulkhead pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/bulkhead
- Chris Richardson, Saga pattern — https://microservices.io/patterns/data/saga.html
- Chris Richardson, Circuit Breaker pattern — https://microservices.io/patterns/reliability/circuit-breaker.html
- Martin Fowler, Consumer-Driven Contracts — https://martinfowler.com/articles/consumerDrivenContracts.html
- Pact documentation — https://docs.pact.io/
- Google SRE Book, addressing cascading failures — https://sre.google/sre-book/addressing-cascading-failures/
- AWS Builders' Library, timeouts, retries, and backoff with jitter — https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- AWS Builders' Library, avoiding insurmountable queue backlogs — https://aws.amazon.com/builders-library/avoiding-insurmountable-queue-backlogs/
- Pact JS (contract testing for Node) — https://github.com/pact-foundation/pact-js
- CloudEvents specification (event envelope) — https://github.com/cloudevents/spec
- Idempotency-Key HTTP Header (IETF draft) — https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/
- Google API Improvement Proposals (resource-oriented naming) — https://google.aip.dev/
- OWASP Microservices Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Microservices_Security_Cheat_Sheet.html