---
name: Webhooks and Event Delivery
description: Covers designing and receiving webhooks and events: HMAC signature verification with constant-time comparison, idempotent receivers, retry schedules with exponential backoff, event schema and envelope design, versioning, ordering guarantees, at-least-once semantics, dead letter handling, and the difference between outbound webhooks and inbound webhook-style events. Use when the user asks to send or receive webhooks, verify signatures, handle duplicate events, design an event schema, or debug failed webhook deliveries.
metadata:
  opencode/autoinvoke: true
---

# Webhooks and Event Delivery

A webhook is your system pushing an HTTP request at a URL you do not control. That inverts every assumption a normal API client gives you: you cannot see the response, the request may arrive five times, it may arrive out of order, and the endpoint is hostile until proven otherwise. Signature verification stops forgery; idempotency stops duplicates; a retry schedule and a dead-letter path stop silent data loss.

## When to use this

- Sending webhooks to customers and needing to reason about delivery guarantees.
- Receiving webhooks and verifying their authenticity safely.
- Choosing an event envelope, schema, and versioning strategy.
- Handling duplicate or out-of-order deliveries.
- Designing a retry schedule with backoff and a terminal failure state.
- Deciding between outbound webhooks, an inbound event gateway, and a message bus.
- Debugging "the customer says they never got the event".

## Quick start

A receiver that verifies signatures, deduplicates, and acknowledges fast:

```ts
import { createHmac, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

const SECRET = process.env.WEBHOOK_SECRET!;
const TOLERANCE_MS = 5 * 60 * 1000;

type ReceivedEvent = {
  id: string;
  type: string;
  created: string;
  data: Record<string, unknown>;
};

// Deduplicate by event id, durably. An in-memory Set loses on restart and
// does not work across instances.
declare const seenEvents: {
  // Returns true the first time it sees an id, false on every replay.
  rememberOnce(id: string, ttlSeconds: number): Promise<boolean>;
};

declare const handlers: {
  "invoice.paid": (data: Record<string, unknown>, event: ReceivedEvent) => Promise<void>;
  "invoice.failed": (data: Record<string, unknown>, event: ReceivedEvent) => Promise<void>;
};

function verifySignature(
  rawBody: Buffer,
  header: string | undefined,
  secret: string,
): boolean {
  if (!header) return false;
  // Format: "t=1755000000,v1=<hex-digest>"
  const parts = Object.fromEntries(
    header.split(",").map((p) => {
      const idx = p.indexOf("=");
      return [p.slice(0, idx).trim(), p.slice(idx + 1).trim()];
    }),
  );
  const timestamp = Number(parts.t);
  const provided = parts.v1;
  if (!Number.isFinite(timestamp) || !provided) return false;

  // Reject replays outside the tolerance window.
  if (Math.abs(Date.now() / 1000 - timestamp) > TOLERANCE_MS / 1000) return false;

  const expected = createHmac("sha256", secret)
    .update(`${timestamp}.`)
    .update(rawBody)
    .digest("hex");

  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(provided, "utf8");
  // timingSafeEqual throws on length mismatch, so check length first.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

const server = createServer((req, res) => {
  if (req.method !== "POST") {
    res.writeHead(405, { allow: "POST" });
    return res.end();
  }

  const chunks: Buffer[] = [];
  let size = 0;
  req.on("data", (c: Buffer) => {
    size += c.length;
    if (size > 1_000_000) {
      req.destroy(); // refuse oversized payloads
      return;
    }
    chunks.push(c);
  });

  req.on("end", () => {
    // Sign the RAW bytes, not a re-serialized object. Re-serialization changes
    // key order and whitespace and breaks every signature.
    const rawBody = Buffer.concat(chunks);
    const signature = req.headers["webhook-signature"] as string | undefined;

    if (!verifySignature(rawBody, signature, SECRET)) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "invalid signature" }));
    }

    let event: ReceivedEvent;
    try {
      event = JSON.parse(rawBody.toString("utf8")) as ReceivedEvent;
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "malformed payload" }));
    }

    // Dedupe: acknowledge a replay without reprocessing.
    void seenEvents.rememberOnce(event.id, 24 * 60 * 60).then(async (firstTime) => {
      if (!firstTime) {
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ received: true, duplicate: true }));
      }
      try {
        const handler = handlers[event.type];
        if (handler) await handler(event.data, event);
        // 2xx only after the work is durable.
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ received: true }));
      } catch (err) {
        // Return 5xx so the sender retries.
        console.error("webhook handler failed", event.id, err);
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "handler failed" }));
      }
    });
  });
});

server.listen(3000, () => console.log("webhook receiver on :3000"));
```

Three properties matter more than anything else in that receiver: sign raw bytes, compare signatures in constant time, and acknowledge only after the work is durable.

## Signature verification

Signatures prove the payload came from you (or from whoever holds the secret) and was not modified in transit. Always verify. Never authenticate a webhook by URL secrecy or by IP allowlist alone.

Common schemes, in decreasing order of preference:

| Scheme | Mechanism | Notes |
| --- | --- | --- |
| HMAC over `timestamp.body` | Your own scheme | Widely used; timestamp prevents replay; simple to implement |
| Standard Webhooks | `webhook-id`, `webhook-timestamp`, `webhook-signature` headers with a versioned scheme and `whsec_` prefixed secrets | A published spec intended to end bespoke signature formats. Check the site for the current spec and helper libraries. |
| Provider-specific | e.g., GitHub's `X-Hub-Signature-256` (`sha256=` + hex HMAC) or Stripe's `Stripe-Signature` | Follow the provider's documented format exactly. |

Rules:

- **Compare in constant time.** `===` and byte loops leak the value through timing. Use `crypto.timingSafeEqual` (Node), `hmac.compare_digest` (Python), `subtle.ConstantTimeCompare` / `hmac.Equal` (Go).
- **Sign the raw request body bytes.** Many JSON serializers reorder keys or change whitespace. If your framework parses the body before you can verify, capture the raw bytes; in many frameworks you must configure the framework to preserve them.
- **Include a timestamp in the signed message and enforce a tolerance window** (commonly a few minutes). Without this, a captured signature is valid forever.
- **Support secret rotation** by verifying against a set of active secrets, or by embedding a key id in the header. Rotating a single shared secret with no overlap means every webhook fails during the switch.
- **Use a different secret per endpoint or per tenant.** A single global secret means one leaked key forges events for everyone.
- **Never log the raw body without redacting.** Webhook payloads routinely contain personal data and occasionally secrets.

Python equivalent, for reference:

```python
import hashlib
import hmac
import time

WEBHOOK_SECRET = b"whsec_..."
TOLERANCE_SECONDS = 300


def verify_webhook_signature(payload: bytes, header: str, secret: bytes = WEBHOOK_SECRET) -> bool:
    """Verify a Standard Webhooks style header: webhook-id,t=<ts>,v1=<sig>."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if timestamp is None or signature is None:
        return False

    try:
        ts = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - ts) > TOLERANCE_SECONDS:
        return False  # too old or in the future

    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest()

    # compare_digest is constant time and length safe.
    return hmac.compare_digest(expected, signature)
```

## Delivery semantics

Webhook delivery is **at-least-once**. You cannot achieve exactly-once over HTTP, because the sender cannot distinguish "your server processed this and the response was lost" from "your server never saw this". The only correct response is: assume duplicates, make the receiver idempotent, and tell the sender to retry.

Guarantees to design for:

| Property | Reality | How to get it |
| --- | --- | --- |
| At-least-once | Default | Deduplicate on event id in the receiver |
| Ordering | Not guaranteed | Include a monotonic sequence or version per entity; make handlers order-independent |
| Exactly-once | Impossible | Achieve "effectively-once" with an idempotent receiver |
| Completeness | Only if the receiver returns 2xx | Retry until 2xx, then dead-letter |
| Expiry | Some senders expire events | Deliver promptly; provide a replay API |

Idempotent receivers come in three forms. Pick by how much state you can afford:

1. **Dedup key check.** Insert the event id into a table with a unique constraint. If the insert conflicts, you have already processed it; return 2xx and stop. This is the general answer.
2. **Idempotent state transition.** Make the effect naturally idempotent: `UPDATE invoices SET status='paid' WHERE id=? AND status<>'paid'` affects zero rows on a replay.
3. **Object versioning.** Store each event with a version and apply only strictly newer versions, discarding stale ones. This also solves out-of-order delivery.

```sql
-- Dedup with a unique constraint; the insert is the atomic claim.
INSERT INTO webhook_events (event_id, event_type, received_at, payload)
VALUES ($1, $2, now(), $3)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id;
-- Zero rows returned means we have seen this event before.
```

Acknowledge fast. If processing takes longer than the sender's timeout, the sender will retry and you have created your own duplicate. In handlers that are genuinely slow, verify the signature, persist the event to a queue or table, return 2xx immediately, and process asynchronously.

## Retry schedules

Retry with exponential backoff and jitter, over a window that spans the sender's and the receiver's realistic downtime. Stop after a bounded number of attempts, then dead-letter.

A common shape:

```ts
// attempt: 0-based attempt number
const SCHEDULE_MS = [
  0,          // immediate first delivery, then roughly
  30_000,     // 30s
  5 * 60_000, // 5m
  30 * 60_000,// 30m
  2 * 3_600_000,
  8 * 3_600_000,
  24 * 3_600_000,
];
```

Delivery rules:

- Retry only on network errors, timeouts, `429`, and `5xx`. Do not retry `4xx` other than `429`: a `400` will never succeed on a retry and retries amplify load.
- Honor `Retry-After` on `429` and `503`.
- Cap total attempts and total elapsed time, and record both when dead-lettering.
- Use a dead-letter queue with the original payload, the full attempt history, and the last error so operators can inspect and replay.
- Escalate or alert when a specific endpoint's success rate collapses; silently dead-lettering a customer's entire event stream is an outage you will not notice.

```ts
async function deliver(endpoint: WebhookEndpoint, event: Event): Promise<void> {
  const body = Buffer.from(JSON.stringify(event));
  const signature = signBody(body, endpoint.secret);

  for (const delay of SCHEDULE_MS) {
    if (delay > 0) await sleep(delay + Math.random() * 1000); // jitter

    try {
      const res = await fetch(endpoint.url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "webhook-id": event.id,
          "webhook-timestamp": String(Math.floor(Date.now() / 1000)),
          "webhook-signature": `v1=${signature}`,
          "user-agent": "Acme-Webhooks/1",
        },
        body,
        signal: AbortSignal.timeout(10_000),
      });

      if (res.ok) {
        await deliveries.markSuccess(endpoint.id, event.id);
        return;
      }
      // Give up immediately on permanent rejections.
      if (res.status >= 400 && res.status < 500 && res.status !== 429) {
        await deliveries.deadLetter(endpoint.id, event, `HTTP ${res.status}`, []);
        return;
      }
      const retryAfter = res.headers.get("retry-after");
      if (retryAfter) await sleep(Number(retryAfter) * 1000 || 0);
    } catch (err) {
      // network error or timeout: fall through and retry
      lastError = String(err);
    }
  }

  await deliveries.deadLetter(endpoint.id, event, lastError, SCHEDULE_MS);
}
```

## Event schema and versioning

An event envelope should carry everything a consumer needs to route, deduplicate, and order the event without guessing.

```json
{
  "id": "evt_01J8Z4QK7S2M9R3T",
  "type": "invoice.paid",
  "version": "2",
  "created": "2026-04-01T12:00:00Z",
  "producer": "billing-service",
  "tenant": "t_123",
  "sequence": 1041,
  "data": {
    "invoice_id": "inv_456",
    "amount_cents": 4999,
    "currency": "USD",
    "paid_at": "2026-04-01T11:59:58Z"
  }
}
```

Envelope fields and their jobs:

| Field | Purpose |
| --- | --- |
| `id` | Unique per delivery-logical event; the dedupe key for receivers |
| `type` | Routing key (dotted namespaced, past-tense facts: `invoice.paid`, not `invoice.update`) |
| `version` | Schema version of the `data` payload |
| `created` | When the fact occurred, not when it was sent |
| `producer` / `tenant` | Scoping and multi-tenant filtering |
| `sequence` | Optional monotonic per-entity version for ordering and stale rejection |
| `data` | The payload |

Use past-tense fact names (`invoice.paid`) over imperative commands (`pay_invoice`). Facts are descriptive and replayable; commands imply a specific handler and do not compose.

Versioning events is separate from versioning an API. Guidance:

- **Add fields, never remove them.** Receivers ignore unknown fields in practice, and you cannot control receiver code.
- **Do not change the type or meaning of an existing field.** If `amount_cents` becomes a decimal, add `amount` and keep `amount_cents` until consumers migrate.
- **Version the `data` payload separately from the envelope.** Bump `version` only for breaking changes to `data`.
- **Tolerate unknown fields in your own receivers.** A newer producer sending fields your consumer does not know must not break it.
- **Do not delete events from the log retroactively.** If an event was wrong, send a correcting event.

If your platform has a standard event format, use it. CloudEvents defines `id`, `source`, `type`, `specversion`, `time`, and `data` with typed `datacontenttype`, which maps closely onto the envelope above. Standard Webhooks defines the signature header format used above.

## Ordering

Global ordering across events is not achievable over HTTP delivery and rarely needed. What is achievable is **per-entity ordering** and **staleness rejection**.

- Include a per-entity monotonic `sequence` (or a version field you already have on the entity).
- In the receiver, compare the incoming sequence against the last applied one for that entity. Ignore anything not strictly newer. This makes the receiver correct under any delivery order and any duplication.
- If ordering matters enough to justify the complexity, move the stream to a partitioned log (Kafka, SQS FIFO with a message group id) which gives per-key ordering, and keep the receiver's stale check as defense in depth.

## Outbound versus inbound

| | Outbound webhook | Inbound event (message bus) | Polling |
| --- | --- | --- | --- |
| Receiver pulls | no | no | yes |
| Retry control | yours | broker | n/a |
| Receiver must expose a public endpoint | yes | no | no |
| Ordered per key | no (unless partitioned) | yes (partitioned) | yes |
| Best for | notifying external systems | internal service-to-service | consumers that cannot be connected |

Choose outbound webhooks when the consumer is external, cannot hold a long-lived connection, or does not want your infrastructure. Choose a message bus for internal service-to-eventing, where a shared broker gives you ordering, replay, and consumer offsets without public endpoints.

Inbound events (a provider pushing data into your system) follow the same receiver rules: verify, dedupe, acknowledge fast, dead-letter. If the provider supports both webhook and polling, prefer polling for large backfills and webhooks for freshness.

## Checklist

Receiving:

- [ ] Signature verified over the raw request bytes, before parsing or trusting any field.
- [ ] Signature compared with a constant-time function.
- [ ] Timestamp checked and outside-tolerance requests rejected.
- [ ] Multiple active secrets supported so rotation does not drop events.
- [ ] Per-endpoint (or per-tenant) secrets, not one global secret.
- [ ] Event id deduplicated with a durable store and a unique constraint.
- [ ] 2xx returned only after the work is durable, or the event queued durably first.
- [ ] Non-retryable `4xx` distinguished from retryable errors.
- [ ] Request body size limited before buffering.
- [ ] Payload redacted in logs.

Sending:

- [ ] Retry schedule with exponential backoff and jitter, over hours or days.
- [ ] `Retry-After` honored.
- [ ] Bounded attempts and a dead-letter store with the full attempt history.
- [ ] Alerting on sustained delivery failure for any endpoint.
- [ ] Endpoint health tracked per customer, not globally.
- [ ] Slow endpoints do not block the delivery pipeline for other endpoints.
- [ ] Replay tool for dead-lettered events.
- [ ] Event envelope carries `id`, `type`, `version`, `created`, `producer`, and `data`.

Design:

- [ ] Event types are past-tense facts with a stable namespace.
- [ ] Payloads are additive; nothing removed or retyped.
- [ ] Receivers tolerate unknown fields.
- [ ] Per-entity sequence present where ordering matters, with stale-event rejection.
- [ ] Schema documented and, ideally, machine-readable.

## Common mistakes

- **Verifying a re-serialized body.** Symptom: every signature fails for some recipients and passes for others, depending on their JSON serializer. Root cause: you hashed `JSON.stringify(req.body)` instead of the raw bytes. Fix: capture raw bytes and sign those.
- **Comparing signatures with `===`.** Symptom: rarely fails outright, but is exploitable via timing analysis to forge a signature byte by byte. Fix: `timingSafeEqual` / `compare_digest` / `hmac.Equal`.
- **No timestamp in the signed payload.** Symptom: a captured request can be replayed indefinitely. Fix: sign `timestamp.body` and reject outside a tolerance window.
- **Single shared secret for all endpoints.** Symptom: one leaked key forges events for every customer. Fix: per-endpoint secrets and rotation overlap.
- **Returning 5xx on handler failure after doing the work.** Symptom: sender retries, work runs twice. Fix: make the handler idempotent, or dedupe by event id before the work.
- **Dedupe in memory.** Symptom: after a deploy or on a second instance, replayed events reprocess. Fix: durable dedupe store keyed by event id.
- **Retrying all non-2xx including `400`.** Symptom: a permanently invalid payload is retried a dozen times, wasting capacity and never succeeding. Fix: treat `4xx` (except `429`) as terminal and dead-letter immediately.
- **Dropping events after the first failed delivery.** Symptom: a receiver restart of ten minutes loses every event in that window, permanently. Fix: retry across hours with jitter, then dead-letter, never drop silently.
- **Single retry schedule shared across all endpoints.** Symptom: one down endpoint consumes the entire delivery worker pool, delaying everyone. Fix: per-endpoint queues or bounded per-endpoint concurrency.
- **Events without ids.** Symptom: the receiver cannot deduplicate, so every retry is a duplicate effect. Fix: a globally unique event id, stable across retries of the same event.

## Anti-patterns

- **A shared secret per *tenant* stored in plaintext in the database with no rotation plan.** Symptom: a dump of the secrets table forges events for every tenant. Fix: encrypt at rest with a KMS-backed key, support overlapping rotation, scope blast radius.
- **Sending the full entity on every event** instead of a reference plus a version. Symptom: payloads grow unboundedly, leak fields that should not be shared, and couple the consumer to your internal model. Fix: send a reference and the smallest necessary snapshot, versioned.
- **"At-least-once is fine, our receiver is idempotent" with no dedupe key implemented.** Symptom: duplicate effects under retry because idempotency was assumed, not coded. Fix: make idempotency explicit (unique constraint or idempotent update), and test it by delivering the same event twice.
- **Verifying signatures only in staging** or only for one provider. Symptom: the production path skips verification because "we already validated the URL". Fix: verification is unconditional; fail closed.
- **Endpoints discovered by scanning all cloud provider IP ranges** as your auth mechanism. Symptom: an attacker in the same cloud enumerates and calls your endpoint; and legitimate traffic from a changed egress IP breaks. Fix: signature verification is the auth mechanism.
- **Retrying forever with no cap.** Symptom: a dead endpoint accumulates unbounded delivery attempts forever. Fix: bounded attempts, then dead-letter with alerting.
- **Requiring a synchronous business action in the receiver before responding.** Symptom: downstream latency exceeds the sender timeout, so the sender retries, doubling load and possibly duplicating work. Fix: acknowledge fast after enqueueing durably; process asynchronously.
- **A single event type like `data.changed` carrying the whole record and expecting consumers to branch.** Symptom: consumers re-implement your routing logic, and adding a field forces every consumer to update. Fix: specific typed events; the type is the routing contract.

## References

- Standard Webhooks specification and helper libraries — https://www.standardwebhooks.com/
- OWASP Webhook Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html
- GitHub, validating webhook deliveries (HMAC signature example) — https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- Stripe, webhooks (signature verification, retries, ordering caveats) — https://docs.stripe.com/webhooks
- Stripe, idempotent requests (keying retries) — https://docs.stripe.com/api/idempotent_requests
- CloudEvents specification — https://github.com/cloudevents/spec
- RFC 9421, HTTP Message Signatures — https://www.rfc-editor.org/rfc/rfc9421.html
- AWS Builders' Library, Timeouts, retries, and backoff with jitter — https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- AWS Builders' Library, Making retries safe with idempotent APIs — https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
- Google SRE Book, handling overload — https://sre.google/sre-book/handling-overload/
- Node.js crypto documentation (timingSafeEqual, createHmac) — https://nodejs.org/api/crypto.html
- Python hmac documentation (compare_digest) — https://docs.python.org/3/library/hmac.html
- Ruby HMAC — https://ruby-doc.org/current/classes/OpenSSL/HMAC.html