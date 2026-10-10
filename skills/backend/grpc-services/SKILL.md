---
name: gRPC and Protobuf Service Development
description: Covers writing .proto schemas, modeling services and RPC methods, implementing unary and all three streaming modes, mapping errors to gRPC status codes, setting deadlines and cancellation, using interceptors, and tuning performance with binary protobuf over JSON. Use when the user asks to define a protobuf schema, build a gRPC server or client, debug a stream, add deadlines, handle gRPC errors, or reduce gRPC latency.
metadata:
  opencode/autoinvoke: true
---

# gRPC and Protobuf Service Development

gRPC is a binary RPC contract: a `.proto` file is the source of truth, code is generated from it, and both sides must compile the same schema. Because the contract is code, changes are compiled rather than deployed, which makes breaking changes a build failure rather than a production incident, if you wire the checks in. Your remaining obligations are deadlines, cancellation, and honest status codes.

## When to use this

- Writing or reviewing a `.proto` file for a new or existing service.
- Choosing between unary and server-streaming, client-streaming, or bidirectional streaming.
- Fixing or adding streaming RPCs (backpressure, half-close, error propagation).
- Mapping application errors to gRPC status codes and attaching metadata/details.
- Adding deadlines, cancellation propagation, and deadline-aware downstream calls.
- Writing interceptors for auth, logging, retries, or tracing.
- Reducing latency or payload size (protobuf binary vs JSON, compression, keepalive).
- Setting up CI to detect breaking proto changes and lint style.

## Quick start

A `.proto` file, generated Python stubs, and a server implementing it with deadlines and status codes:

```proto
syntax = "proto3";

package catalog.v1;

import "google/protobuf/timestamp.proto";
import "google/protobuf/empty.proto";

option go_package = "github.com/acme/catalog/gen/catalog/v1;catalogv1";
option java_multiple_files = true;
option java_package = "com.acme.catalog.v1";

// Products is a read-mostly catalog service.
service Products {
  // GetProduct returns a single product. Unary.
  rpc GetProduct(GetProductRequest) returns (Product);

  // ListProducts streams products in pages-as-they-become-available.
  rpc ListProducts(ListProductsRequest) returns (stream Product);

  // ImportProducts accepts a stream of products and reports progress.
  rpc ImportProducts(stream ImportProductRequest) returns (ImportSummary);
}

message GetProductRequest {
  // id is the product identifier (UUIDv7 string form).
  string id = 1;
}

message ListProductsRequest {
  uint32 page_size = 1;
  // page_size is a hint; the server may return fewer per message.
  string page_token = 2;
  string category = 3;
}

message Product {
  string id = 1;
  string name = 2;
  int64 price_cents = 3;
  string currency = 4;
  repeated string tags = 5;
  ProductStatus status = 6;
  google.protobuf.Timestamp updated_at = 7;
}

enum ProductStatus {
  PRODUCT_STATUS_UNSPECIFIED = 0;
  PRODUCT_STATUS_ACTIVE = 1;
  PRODUCT_STATUS_DISCONTINUED = 2;
}

message ImportProductRequest {
  oneof payload {
    ImportProductMetadata metadata = 1;
    Product product = 2;
  }
}

message ImportProductMetadata {
  string source = 1;
  uint32 total_expected = 2;
}

message ImportSummary {
  uint32 imported_count = 1;
  uint32 failed_count = 2;
  repeated string failed_ids = 3;
}
```

Generating stubs and running the server (Python):

```bash
# Generate Python stubs into ./gen (adjust the output path to your layout).
python -m grpc_tools.protoc -I. --python_out=gen --grpc_python_out=gen ./catalog/v1/products.proto

# Go
protoc -I. --go_out=gen --go_opt=paths=source_relative \
       --go-grpc_out=gen --go-grpc_opt=paths=source_relative ./catalog/v1/products.proto
```

```python
# server.py
from __future__ import annotations

import logging
from concurrent import futures
from datetime import datetime, timezone

import grpc
from google.protobuf import empty_pb2, timestamp_pb2

import catalog.v1.products_pb2 as pb
import catalog.v1.products_pb2_grpc as pb_grpc

log = logging.getLogger("catalog")


def _ts(dt: datetime) -> timestamp_pb2.Timestamp:
    out = timestamp_pb2.Timestamp()
    out.FromDatetime(dt.astimezone(timezone.utc))
    return out


class ProductsServicer(pb_grpc.ProductsServicer):
    def __init__(self, store: dict[str, pb.Product]) -> None:
        self._store = store

    def GetProduct(self, request, context):
        if not request.id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "id is required")

        product = self._store.get(request.id)
        if product is None:
            # Not found is a client-correctable condition, not a server fault.
            context.abort(grpc.StatusCode.NOT_FOUND, f"product {request.id} not found")

        return product

    def ListProducts(self, request, context):
        # page_size is a hint, not a guarantee. Never exceed a server max.
        limit = min(request.page_size or 50, 200)
        token = request.page_token

        sent = 0
        for pid, product in self._store.items():
            if request.category and product.tags and request.category not in product.tags:
                continue
            if token and pid <= token:
                continue
            if context.is_active() is False:
                # Client cancelled or the deadline expired; stop promptly.
                return
            yield product
            sent += 1
            if sent >= limit:
                break

    def ImportProducts(self, request_iterator, context):
        seen_metadata = False
        imported = failed = 0
        failed_ids: list[str] = []

        for chunk in request_iterator:
            which = chunk.WhichOneof("payload")
            if which == "metadata":
                seen_metadata = True
                continue
            if which != "product":
                failed += 1
                continue
            product = chunk.product
            if not product.id:
                failed += 1
                failed_ids.append("<missing-id>")
                continue
            self._store[product.id] = product
            imported += 1

        if not seen_metadata:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "first message must be metadata")

        return pb.ImportSummary(
            imported_count=imported, failed_count=failed, failed_ids=failed_ids
        )


def serve() -> None:
    store: dict[str, pb.Product] = {}
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    pb_grpc.add_ProductsServicer_to_server(ProductsServicer(store), server)

    # Reflection lets grpcurl and other tooling introspect the server.
    from grpc_reflection.v1alpha import reflection

    service_names = (pb.DESCRIPTOR.services_by_name["Products"].full_name, reflection.SERVICE_NAME)
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port("[::]:50051")
    server.start()
    log.info("catalog listening on :50051")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
```

Calling it (Python client with a deadline):

```python
import grpc
import catalog.v1.products_pb2 as pb
import catalog.v1.products_pb2_grpc as pb_grpc

channel = grpc.insecure_channel("localhost:50051")
stub = pb_grpc.ProductsStub(channel)

# Every call carries a deadline. Without one, a hung server hangs the caller forever.
try:
    product = stub.GetProduct(pb.GetProductRequest(id="p_123"), timeout=2.0)
except grpc.RpcError as exc:
    if exc.code() == grpc.StatusCode.NOT_FOUND:
        print("missing")
    elif exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        print("timed out; retry or degrade")
    else:
        print(f"rpc failed: {exc.code()}: {exc.details()}")

# Streaming client: iterate lazily and stop early.
for p in stub.ListProducts(pb.ListProductsRequest(page_size=10), timeout=5.0):
    print(p.id, p.name)
    break  # breaking out cancels the call
```

Note that `grpc.insecure_channel` is plaintext and fine only for local development. Use TLS credentials in any deployed environment; see the security section.

## Schema design

Proto3 field numbering is a permanent, public contract. Once a number is used it can never mean anything else, and reusing a number is the most dangerous mistake you can make because it silently corrupts data across every deployed version.

Rules that prevent future breakage:

- Start every message at field number `1` and increment. Reserve nothing you have not used.
- Never renumber fields. To remove a field, mark it `reserved "name"; reserved 5;` and never reuse the number.
- Use `optional` for fields where you must distinguish "absent" from "default value" (see field presence below).
- Prefer `optional` or wrapper semantics over bare pointers, and prefer explicit `oneof` for mutually exclusive fields.
- Use enums with a `UNSPECIFIED = 0` zero value. The zero value must be a safe default and must not mean a valid business state.
- Use `google.protobuf.Timestamp` (not epoch integers) and `google.protobuf.Duration` for time; they serialize unambiguously across languages.
- Follow the official protobuf style guide for naming: `package_name`, `MessageName`, `message_name`, `RPCName`. Auto-generated code depends on these conventions.
- Document each field in a comment. The comment is what shows up in generated docs and IDE tooltips.

Field presence in proto3 is subtle and has bitten everyone. In proto3 without `optional`, a scalar field equal to its default (`0`, `""`, `false`) is indistinguishable from absent:

```proto
message UserPatch {
  // Without `optional`, you cannot tell "set display_name to empty string"
  // from "do not touch display_name".
  optional string display_name = 1;
  optional bool email_verified = 2;
}
```

Rules of thumb:

- Use `optional` on any update/partial-update message.
- Use `oneof` when exactly one of several fields will be set and the distinction matters.
- Be careful with the classic "cannot unset" case: to clear a scalar, document sending the default value and treat receiving the default as an explicit clear, which is why `optional` (message-wrapped in the API, distinct on the wire) is often better.

### Service and method modeling

gRPC method naming should read as RPC, not HTTP. Follow the widely used AIP conventions:

- RPC names are `EntityAction` in PascalCase: `GetProduct`, `ListProducts`, `CreateProduct`, `UpdateProduct`, `DeleteProduct`, `BatchGetProducts`, `ArchiveProduct`.
- Use `Get` for reads by ID, `List` for collection reads, `Create`/`Update`/`Delete` for mutations.
- Prefer streaming `List` for large collections over a single message carrying every row; memory and time-to-first-byte both improve.
- Standard method signatures: `GetX(GetXRequest) returns (X)`, `ListX(ListXRequest) returns (stream X)` for large sets.

Request and response messages should be named after the RPC, not `Request`/`Response`, so a wire dump is self-describing.

## Streaming modes

Four modes exist. Pick based on the data flow shape.

| Mode | Client sends | Server sends | Use for |
| --- | --- | --- | --- |
| Unary | one message | one message | Most RPCs. `GetProduct`. |
| Server streaming | one request | many responses | Large reads, incremental results, subscriptions over gRPC. `ListProducts`. |
| Client streaming | many requests | one response | Bulk upload, client-side batching. `ImportProducts`. |
| Bidirectional | many | many, interleaved | Chat, interactive sessions, full duplex. |

Patterns that matter in practice:

- **Backpressure in server streaming.** Do not `yield` faster than the transport drains. The runtime applies flow control, but if you buffer the entire result set before yielding, you have negated streaming. Yield incrementally from a cursor or a paged query.
- **Flow control in client streaming.** The client should limit in-flight messages; the server can check `context.is_active()` between iterations to stop early on cancellation.
- **Error propagation.** Once an error is raised (e.g., `context.abort`), the RPC terminates and the client sees the status. Partial successes should be reported inside a successful final message (like `ImportSummary`) rather than by aborting halfway, unless the contract says the whole operation must be all-or-nothing.
- **Backpressure in bidi.** Alternate request and response turns ("ping-pong") unless you truly need concurrent streams, and bound any queue between reader and writer goroutines.

Half-close matters: in client-streaming and bidi, the client signals end-of-stream. If the server tries to `yield` after the client half-closed, behavior depends on the runtime; make the handler respect `context.is_active()` and document that.

## Errors and status codes

gRPC status codes are a fixed, small vocabulary; choose the one that tells the caller what to do next. Do not invent codes, and do not put a success code with an error payload.

| Code | Meaning | Retry? | Typical cause |
| --- | --- | --- | --- |
| `OK` | Success | n/a | — |
| `CANCELLED` | Caller cancelled | no | Client aborted, deadline shorter than the work. |
| `UNKNOWN` | Unknown error | maybe | Unhandled exception. Last resort; leaks nothing useful. |
| `INVALID_ARGUMENT` | Bad input | no | Validation failure, missing field, malformed ID. |
| `DEADLINE_EXCEEDED` | Deadline passed | maybe | Work took too long. |
| `NOT_FOUND` | Resource absent | no | Missing entity. |
| `ALREADY_EXISTS` | Uniqueness conflict | no | Create with an existing key. |
| `PERMISSION_DENIED` | Authenticated, not allowed | no | AuthZ failure. |
| `UNAUTHENTICATED` | Missing/invalid credentials | after re-auth | AuthN failure. Include `WWW-Authenticate`-style metadata. |
| `RESOURCE_EXHAUSTED` | Quota or rate limit | yes, with backoff | Rate limit, quota exhausted, out of disk. |
| `FAILED_PRECONDITION` | System state blocks the operation | no | Delete a running resource, modify a finalized object. |
| `ABORTED` | Concurrency conflict | yes | Transaction conflict, optimistic-lock failure. |
| `OUT_OF_RANGE` | Beyond valid bounds | no | Offset past the end, invalid index. |
| `UNIMPLEMENTED` | Method not implemented | no | Missing RPC, wrong server. |
| `INTERNAL` | Internal invariant broken | maybe | Bug, unhandled error. |
| `UNAVAILABLE` | Temporarily down | yes | Server restarting, connection refused, overloaded. |
| `DATA_LOSS` | Unrecoverable data loss | maybe | Corrupted storage. |
| `UNAUTHENTICATED` | (see above) | | |
| `UNAUTHENTICATED` duplicates `UNAUTHENTICATED`; see table above for the correct usage. | | | |

Guide by intent:

- **Client will not fix it by retrying:** `INVALID_ARGUMENT`, `NOT_FOUND`, `ALREADY_EXISTS`, `PERMISSION_DENIED`, `UNAUTHENTICATED`, `FAILED_PRECONDITION`, `OUT_OF_RANGE`, `UNIMPLEMENTED`.
- **Client should retry with backoff:** `UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `ABORTED`, `DEADLINE_EXCEEDED` (if the operation is idempotent).
- **Bug on the server:** `INTERNAL`, `DATA_LOSS`, `UNKNOWN`.

Attach structured detail rather than encoding it in the message string:

```python
from google.rpc import code_pb2, status_pb2, error_details_pb2
import grpc

def abort_with_detail(context, code, message, field_violations=None):
    details = []
    if field_violations:
        for v in field_violations:
            bad = error_details_pb2.BadRequest.FieldViolation()
            bad.field = v["field"]
            bad.description = v["description"]
            details.append(bad.pack())

    rich = status_pb2.StatusProto()
    rich.code = code.value[0]
    rich.message = message
    for d in details:
        rich.details.add().Unpack(d)

    context.abort_with_status(rich)
```

Rich error details follow the Google API error model: send a `google.rpc.Status` as the status details and include `google.rpc.BadRequest` with `FieldViolation` entries so clients get structured, per-field errors. `abort_with_status` is the Python API; other languages have equivalents (for example `status.WithDetails` in Go). Serve the standard `google.rpc` status proto from `google/rpc/status.proto`.

Status-code discipline rules:

- Use `context.abort(code, message)` which raises and stops the handler. Do not return a status inside the message.
- For streams, the first error terminates the stream; later errors are not delivered.
- Never put secrets, SQL, or stack traces in the `message`. It crosses the network to the caller and is typically logged.
- `UNIMPLEMENTED` should be your default in a base-class stub method, so an unimplemented RPC is explicit rather than crashing.

## Deadlines and cancellation

A deadline is a promise that a call will not hang forever. Every client call should set one, and every server should propagate it to downstream calls. Without deadlines, a stuck dependency becomes a stuck thread pool, which becomes an outage.

```python
# Client: always set a deadline.
product = stub.GetProduct(pb.GetProductRequest(id="p_123"), timeout=2.0)

# Server: read the propagated deadline and shorten downstream calls.
class ProductsServicer(pb_grpc.ProductsServicer):
    def GetProduct(self, request, context):
        # Remaining budget for this call; a few seconds or less.
        budget = context.time_remaining()
        if budget is not None and budget < 0.1:
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "no time budget left")
        # Pass a fraction of the remaining budget to downstream calls.
        downstream_timeout = (budget * 0.8) if budget is not None else None
        return self._db.get(request.id, timeout=downstream_timeout)
```

Principles:

- Set the client deadline. Omitting it means the call waits for the network's default, which may be unbounded.
- Propagate the deadline on server-to-server calls so the whole chain respects the original budget.
- Check cancellation cooperatively in loops and long operations. When `context.is_active()` is false or the deadline passed, stop and return; do not finish the work for a caller that has already given up.
- Retry only idempotent operations, and only for retryable codes (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`). Use exponential backoff with jitter. gRPC also supports a declarative retry policy in the service config; if you use it, verify the exact policy fields with your library's docs.

## Interceptors

Interceptors are the gRPC analogue of middleware. They run on both client and server and are the right place for cross-cutting concerns: authentication, structured logging, metrics, tracing, retries, panic recovery.

```python
import time
import grpc
from typing import Any, Callable


class AuthInterceptor(grpc.ServerInterceptor):
    """Validate a bearer token from metadata and reject unauthenticated calls."""

    def __init__(self, verify: Callable[[str], dict | None]) -> None:
        self._verify = verify

    def intercept_service(self, continuation, handler_call_details):
        md = dict(handler_call_details.invocation_metadata or ())
        auth = md.get("authorization", "")
        if not auth.startswith("Bearer "):
            return _deny(handler_call_details, grpc.StatusCode.UNAUTHENTICATED, "missing bearer token")
        claims = self._verify(auth[len("Bearer ") :])
        if claims is None:
            return _deny(handler_call_details, grpc.StatusCode.UNAUTHENTICATED, "invalid token")
        return continuation(handler_call_details)


def _deny(details, code, message):
    # Build a handler that always aborts with the given status.
    def deny_behavior(request, context):
        context.abort(code, message)
    return grpc.unary_unary_rpc_method_handler(
        deny_behavior,
        request_deserializer=details.request_deserializer,
        response_serializer=details.response_serializer,
    )


class TimingInterceptor(grpc.ServerInterceptor):
    """Record per-method latency as structured logs."""

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None:
            return None
        method = handler_call_details.method
        start = time.perf_counter()

        if handler.request_streaming and handler.response_streaming:
            behavior = handler.stream_stream
            kind = "bidi"
        elif handler.request_streaming:
            behavior = handler.stream_unary
            kind = "client_stream"
        elif handler.response_streaming:
            behavior = handler.unary_stream
            kind = "server_stream"
        else:
            behavior = handler.unary_unary
            kind = "unary"

        def timed(request, context):
            try:
                return behavior(request, context)
            finally:
                log.info(
                    "rpc_complete",
                    extra={
                        "method": method,
                        "kind": kind,
                        "code": context.code().name if context.code() else "OK",
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                )

        return grpc.unary_unary_rpc_method_handler(
            timed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        ) if kind == "unary" else _wrap_stream(handler, timed, kind, start, method)
```

Note: wrapping a streaming handler requires preserving its streaming type. The shape above is illustrative; in production use the language's standard interceptor helpers rather than hand-wrapping handlers, because the streaming variants need their own wrappers. Check your language's gRPC docs for the provided interceptor base classes.

Server interceptor ordering matters. Authentication should run before logging that trusts identity, and logging should run outermost so it captures everything including rejections. Wire them explicitly:

```python
server = grpc.server(
    futures.ThreadPoolExecutor(max_workers=16),
    interceptors=[TimingInterceptor(), AuthInterceptor(verify_token)],
)
```

## Performance: protobuf binary over JSON

gRPC's default wire format is protobuf binary, which is more compact and faster to parse than the JSON mapping of the same message. The comparison is not always apples-to-apples (JSON over HTTP has different overhead in headers and framing), but within gRPC, binary is the default for good reason: less bytes on the wire and faster codec.

Guidance:

- Keep the default binary encoding. Do not switch a gRPC service to JSON just for readability; use `protoc --decode` or grpcurl with the proto for human inspection instead.
- Field numbers drive compactness. Delete and reuse of unused numbers is safe for size; use small numbers early and do not scatter renumbers. Every byte beyond a small threshold costs a varint byte per occurrence, so packing many rarely-used fields at high numbers inflates messages that carry them.
- Repeated scalars are packed by default in proto3; this is a large win and you should not set `packed = false` unless a consumer cannot decode it.
- Use `optional`/oneof and avoid unnecessary wrapper messages; each wrapper adds a length prefix and can prevent packing.
- Enable gzip compression for large or text-heavy payloads. gRPC supports per-message compression and negotiation; see the compression guide. Do not compress tiny messages, where the compression header and CPU outweigh the savings.
- Keepalive and HTTP/2 settings (max concurrent streams, ping interval) matter under load; tune per the keepalive and channel-args guidance rather than guessing values.
- Use the gRPC health-checking protocol so load balancers and orchestrators can probe readiness properly.

## Evolving the contract safely

Because proto changes are compiled, you can enforce compatibility in CI rather than discovering it in production.

- Lint: Buf lints against the standard rules (naming, field number ranges, required `UNSPECIFIED` zero enums, RPC request/response naming). Run `buf lint` in CI; the rule set is `STANDARD` by default.
- Breaking-change detection: `buf breaking` compares your `.proto` against a baseline (often a git ref or a published module) and fails when a change would break generated-code compatibility. This is the highest-value CI check for a proto repo.
- Reserve removed fields and numbers. Linting also flags reuse.
- Never renumber, never change a field's type, never change wire format (`string` to `bytes` is fine in some cases per spec but avoid), never reuse `reserved` numbers.

```yaml
# Example CI step (adapt to your repo layout).
name: proto-ci
on: [push, pull_request]
jobs:
  buf:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: bufbuild/buf-action@v1
        with:
          lint_input: proto
          breaking_input: 'proto'
          breaking_against: 'https://github.com/YOUR-ORG/YOUR-PROTO-REPO.git#branch=main,subdir=proto'
```

Check the Buf action's documentation for the exact input names, since they evolve. The `buf` CLI can also be invoked directly (`buf lint`, `buf breaking --against ...`), which is often simpler to reason about than the action.

## Checklist

- [ ] Every field number is assigned once and never reused; removed fields are `reserved`.
- [ ] Enums have an `UNSPECIFIED = 0` zero value that is not a valid business state.
- [ ] Partial-update messages use `optional` fields so presence is distinguishable.
- [ ] RPC names follow `EntityAction` and request/response messages are named after the RPC.
- [ ] List RPCs over large collections use server streaming rather than one giant message.
- [ ] Streaming handlers yield incrementally and check `context.is_active()` in loops.
- [ ] Errors use the right status code from the fixed vocabulary; retryable codes only for retryable conditions.
- [ ] Rich error details use `google.rpc.Status` and `BadRequest.FieldViolation`.
- [ ] Every client call sets a deadline; servers propagate a fraction downstream.
- [ ] Auth and logging are interceptors; ordering is explicit.
- [ ] Streaming interceptor wrappers preserve streaming vs unary handler types.
- [ ] Binary encoding is the default; compression is enabled selectively.
- [ ] `buf lint` and `buf breaking` run in CI and block merges.
- [ ] Server reflection and health checking are enabled deliberately (not left on by accident in prod, on for internal tooling).

## Common mistakes

- **Reusing a field number.** Symptom: a client compiled against an old schema silently reads a different field's value; no error, wrong data. Fix: `reserved` the number and never reuse it; catch it with `buf breaking`.
- **Renumbering to "tidy up".** Symptom: wire incompatibility with every deployed client. Fix: field numbers are immutable once published; linting enforces this.
- **No deadline on the client call.** Symptom: the client thread blocks indefinitely when the server hangs; thread pools exhaust and the whole client dies. Fix: always set `timeout`.
- **Ignoring `context.is_active()` in a long stream.** Symptom: server keeps processing messages for a cancelled or timed-out call, wasting CPU and potentially writing side effects nobody reads. Fix: check between iterations.
- **Using `UNKNOWN` for everything.** Symptom: clients cannot decide what to retry, and retrying non-retryable operations causes cascading load. Fix: map to specific codes.
- **Wrapping a streaming handler with a unary interceptor wrapper.** Symptom: runtime errors or silently broken streaming at the first response. Fix: preserve the handler type or use the language's streaming-aware interceptor helpers.
- **Putting the error detail in the message string.** Symptom: clients must string-match on text to detect conditions, and any rewording breaks them. Fix: structured status details.
- **A mutable global interceptor ordering.** Symptom: auth runs after logging that trusts the identity, so rejected calls log as authenticated. Fix: register interceptors explicitly, auth innermost, logging outermost.
- **Gzip on every message by default.** Symptom: added CPU and a compression header on tiny messages, making small RPCs slower. Fix: compress selectively by message size or content type.
- **Enum with zero meaning something real** (e.g., `STATUS_ACTIVE = 0`). Symptom: a client that never sets the field gets a valid-looking active state. Fix: reserve zero for `UNSPECIFIED`.

## Anti-patterns

- **Using gRPC for public-facing APIs without a gateway.** Browsers cannot speak gRPC/HTTP2 directly, and firewalls and CDNs often break HTTP/2 framing. Use gRPC internally and expose REST or Connect over the edge, or use a transcoding gateway.
- **Streaming a list where a paged unary response is simpler and sufficient.** Streaming adds connection lifecycle, cancellation, and error-propagation complexity you may not need. Prefer paged unary for lists under a few thousand rows.
- **`string` for every value, including numbers, timestamps, and enums.** You lose schema validation, cross-language type safety, and compactness. Use `int64`, `Timestamp`, and enums; the generated types are the point of the contract.
- **A giant `struct`-like `bytes` blob to "avoid schema churn".** Symptom: opaque payloads, no validation, no evolution story, every consumer must hand-decode. Fix: define the message; field numbers make evolution cheap.
- **Retry on every error.** Retrying `INVALID_ARGUMENT` or `PERMISSION_DENIED` produces load without success and can trip rate limiters. Retry only `UNAVAILABLE`, `RESOURCE_EXHAUSTED`, and idempotent `ABORTED`/`DEADLINE_EXCEEDED`.
- **Changing a field's type in place "because it is more correct".** This is a wire-breaking change; add a new field with a new number, deprecate the old, migrate, then remove.
- **Skipping CI proto checks.** "We are careful" is not a control. `buf breaking` makes compatibility a build failure; manual review does not scale across many services and field renumberings slip through review.

## References

- gRPC core concepts — https://grpc.io/docs/what-is-grpc/core-concepts/
- gRPC documentation guides — https://grpc.io/docs/guides/
- Deadlines — https://grpc.io/docs/guides/deadlines/
- Status codes and their use — https://grpc.io/docs/guides/status-codes/
- gRPC status codes reference — https://grpc.github.io/grpc/core/md_doc_statuscodes.html
- Retry guide — https://grpc.io/docs/guides/retry/
- Interceptors — https://grpc.io/docs/guides/interceptors/
- gRPC auth guide — https://grpc.io/docs/guides/auth/
- Flow control — https://grpc.io/docs/guides/flow-control/
- Performance guide — https://grpc.io/docs/guides/performance/
- Compression — https://grpc.io/docs/guides/compression/
- Keepalive — https://grpc.io/docs/guides/keepalive/
- Health checking — https://grpc.io/docs/guides/health-checking/
- gRPC languages index — https://grpc.io/docs/languages/
- Protobuf overview — https://protobuf.dev/overview/
- proto3 language guide — https://protobuf.dev/programming-guides/proto3/
- Field presence (proto3 optional and default behavior) — https://protobuf.dev/programming-guides/field_presence/
- Protobuf encoding and wire format — https://protobuf.dev/programming-guides/encoding/
- Protobuf JSON mapping — https://protobuf.dev/programming-guides/json/
- Protobuf style guide — https://protobuf.dev/programming-guides/style/
- Protobuf reference (well-known types) — https://protobuf.dev/reference/
- Buf linting — https://buf.build/docs/lint/
- Buf breaking-change detection — https://buf.build/docs/breaking/
- Buf CLI — https://buf.build/bufbuild/buf
- Google API Improvement Proposals (method naming conventions, AIP-121/191/193) — https://google.aip.dev/
- Google RPC status proto — https://github.com/googleapis/googleapis/blob/master/google/rpc/status.proto
- Connect protocol (alternative HTTP-friendly gRPC) — https://connectrpc.com/docs/protocol
- OWASP gRPC Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/gRPC_Security_Cheat_Sheet.html