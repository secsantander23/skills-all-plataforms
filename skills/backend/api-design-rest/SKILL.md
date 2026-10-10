---
name: REST API Design and Review
description: Covers resource modeling, HTTP method and status code semantics, caching headers, content negotiation, cursor versus offset pagination, idempotency keys, RFC 9457 error envelopes, API versioning, and OpenAPI documentation. Use when the user asks to design or review a REST API, choose status codes, add pagination to a list endpoint, make a POST retry-safe, implement ETags or Cache-Control, define an error response shape, version an API, or write an OpenAPI document.
metadata:
  opencode/autoinvoke: true
---

# REST API Design and Review

REST is a constraint system, not a naming style. The API is correct when a generic HTTP client, proxy, cache, and retry policy can treat it correctly without knowing anything about your domain. Model resources first, then map them onto methods and status codes, and let HTTP's own semantics carry caching, idempotency, and concurrency.

## When to use this

- Designing a new HTTP API, or reviewing one someone else designed.
- Choosing between status codes, or deciding whether an operation is a sub-resource, a field, or an action.
- Adding list pagination and deciding between offset and cursor.
- Making `POST` safe to retry, or implementing an `Idempotency-Key` handler.
- Setting `Cache-Control`, `ETag`, `Last-Modified`, or `Vary`.
- Choosing between a JSON error body, a problem-details body, or per-endpoint shapes.
- Picking a versioning strategy, or deciding when a change is backward compatible.
- Writing or linting an OpenAPI (Swagger) document.

## Quick start

A minimal Express service that gets resource modeling, status codes, conditional requests, and a problem-details error envelope right:

```ts
import express, { type Request, type Response, type NextFunction } from "express";
import { createHash } from "node:crypto";
import { z } from "zod";

const app = express();
app.use(express.json({ limit: "256kb" }));

// --- Domain layer -------------------------------------------------------
type Order = {
  id: string;
  customerId: string;
  status: "pending" | "paid" | "shipped";
  totalCents: number;
  version: number;
};

const orders = new Map<string, Order>();
let seq = 0;

const CreateOrder = z.object({
  customerId: z.string().min(1),
  items: z
    .array(z.object({ sku: z.string().min(1), qty: z.number().int().min(1).max(99) }))
    .min(1)
    .max(100),
});

// --- RFC 9457 problem details -------------------------------------------
type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  errors?: Array<{ pointer: string; message: string }>;
};

function problem(
  res: Response,
  status: number,
  title: string,
  extra: Partial<Problem> = {},
): void {
  res
    .status(status)
    .type("application/problem+json")
    .json({ type: `https://errors.example.com/${slug(title)}`, title, status, ...extra });
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function weakEtag(order: Order): string {
  return `W/"${order.id}-${order.version}"`;
}

// --- Routes -------------------------------------------------------------
app.get("/orders/:id", (req: Request, res: Response, next: NextFunction) => {
  const order = orders.get(req.params.id);
  if (!order) return problem(res, 404, "Order not found", { detail: `No order ${req.params.id}` });

  const etag = weakEtag(order);
  res.setHeader("ETag", etag);
  res.setHeader("Cache-Control", "private, max-age=0, must-revalidate");

  // Conditional GET: 304 with no body if the client already has this version.
  if (req.headers["if-none-match"] === etag) return res.status(304).end();

  res.json(order);
});

app.post("/orders", (req: Request, res: Response) => {
  const parsed = CreateOrder.safeParse(req.body);
  if (!parsed.success) {
    return problem(res, 400, "Invalid request body", {
      errors: parsed.error.issues.map((i) => ({
        pointer: i.path.join("/"),
        message: i.message,
      })),
    });
  }

  const order: Order = {
    id: `ord_${++seq}`,
    customerId: parsed.data.customerId,
    status: "pending",
    totalCents: 0,
    version: 1,
  };
  orders.set(order.id, order);

  res.location(`/orders/${order.id}`)
    .status(201)
    .setHeader("ETag", weakEtag(order))
    .json(order);
});

app.delete("/orders/:id", (req: Request, res: Response) => {
  if (!orders.delete(req.params.id)) return problem(res, 404, "Order not found");
  res.status(204).end();
});

app.use((_req: Request, res: Response) => problem(res, 500, "Internal server error"));

app.listen(8080, () => console.log("listening on :8080"));
```

## Resource modeling

Model nouns that a client can name. An operation that is not expressible as CRUD on a noun becomes an explicit sub-resource or an action resource.

| Instead of | Model it as |
| --- | --- |
| `POST /createOrder` | `POST /orders` |
| `POST /updateOrder` | `PUT /orders/{id}` or `PATCH /orders/{id}` |
| `POST /deleteOrder` | `DELETE /orders/{id}` |
| `POST /orders/{id}/cancel` | `PUT /orders/{id}/status` with `{"status":"cancelled"}` |
| `GET /getOrderStatus?id=1` | `GET /orders/{id}` with a field, or `GET /orders/{id}/status` |

Rules that keep a resource model navigable:

- Plural, lowercase, kebab-case collection names: `/purchase-orders`.
- One path segment per resource level. Do not encode relations in flat keys like `/customer123orders`.
- Identifiers in the path, filters in the query string: `GET /orders?status=paid&customerId=c_9`.
- Nest only one or two levels. `GET /orders/{id}/items` is fine; `GET /customers/{id}/orders/{oid}/items/{iid}/taxes` is not, because it forces the client to know the hierarchy to fetch one item.
- When you nest, also expose the flat route. `GET /items/{itemId}` should not require the caller to know its parent.

### Actions

Some operations genuinely do not fit CRUD: `cancel`, `capture`, `publish`, `retry`. Model them as sub-resources with an imperative verb, and use `POST` on a collection:

```text
POST /orders/{orderId}/cancellations   -> 201, body {"reason": "customer_request"}
GET  /orders/{orderId}/cancellations   -> 200, list of cancellation records
```

The resulting resource is auditable and idempotent in a way that `POST /orders/{id}/cancel` is not: retrying `POST /orders/{id}/cancellations` returns the existing cancellation instead of double-cancelling.

## HTTP methods

`PUT`, `POST`, and `DELETE` are defined in terms of idempotency, and clients, proxies, and retry policies rely on that. Choosing the wrong one breaks behavior you cannot observe locally.

| Method | Safe | Idempotent | Has request body | Use for |
| --- | --- | --- | --- | --- |
| `GET` | yes | yes | no | Reading. Must never mutate. |
| `HEAD` | yes | yes | no | Fetching metadata only. Same headers as `GET`, no body. |
| `POST` | no | no | yes | Creating, non-idempotent actions, RPC-style calls. |
| `PUT` | no | yes | yes | Full replacement of a resource at a known URI. |
| `PATCH` | no | not guaranteed | yes | Partial update. Define the patch format explicitly. |
| `DELETE` | no | yes | optional | Removing. Repeated deletes should end in the same state. |

- `GET` must be safe. A `GET /orders/reset` that truncates a table breaks every crawler, prefetcher, and browser cache in the path.
- `DELETE` on a resource that is already deleted should return `404` or `204`, not `500`. Define one and be consistent.
- `PATCH` needs an explicit format. RFC 6902 JSON Patch (`application/json-patch+json`) and RFC 7396 JSON Merge Patch (`application/merge-patch+json`) are both standardized. If you invent your own partial-update semantics, document the merge rules.

```ts
// JSON Merge Patch (RFC 7396): null deletes, absent keys leave untouched.
app.patch("/orders/:id", express.json({ type: "application/merge-patch+json" }), (req, res) => {
  const order = orders.get(req.params.id);
  if (!order) return problem(res, 404, "Order not found");
  const current = { ...order };
  for (const [k, v] of Object.entries(req.body)) {
    if (v === null) delete (current as Record<string, unknown>)[k];
    else (current as Record<string, unknown>)[k] = v;
  }
  Object.assign(order, current, { version: order.version + 1 });
  res.setHeader("ETag", weakEtag(order)).json(order);
});
```

## Status codes

Pick codes by what actually happened, not by what the handler found easiest to write. Clients branch on status codes more than on bodies.

| Status | Meaning | Typical use |
| --- | --- | --- |
| `200 OK` | Success with a representation | Successful `GET`, `PUT`, `PATCH`. |
| `201 Created` | New resource created | Include a `Location` header pointing at the new URI. |
| `202 Accepted` | Accepted for async processing | Work queued; the client must poll a status resource. |
| `204 No Content` | Success, no body | Successful `DELETE`, or a `PUT` that returns nothing. |
| `304 Not Modified` | Cached copy still valid | Response to a matching `If-None-Match` or `If-Modified-Since`. |
| `400 Bad Request` | Malformed or unprocessable | JSON parse failure, schema validation failure. |
| `401 Unauthorized` | Not authenticated | Missing or invalid credentials. Include `WWW-Authenticate`. |
| `403 Forbidden` | Authenticated but not allowed | Authenticated fine, authorization denied. Do not use for auth failure. |
| `404 Not Found` | No such resource | Including resources the caller may not see. See below. |
| `405 Method Not Allowed` | Method unsupported here | Include an `Allow` header listing what is supported. |
| `406 Not Acceptable` | Cannot produce the requested media type | No representation matches the `Accept` header. |
| `409 Conflict` | Conflicts with current state | Version mismatch, duplicate unique key, concurrent update. |
| `410 Gone` | Deliberately removed | Resource existed and was deleted; distinguishes from "never existed". |
| `412 Precondition Failed` | `If-Match` / `If-Unmodified-Since` failed | The optimistic-concurrency check failed. |
| `415 Unsupported Media Type` | Body format not supported | Reject unknown `Content-Type` rather than guessing. |
| `422 Unprocessable Content` | Syntactically valid, semantically rejected | Well-formed JSON that fails business rules. |
| `428 Precondition Required` | Server requires a precondition | Server refuses unsafe requests lacking `If-Match` (RFC 6585). |
| `429 Too Many Requests` | Rate limited | Include `Retry-After`. |
| `500 Internal Server Error` | Server fault | Generic. Never leak internals in the body. |
| `502 Bad Gateway` | Upstream returned garbage | Proxy saw an invalid upstream response. |
| `503 Service Unavailable` | Temporarily down or overloaded | Include `Retry-After`. |
| `504 Gateway Timeout` | Upstream did not answer in time | Proxy-generated. |

Distinguish `401` from `403`: `401` means "I do not know who you are", `403` means "I know who you are and the answer is still no". Returning `403` for a missing token makes clients skip re-authentication; returning `401` for a genuine permission denial leaks the existence of the resource.

For object-level authorization, prefer `404` over `403` on resource routes the caller must not know about. `403` confirms the resource exists, which is an enumeration oracle. Reserve `403` for endpoints where existence is already public.

For asynchronous work, return `202 Accepted` plus a status resource:

```text
POST /reports            -> 202 Accepted, Location: /reports/rpt_8812
GET  /reports/rpt_8812   -> 200 {"status":"running"}
GET  /reports/rpt_8812   -> 200 {"status":"succeeded","downloadUrl":"..."}
```

`201` promises the thing exists now and you can interact with it. If it takes forty seconds to build, `201` forces every client to implement a timeout.

## Caching

HTTP caching is the cheapest performance work available and it is entirely declarative. If you do not set caching headers, intermediaries must apply conservative defaults and clients will not cache at all.

| Header | Purpose |
| --- | --- |
| `Cache-Control` | The main directive. `max-age`, `s-maxage`, `public`, `private`, `no-store`, `no-cache`, `must-revalidate`, `immutable`. |
| `ETag` | Opaque validator for a specific representation. Strong or weak (`W/"..."`). |
| `Last-Modified` | Coarse validator. One-second resolution; weak by nature. |
| `Vary` | Which request headers affect the response. `Vary: Accept-Encoding`, `Vary: Authorization`. |
| `Age` | Set by caches; how long it has been cached. |
| `Retry-After` | With `429` or `503`. Seconds or an HTTP date. |

Decision guide:

```text
Static, content-addressed assets  ->  Cache-Control: public, max-age=31536000, immutable
Authenticated read                ->  Cache-Control: private, max-age=0, must-revalidate  + ETag
Public read, slow to change       ->  Cache-Control: public, max-age=60, stale-while-revalidate=300
Contains PII, never persist      ->  Cache-Control: no-store
```

`private` and `no-store` are different. `private` allows a browser cache to hold the response but forbids shared caches. `no-store` forbids writing it anywhere, and is what you want on responses containing PII or on authenticated responses that could otherwise sit on a shared proxy.

Implement `GET` with a validator so that repeat reads cost `304`:

```ts
app.get("/orders", (req: Request, res: Response) => {
  const status = String(req.query.status ?? "pending");
  const body = JSON.stringify({ status, orders: [...orders.values()].filter(o => o.status === status) });
  const etag = `W/"${createHash("sha256").update(body).digest("base64url")}"`;

  res.setHeader("ETag", etag);
  res.setHeader("Cache-Control", "private, max-age=0, must-revalidate");
  res.setHeader("Vary", "Accept-Encoding");

  if (req.headers["if-none-match"] === etag) return res.status(304).end();
  res.type("application/json").send(body);
});
```

For writes, guard with `If-Match` so a lost update becomes `412` instead of silent corruption:

```ts
app.put("/orders/:id", (req: Request, res: Response) => {
  const order = orders.get(req.params.id);
  if (!order) return problem(res, 404, "Order not found");

  const ifMatch = req.headers["if-match"];
  if (!ifMatch) return problem(res, 428, "Precondition required", {
    detail: "Send If-Match with the ETag from a prior GET.",
  });
  if (ifMatch !== weakEtag(order)) return problem(res, 412, "Precondition failed");

  order.status = req.body.status ?? order.status;
  order.version += 1;
  res.setHeader("ETag", weakEtag(order)).json(order);
});
```

Note `428` here: the server is refusing to accept an unsafe request that lacks a precondition. That is a deliberate hardening choice, not the default for every API, so document it.

## Content negotiation

Send `Vary` whenever the response depends on a request header, or shared caches will serve one variant to everyone.

```text
Accept: application/json                  -> default JSON
Accept: application/problem+json           -> errors only
Accept-Language: en-GB                    -> pick locale
Accept-Encoding: gzip, br                 -> pick encoding (always Vary on this if dynamic)
```

Return `406 Not Acceptable` when no registered representation matches, rather than silently returning the default. Return `415 Unsupported Media Type` when the request `Content-Type` is not one you parse.

Prefer JSON for nearly everything. YAML is indentation-fragile and unquoted keys silently become non-strings; CBOR and MessagePack are worth it only when profiling shows serialization is a real cost.

## Error envelope

Standardize on RFC 9457 `application/problem+json`. One shape, one media type, machine-readable. Field errors go in `errors` with a JSON Pointer into the submitted document.

```json
{
  "type": "https://errors.example.com/validation-failed",
  "title": "Validation failed",
  "status": 400,
  "detail": "3 fields failed validation.",
  "instance": "/orders",
  "errors": [
    { "pointer": "/items/0/qty", "message": "Expected integer, received string" },
    { "pointer": "/customerId", "message": "Required" }
  ],
  "traceId": "01J8Z4QK7S2M"
}
```

Rules that make the envelope useful:

- `status` in the body must match the HTTP status. Two sources of truth drift.
- `type` is a stable URI. Clients branch on it, not on `title`, which is human-readable and may be reworded.
- `detail` is for humans and may change. `type` is for machines and may not.
- Add a `traceId`. A support ticket containing only "422" is unroutable.
- Never put stack traces, SQL fragments, or internal hostnames in a `5xx` body. Log them with the `traceId`.

Never place a `5xx` error in an HTTP `200` with an `"error"` field. That defeats every retry policy, cache, and monitoring probe in the ecosystem, because the transport layer no longer carries the outcome.

## Pagination

Offset pagination is easy and wrong under concurrent writes; cursor pagination is right and slightly harder. Choose per endpoint based on the requirements.

| | Offset (`?limit=50&offset=100`) | Cursor (`?limit=50&after=<token>`) |
| --- | --- | --- |
| Jump to page N | yes | no |
| Stable under inserts/deletes | no | yes |
| Deep offsets | degrade (`OFFSET 100000` scans) | constant cost |
| Index-friendly | only with keyset rewrite | yes |
| Cacheable page URLs | yes | no |
| Simple for the client | yes | no |

Use offset for small, admin-only, non-changing collections where "jump to page" matters. Use cursor for anything user-facing that can change between requests.

```ts
import { createHash } from "node:crypto";

type Page = { data: Order[]; nextCursor: string | null };

function encodeCursor(o: Order): string {
  return Buffer.from(`${o.createdAt}|${o.id}`).toString("base64url");
}

function decodeCursor(cursor: string): { createdAt: string; id: string } {
  const raw = Buffer.from(cursor, "base64url").toString("utf8");
  const idx = raw.lastIndexOf("|");
  if (idx < 1) throw new Error("malformed cursor");
  return { createdAt: raw.slice(0, idx), id: raw.slice(idx + 1) };
}

const ListQuery = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(25),
  after: z.string().optional(),
});

app.get("/orders", async (req: Request, res: Response) => {
  const q = ListQuery.parse(req.query);

  // Keyset predicate: (created_at, id) < (cursor.createdAt, cursor.id).
  // Build this with a bound parameter, never string concatenation.
  let rows: Order[];
  if (q.after) {
    const c = decodeCursor(q.after);
    rows = await db
      .select()
      .from("orders")
      .where(and(lte(orders.createdAt, c.createdAt), lt(orders.id, c.id)))
      .orderBy(desc(orders.createdAt), desc(orders.id))
      .limit(q.limit + 1);
  } else {
    rows = await db
      .select()
      .from("orders")
      .orderBy(desc(orders.createdAt), desc(orders.id))
      .limit(q.limit + 1);
  }

  const hasMore = rows.length > q.limit;
  const page: Page = {
    data: hasMore ? rows.slice(0, q.limit) : rows,
    nextCursor: hasMore ? encodeCursor(rows[q.limit - 1]) : null,
  };

  res.setHeader("Cache-Control", "private, max-age=0, must-revalidate");
  res.json(page);
});
```

Always expose `limit + 1` rows internally: the extra row tells you whether there is a next page without a second count query. Cap `limit` server-side; an unbounded `limit` is a denial-of-service vector.

For HATEOAS-style traversal, RFC 8288 `Link` headers work with any client:

```text
Link: <https://api.example.com/orders?after=eyJ...>; rel="next",
      <https://api.example.com/orders>; rel="first",
      <https://api.example.com/orders?before=eyJ...>; rel="prev"
```

Return a total count only when it is cheap and genuinely needed. `COUNT(*)` on a large filtered table is frequently the most expensive part of the request.

## Idempotency

`POST` is not idempotent, so a client that retries after a timeout can double-charge a card. `Idempotency-Key` (IETF draft `draft-ietf-httpapi-idempotency-key-header`) solves this: the client generates a unique key, the server stores the response against it, and replays the stored response on a repeat.

```ts
import { createHash, randomUUID } from "node:crypto";

type Stored = { requestHash: string; status: number; body: unknown; headers: Record<string, string> };
const idempotencyStore = new Map<string, Stored>();

app.post("/payments", async (req: Request, res: Response) => {
  const key = req.get("Idempotency-Key");
  if (!key) return problem(res, 400, "Idempotency-Key required");

  // Bind the key to the request body so a reused key with different input fails loudly.
  const requestHash = createHash("sha256").update(JSON.stringify(req.body)).digest("hex");
  const existing = idempotencyStore.get(key);

  if (existing) {
    if (existing.requestHash !== requestHash) {
      return problem(res, 422, "Idempotency key reused with different payload");
    }
    res.set(existing.headers).status(existing.status).json(existing.body);
    return;
  }

  const result = await chargeCustomer(req.body);

  const headers = { "Idempotency-Key": key, "Location": `/payments/${result.id}` };
  idempotencyStore.set(key, {
    requestHash,
    status: 201,
    body: result,
    headers,
  });

  res.set(headers).status(201).json(result);
});
```

Details that matter in production:

- Store the key with the response in the same transaction, or in a shared store with a TTL. An in-memory `Map` does not survive a restart and does not work across instances; use Redis, Postgres, or DynamoDB.
- Expire keys. Twenty-four hours is the Stripe default and a reasonable starting point; document yours.
- Reject a key reused with a different body. Otherwise a client bug replays the wrong charge forever.
- Return the original status code on replay, not `200`. The caller needs to know it already created something.

If the operation is naturally idempotent (`PUT /orders/{id}` with a client-supplied resource ID), prefer that over an idempotency key: it removes the extra state entirely.

## Versioning

| Strategy | How | Cost | Use when |
| --- | --- | --- | --- |
| No version, additive only | Add fields, never remove or retype | Lowest | Internal APIs you fully control |
| URI path | `/v2/orders` | Routing and caching noise | Most public APIs |
| Header | `Accept: application/vnd.acme.v2+json` | Poor browser/tooling support | Content negotiation-heavy APIs |
| Media type param | `?v=2` | Easy to forget, hard to enforce | Rarely a good choice |
| Date-based | `/2024-06/orders` | Predictable deprecation dates | APIs with scheduled breaking changes |

Whichever you pick, decide what counts as a breaking change. A reasonable default set:

- Removing or renaming a field.
- Changing a field's type, or making an optional field required.
- Adding an element to an enum that clients switch on exhaustively.
- Changing a default that alters observable behavior.
- Tightening validation on previously accepted input.
- Changing status codes for existing conditions.
- Removing an endpoint or changing its path.

Non-breaking: adding a new optional field, adding a new endpoint, adding a new enum value where clients are documented to tolerate unknowns, relaxing validation.

Enforce the no-breaking-change rule in CI by diffing the current OpenAPI document against the previous release and failing on breaking diffs. Tools exist for this; check the OpenAPI ecosystem for the one your pipeline already has rather than hand-rolling it.

Prefer additive change over versioning. `{"status": "paid"}` becoming `{"status": "paid", "paidAt": "..."}` is a non-event. `{"status": 2}` becoming `{"status": "PAID"}` needs `v2`.

## OpenAPI

OpenAPI is the contract and the documentation and the client generator input. Generate the document from the same definitions you validate against, then diff it in CI.

```yaml
openapi: 3.1.0
info:
  title: Orders API
  version: 1.4.0
  description: Create and inspect orders.
servers:
  - url: https://api.example.com
paths:
  /orders:
    get:
      operationId: listOrders
      summary: List orders
      parameters:
        - name: limit
          in: query
          schema: { type: integer, minimum: 1, maximum: 100, default: 25 }
        - name: after
          in: query
          description: Opaque cursor from a previous response's nextCursor.
          schema: { type: string }
        - name: Idempotency-Key
          in: header
          schema: { type: string, maxLength: 255 }
      responses:
        "200":
          description: A page of orders.
          headers:
            ETag: { schema: { type: string } }
            Cache-Control: { schema: { type: string } }
          content:
            application/json:
              schema:
                type: object
                required: [data, nextCursor]
                properties:
                  data:
                    type: array
                    items: { $ref: "#/components/schemas/Order" }
                  nextCursor: { type: [string, "null"] }
        "400":
          $ref: "#/components/responses/BadRequest"
    post:
      operationId: createOrder
      summary: Create an order
      parameters:
        - name: Idempotency-Key
          in: header
          required: true
          schema: { type: string, maxLength: 255 }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/CreateOrder" }
      responses:
        "201":
          description: Created.
          headers:
            Location: { schema: { type: string } }
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Order" }
        "409": { $ref: "#/components/responses/Conflict" }
components:
  schemas:
    Order:
      type: object
      required: [id, customerId, status, totalCents]
      properties:
        id: { type: string }
        customerId: { type: string }
        status: { type: string, enum: [pending, paid, shipped, cancelled] }
        totalCents: { type: integer, minimum: 0 }
        version: { type: integer, minimum: 1 }
    Problem:
      type: object
      required: [type, title, status]
      properties:
        type: { type: string, format: uri }
        title: { type: string }
        status: { type: integer }
        detail: { type: string }
        instance: { type: string }
        traceId: { type: string }
  responses:
    BadRequest:
      description: The request failed validation.
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    Conflict:
      description: The request conflicts with current state.
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
```

OpenAPI 3.1 is aligned with JSON Schema 2020-12, so `$ref` can point at any JSON Schema document and `type: ["string", "null"]` is valid (note the array form, which replaced `nullable: true` from 3.0). Give every operation an `operationId`: it becomes the generated client method name, and it is the only stable hook consumers have.

## Checklist

Review an API against these before calling it done:

- [ ] Every path is a noun or a sub-resource of a noun. No verbs in paths outside explicit action resources.
- [ ] `GET` and `HEAD` mutate nothing.
- [ ] Every `POST` that moves money or sends a message either accepts an `Idempotency-Key` or is naturally idempotent.
- [ ] Status codes distinguish `401` from `403`, and `404` from `410`.
- [ ] `201` responses include `Location`; `202` responses point at a pollable status resource.
- [ ] `429` and `503` include `Retry-After`.
- [ ] Every read defines a cache policy, and personalized reads use `private` or `no-store`.
- [ ] `Vary` is set for every request header that changes the response.
- [ ] Errors are RFC 9457 `application/problem+json` with a matching status, stable `type` URI, and `traceId`.
- [ ] No `5xx` body leaks internals.
- [ ] List endpoints cap `limit` server-side and use cursor pagination where data mutates.
- [ ] Writes that can race accept `If-Match` and return `412` on conflict.
- [ ] The OpenAPI document is generated from code, published, and diffed for breaking changes in CI.
- [ ] The versioning policy and its deprecation window are documented publicly.

## Common mistakes

- **`GET` that mutates.** A `GET /orders/export` endpoint that clears a flag breaks browser prefetch and every crawler. Symptom: state changes with no user action. Fix: `POST` with the side effect named as a sub-resource.
- **`403` returned for missing credentials.** Clients cannot distinguish "log in again" from "you are banned", so they never re-authenticate. Fix: `401` plus a `WWW-Authenticate` header; reserve `403` for authenticated-but-denied.
- **Errors hidden in a `200` envelope.** `{ "success": false, "error": "..." }` with status `200`. Symptom: monitoring shows a flat green dashboard during a total outage, because every probe gets `200`. Fix: use the status line, always.
- **`500` for a missing record.** Symptom: a typo in an ID produces an error page with a stack trace and a 500 that pollutes error budgets. Fix: `404` with a problem body, and log the miss at debug level, not error.
- **Deep offset pagination.** Symptom: `GET /orders?offset=500000` takes seconds and pins a CPU while the database scans and discards half a million rows. Fix: keyset cursor.
- **Idempotency key stored only in memory.** Symptom: duplicate charges reappear after a deploy or on a second instance. Fix: shared durable store with a TTL, written in the same transaction as the effect.
- **Unbounded `limit`.** Symptom: a single request for `limit=1000000` becomes a denial-of-service. Fix: schema-level maximum, enforced before the query runs.
- **Weak ETag compared with `===` on a list that varies by auth.** Symptom: one tenant receives another tenant's cached body from a shared proxy. Fix: `Vary` plus `private`, or include a per-tenant salt in the validator.
- **`Content-Type` guessed instead of checked.** Symptom: a client sends `text/plain` and the JSON parser produces a confusing `SyntaxError` at an unhelpful location. Fix: return `415` unless the media type is registered.
- **Breaking change shipped under the same version.** Symptom: a partner's client starts returning `null` for a field that changed type. Fix: diff the OpenAPI document in CI and fail on breaking changes.

## Anti-patterns

- **`/api/v1/users/{id}/orders/{oid}/items/{iid}/discounts/{did}/rules/{rid}` over-nesting.** The client must know the whole tree to fetch one rule. Expose `/rules/{rid}` directly.
- **RPC-over-HTTP**: `POST /api/getUserData` with a JSON-RPC body. This uses HTTP as a tunnel and gives up caching, conditional requests, and status-code semantics for free. If you need an RPC call, model it as a named sub-resource: `POST /users/{id}/deprovisioning-requests`.
- **Every endpoint returns `200` with a status field in the body.** Same failure mode as the error envelope anti-pattern: the transport stops carrying the outcome, so proxies, caches, retries, and dashboards all break.
- **Versioning in the query string only (`/orders?v=2`)** with no header or path component. It defeats HTTP caching keyed on the URL path alone and is trivially forgotten by clients, so you silently serve v1 responses to v2 callers.
- **Trailing-slash duality.** `/orders` and `/orders/` both being valid creates two cache entries for one resource and produces redirect chains that break non-GET methods. Pick one and redirect the other with `308`.
- **Returning `200` with an empty body for `DELETE`, and separately returning `404` for a second delete.** Choose `404` or idempotent `204`, then apply it uniformly. Two code paths means one of them is wrong somewhere.
- **`lastModified` as a cache validator on high-write resources.** Second-resolution timestamps collide, so two distinct versions share a validator and a stale response is served. Use `ETag` derived from content or a version counter.
- **Turning `PATCH` into a full update.** Treating `PATCH` as `PUT` surprises clients that read the docs. If you mean full replacement, use `PUT`.
- **Hiding `Retry-After` on `429`.** Clients that cannot learn when to retry either hammer immediately or give up. The header is the contract.

## References

- RFC 9110, HTTP Semantics — https://www.rfc-editor.org/rfc/rfc9110.html
- RFC 9111, HTTP Caching — https://www.rfc-editor.org/rfc/rfc9111.html
- RFC 9112, HTTP/1.1 — https://www.rfc-editor.org/rfc/rfc9112.html
- RFC 9457, Problem Details for HTTP APIs — https://www.rfc-editor.org/rfc/rfc9457.html
- RFC 6585, Additional HTTP Status Codes (428, 429, 431, 511) — https://www.rfc-editor.org/rfc/rfc6585.html
- RFC 8288, Web Linking (Link header) — https://www.rfc-editor.org/rfc/rfc8288.html
- RFC 6902, JSON Patch — https://www.rfc-editor.org/rfc/rfc6902.html
- RFC 7396, JSON Merge Patch — https://www.rfc-editor.org/rfc/rfc7396.html
- RFC 9110 §9.3.1 `Prefer` and RFC 7244, Prefer — https://www.rfc-editor.org/rfc/rfc7244.html
- Idempotency-Key HTTP Header (IETF draft) — https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/
- OpenAPI Specification (latest) — https://spec.openapis.org/oas/latest.html
- OpenAPI Specification 3.1.1 — https://spec.openapis.org/oas/v3.1.1.html
- JSON Schema 2020-12 — https://json-schema.org/draft/2020-12/json-schema-core.html
- OWASP REST Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- OWASP API Security Top 10 — https://owasp.org/API-Security/
- IETF HTTP API Field Names registry (naming shared header fields) — https://www.iana.org/assignments/http-fields/http-fields.xhtml