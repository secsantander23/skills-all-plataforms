---
name: HTTP Fundamentals
description: Covers HTTP as an application protocol including methods and safety and idempotency, status code classes, headers and content negotiation, HTTP/1.1 versus HTTP/2 versus HTTP/3, connection management, compression, caching semantics with Cache-Control, ETag, and conditional requests, CORS and the same-origin policy, cookies and sessions, and proxies. Use when the user asks about HTTP semantics, status codes, headers, caching, CORS, cookies, keep-alive, protocol versions, or debugging a request and response.
metadata:
  opencode/autoinvoke: true
---

# HTTP Fundamentals

HTTP is a stateless request/response application protocol: the client opens a connection, sends
a request, the server answers, and neither side is required to remember anything. Everything
else — caching, sessions, CORS, load balancers — exists because that protocol is so simple.
Most HTTP bugs are a mismatch between what the protocol guarantees and what the code assumes.

Normative references: **RFC 9110** (semantics), **RFC 9111** (caching), **RFC 9112** (HTTP/1.1),
**RFC 9113** (HTTP/2), **RFC 9114** (HTTP/3).

## When to use this

- Choosing a method or deciding whether an operation is safe or idempotent.
- Picking the right status code, or debugging why one was rejected.
- Setting up caching, revalidation, or a CDN.
- Debugging CORS preflight failures.
- Deciding whether HTTP/2 or HTTP/3 is being used, and why it is not.
- Configuring cookies, sessions, or proxy header handling.

## Quick start

A minimal exchange, annotated:

```http
GET /orders/42 HTTP/1.1
Host: api.example.com
Accept: application/json
Accept-Encoding: gzip, br
If-None-Match: "7f3a"
```

```http
HTTP/1.1 304 Not Modified
ETag: "7f3a"
Cache-Control: max-age=0, must-revalidate
Content-Encoding: br
```

The server found the client's validator still valid and answered with no body. This single
pattern is the whole point of HTTP caching: repeat a GET for the price of headers.

## Methods: safety and idempotency

RFC 9110 §9 defines two properties:

- **Safe**: the method is read-only by intent. A safe method MUST NOT request a state change
  the server is obliged to honor. Clients may prefetch, cache, and retry safe methods freely.
- **Idempotent**: the effect on the server of N identical requests is the same as the effect of
  one. Clients may automatically retry idempotent methods.

| Method | Safe | Idempotent | Semantics | Notes |
| --- | --- | --- | --- | --- |
| `GET` | Yes | Yes | Read a representation | Only `GET`/`HEAD` responses are cacheable by default |
| `HEAD` | Yes | Yes | Same headers as `GET`, no body | Must not send a body |
| `OPTIONS` | Yes | Yes | Communication options | CORS preflight uses it |
| `TRACE` | Yes | Yes | Loopback diagnostics | Often disabled for security |
| `POST` | No | No | Create a subordinate resource or non-idempotent action | Use an `Idempotency-Key` to make it retryable |
| `PUT` | No | Yes | Replace or create at a known URI | The URI is the thing being identified |
| `DELETE` | No | Yes | Remove the resource | Second delete may return `404` or `204`; both are fine |
| `PATCH` | No | No* | Partial modification | *Idempotent if the patch document is idempotent (e.g. a JSON Patch that sets values) |

Two traps:

- **`POST` is not idempotent**, so a client or proxy must not retry it automatically. If a
  retryable create matters, accept an idempotency key header and de-duplicate server-side.
- **`PUT` is idempotent only if you replace, not append.** `PUT /orders` that appends to a
  list is a `POST` wearing a disguise.

`HEAD` must return the same status code and the same `Content-Length`, `Content-Type`, and other
representation metadata as the `GET` would, but no content.

## Status codes

Class meanings follow RFC 9110 §15.

| Class | Meaning | Client should |
| --- | --- | --- |
| 1xx | Informational, interim | Wait for the final response |
| 2xx | Succeeded | Act on the representation |
| 3xx | Redirection, or use the cached copy | Follow or use cache |
| 4xx | Client error | Fix the request before retrying |
| 5xx | Server error | Retry only if the response says it is safe to |

Codes you will actually meet:

| Code | Meaning | Correct use |
| --- | --- | --- |
| `200 OK` | Success | Response carries a representation |
| `201 Created` | Created | Include a `Location` header |
| `202 Accepted` | Accepted for processing | Work happens asynchronously |
| `204 No Content` | Success, no body | `DELETE` success; MUST NOT include a body |
| `206 Partial Content` | Partial representation | Sent in response to a `Range` request |
| `301 Moved Permanently` | Permanent redirect | Method may change; see below |
| `302 Found` | Temporary redirect | Historically user agents may turn `POST` into `GET` |
| `304 Not Modified` | Cached copy still valid | No body; used with a conditional request |
| `307 Temporary Redirect` | Temporary redirect | Method and body are preserved |
| `308 Permanent Redirect` | Permanent redirect | Method and body are preserved |
| `400 Bad Request` | Malformed or invalid | Schema validation failure |
| `401 Unauthorized` | Not authenticated | Include a `WWW-Authenticate` header; name is a misnomer |
| `403 Forbidden` | Authenticated but not allowed | Do not retry |
| `404 Not Found` | No such resource | Also a valid answer to hide existence |
| `405 Method Not Allowed` | Method not supported here | MUST include an `Allow` header |
| `406 Not Acceptable` | No acceptable representation | Server cannot satisfy `Accept` |
| `409 Conflict` | Conflicts with current state | Duplicate key, version conflict |
| `410 Gone` | Permanently gone | Stronger than `404`, signals removal |
| `412 Precondition Failed` | A conditional header failed | `If-Match`/`If-None-Match` semantics |
| `413 Content Too Large` | Body too big | Distinguish from `431` for headers |
| `415 Unsupported Media Type` | `Content-Type` not supported | Client sent the wrong media type |
| `422 Unprocessable Content` | Syntactically valid, semantically wrong | Well-formed JSON that fails domain rules |
| `429 Too Many Requests` | Rate limited | Include `Retry-After` |
| `500 Internal Server Error` | Unexpected failure | Bug; log with a request ID |
| `501 Not Implemented` | Method or feature unknown | Server does not support it |
| `502 Bad Gateway` | Invalid response from an upstream | Proxy-specific |
| `503 Service Unavailable` | Temporarily down | Include `Retry-After` |
| `504 Gateway Timeout` | Upstream did not respond in time | Proxy-specific |

Distinctions that are commonly gotten wrong:

- `400` versus `422`. `400` means the request could not be parsed or fails the schema. `422`
  means it parsed and the content failed business rules. Both are defensible if documented
  consistently.
- `401` versus `403`. `401` means "I do not know who you are"; send `WWW-Authenticate`. `403`
  means "I know who you are and the answer is no". Retrying a `403` with the same credentials
  never succeeds.
- `404` versus `410`. `410` tells caches and crawlers the resource is gone for good.
- `429` and `503` are both retryable, but `429` means the client should slow down and `503`
  means the server is unavailable.

Redirect method preservation: RFC 9110 §15.4 explicitly preserves the historical behavior where
user agents MAY change the request to `GET` for `301` and `302`. If you are redirecting a
`POST` and the method must survive, use `307` or `308`.

## Headers

### Request framing and identity

- `Host` — required in HTTP/1.1; replaced by the `:authority` pseudo-header in HTTP/2 and 3.
- `Content-Length` — exact byte count of the body.
- `Transfer-Encoding: chunked` — streaming body without a known length. **Forbidden in HTTP/2
  and HTTP/3**; framing is at the frame layer.
- `User-Agent` — client identifier.
- `Connection` — hop-by-hop control; the header field itself is not forwarded by proxies. In
  HTTP/2 and HTTP/3, connection-specific fields are forbidden.
- `Expect: 100-continue` — client asks permission before sending a large body.

### Content negotiation

```http
Accept: application/json;q=0.9, application/xml;q=0.8, */*;q=0.1
Accept-Language: en-GB, en;q=0.9
Accept-Encoding: gzip, br, zstd;q=0.9
```

`q` values run from 0 to 1 with one decimal place; higher is more preferred. `q=0` means "not
acceptable" and must be honored, not treated as a wildcard. The server must ignore preferences
it does not understand by falling back to `*/*`.

Corresponding response headers: `Content-Type`, `Content-Language`, `Content-Encoding`,
`Vary` (which request headers affected the selection — a proxy needs this to cache correctly).

### Compression

```http
Content-Encoding: br
```

`gzip` (RFC 1952), `br` (RFC 7932, Brotli), and `zstd` (RFC 8878) are the current encodings.
Always send `Vary: Accept-Encoding`. Compression changes the response to the same
`Content-Length`, so never cache a compressed representation without a `Vary`.

Compression only pays for text and other compressible payloads; it usually makes images and
already-compressed media larger. Measure rather than assuming.

### Cookies and sessions

```http
Set-Cookie: session=abc123; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=3600
Set-Cookie: __Host-session=abc123; Path=/; Secure; HttpOnly; SameSite=Lax
```

| Attribute | Effect |
| --- | --- |
| `Domain` | Scope; omit to make the cookie host-only |
| `Path` | Path prefix the cookie is sent for |
| `Max-Age` / `Expires` | Lifetime; a session cookie is sent when both are absent |
| `Secure` | Send only over HTTPS |
| `HttpOnly` | Not readable from `document.cookie` |
| `SameSite=Lax` | Not sent on cross-site subrequests; sent on top-level GET navigation. The default in modern browsers when omitted |
| `SameSite=Strict` | Not sent on any cross-site request; breaks inbound links |
| `SameSite=None` | Sent cross-site; **requires `Secure`** |
| `Partitioned` | Partition by top-level site (CHIPS); requires `Secure` |

Cookie prefixes are a browser-enforced contract:

- `__Host-` requires `Secure`, `Path=/`, and **no** `Domain` attribute.
- `__Secure-` requires `Secure`.

Browsers reject a cookie that violates its prefix, so prefixing converts a silent
misconfiguration into a visible failure.

`SameSite=None; Secure` is required for cookies read in a cross-site context, including
iframes on another origin.

## Caching

Two independent layers implement RFC 9111: the browser's private cache, and shared caches
(proxies, CDNs).

### Cache-Control directives

| Directive | Meaning |
| --- | --- |
| `max-age=<s>` | Fresh for N seconds in any cache |
| `s-maxage=<s>` | Fresh for N seconds in shared caches only |
| `no-store` | Store nothing, anywhere. The strongest instruction |
| `no-cache` | Store, but revalidate before every reuse |
| `must-revalidate` | Once stale, must not be served without validation |
| `immutable` | Will not change during its freshness lifetime |
| `private` | Only a browser may store it |
| `public` | Shared caches may store it, even for `Authorization` responses |

Selection guidance:

| Content | Recommended |
| --- | --- |
| Fingerprinted asset with a hash in the URL | `public, max-age=31536000, immutable` |
| HTML page | `no-cache` (store it, revalidate every time) |
| Authenticated API response | `private, no-store` |
| Public data read by many, tolerant of staleness | `public, max-age=60, stale-while-revalidate=600` |
| Data that must never appear in a shared cache | `no-store` |

### Validators and conditional requests

```http
GET /orders/42 HTTP/1.1
Host: api.example.com
If-None-Match: "7f3a"
```

```http
HTTP/1.1 304 Not Modified
ETag: "7f3a"
Cache-Control: max-age=0, must-revalidate
```

- `ETag` — an opaque validator for the selected representation. Strong (`"abc"`) or weak
  (`W/"abc"`). Weak validators compare semantic equivalence, not bytes.
- `Last-Modified` — a date validator. Coarse: one-second resolution, and it is unusable when the
  clock skews or the change happens within the same second.
- `If-None-Match` — on a safe method, a matching validator yields `304`. On an unsafe method, a
  matching validator yields `412`.
- `If-Modified-Since` — date form of the same, used only when `If-None-Match` is absent.
- `If-Match` / `If-None-Match` on writes — optimistic concurrency. If the resource changed
  since you read it, the write fails with `412` rather than clobbering it.
- `If-Range` — allows a client to send a range request only if the resource is unchanged.

Prefer `ETag` over `Last-Modified`: it is exact, and it is the only one that works for
representations whose dates do not change.

### Range requests

```http
GET /video.mp4 HTTP/1.1
Range: bytes=0-1023
```

```http
HTTP/1.1 206 Partial Content
Accept-Ranges: bytes
Content-Range: bytes 0-1023/5242880
Content-Length: 1024
```

`Accept-Ranges: bytes` advertises support so clients know they can resume, split downloads, and
seek. Resumable uploads use this too.

## CORS and the same-origin policy

The same-origin policy says a document may not read a response from a different origin. CORS
(RFC 6454, implemented in the Fetch Standard) is the protocol that relaxes it by asking the
server for permission.

A cross-origin request with credentials (`fetch` with `credentials: 'include'`, or an `XMLHttpRequest`
with `withCredentials`) triggers a **preflight**: an `OPTIONS` request the browser sends before
the real one.

```http
OPTIONS /v1/orders HTTP/1.1
Origin: https://app.example.com
Access-Control-Request-Method: POST
Access-Control-Request-Headers: content-type,idempotency-key
```

```http
HTTP/1.1 204 No Content
Access-Control-Allow-Origin: https://app.example.com
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, Idempotency-Key
Access-Control-Allow-Credentials: true
Access-Control-Max-Age: 600
Vary: Origin
```

Rules that cause most failures:

1. `Access-Control-Allow-Origin: *` is **invalid** when credentials are used. Echo the exact
   origin, and send `Access-Control-Allow-Credentials: true`.
2. Always send `Vary: Origin` when the allowed origin depends on the request, or a shared cache
   will serve one origin's response to another.
3. `Access-Control-Expose-Headers` is required to read anything beyond the CORS-safelisted
   response headers (`Cache-Control`, `Content-Language`, `Content-Length`, `Content-Type`,
   `Expires`, `Last-Modified`, `Pragma`). If your client cannot read your `X-Request-Id`, add it
   there.
4. A preflight is cached for `Access-Control-Max-Age` seconds. After changing allowed headers,
   clients may fail for the cache duration.
5. CORS is enforced by the browser only. It is not an authorization mechanism: a non-browser
   client ignores it entirely. Enforce authorization server-side.
6. `mode: 'no-cors'` returns an opaque response. The request is sent, but the body cannot be
   read. It is not a way around CORS.

## Connection management

**HTTP/1.1** is persistent by default: a connection carries many requests. A server or proxy
closes it with `Connection: close`, or when its idle timeout expires.

- `Connection: keep-alive` in HTTP/1.0 opts in. In HTTP/1.1 it is the default.
- Too many parallel connections per host (historically 6 per origin) causes queuing. HTTP/2
  multiplexing fixes this.
- Keep idle connections to low-latency origins alive, and drop idle ones to expensive origins.
- The server's idle timeout must be known and respected by the client; a client that reuses a
  closed connection must retry idempotent requests on a new connection.

**HTTP/2** (RFC 9113):

- Binary framing, with a stream per request.
- HPACK header compression (RFC 7541).
- Multiplexing over one connection: no head-of-line blocking between streams.
- Streams are *not* prioritized in the base spec; the Extensible Prioritization document
  (RFC 9218) standardizes priority. Do not rely on server push: it was removed from later core
  drafts and support is uneven.
- Still bound by TCP-level head-of-line blocking: a lost packet stalls every stream on that
  connection.

**HTTP/3** (RFC 9114):

- Runs over QUIC (RFC 9000), which integrates TLS 1.3 and does not need a separate `TCP` +
  `TLS` handshake.
- Per-stream loss recovery: a lost packet does not stall unrelated streams.
- QPACK header compression (RFC 9204).
- Connection migration: the connection survives a network change.
- Usually reached over HTTP/3 via an `Alt-Svc` header from an HTTP/2 or HTTP/1.1 response; a
  client advertises HTTP/3 with `Alt-Requires` / the `h3` ALPN token.

```http
HTTP/2 200
alt-svc: h3=":443"; ma=86400
```

`ma` is the lifetime in seconds. Browsers may ignore it. If HTTP/3 is a requirement, verify it
is actually negotiated with a client that reports `h3` in its ALPN list.

## Proxies and reverse proxies

A **forward proxy** sits in front of the client and fetches on its behalf. A **reverse proxy**
sits in front of servers, which are usually unaware of it.

Every hop adds, or can add, hop-by-hop headers (`Connection`, `Keep-Alive`, `Proxy-Authenticate`,
`TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`). A proxy must strip these before forwarding,
because they describe *its* connection, not yours.

Trusting client-supplied forwarding headers is a real vulnerability. `X-Forwarded-For` is a
de facto convention (comma-separated list, appending); **RFC 7239 `Forwarded`** is the
standardized equivalent:

```http
Forwarded: for=192.0.2.60;proto=https;by=203.0.113.43;host=example.com
```

```http
X-Forwarded-For: 192.0.2.60, 198.51.100.17
X-Forwarded-Proto: https
X-Forwarded-Host: example.com
```

Correct configuration:

1. Strip incoming `X-Forwarded-*` / `Forwarded` at the edge.
2. Append the real peer address as your proxy appends it.
3. Configure the trusted proxy count so the client address is taken from the correct position.
4. Ignore the forwarding headers entirely when the connection is not from a trusted proxy.

Get this wrong and an attacker spoofs their IP, which defeats rate limiting, audit logs, and IP
allowlists all at once.

## Checklist

- [ ] Method semantics are correct: `PUT` replaces, `DELETE` removes, `POST` creates or acts.
- [ ] Retryable operations are idempotent, or accept an idempotency key.
- [ ] Status codes match the actual outcome; `401` carries `WWW-Authenticate`; `405` carries
      `Allow`.
- [ ] `4xx` versus `5xx` split is meaningful so clients can decide whether to retry.
- [ ] `Content-Type` is sent and correct on responses that have a body.
- [ ] Caching: personalized responses are `private` or `no-store`; public responses carry
      `Vary: Accept-Encoding`.
- [ ] ETags exist on cacheable resources; `304` responses carry no body.
- [ ] CORS: explicit origin (never `*` with credentials), `Vary: Origin`, exposed headers.
- [ ] Cookies: `Secure`, `HttpOnly`, `SameSite`, and a `__Host-` prefix where host-only scope
      is intended.
- [ ] Forwarding headers are set by the edge only and read only from trusted proxies.

## Common mistakes

**A `200` with an error body.** Symptom: clients check `status === 200` and treat
`{"error": ...}` as data. Fix: use the 4xx/5xx code; the body is for humans.

**Caching personalized data publicly.** Symptom: one user's name appears in another's page from
a CDN. Fix: `Cache-Control: private, no-store` on any response that varies by identity.

**Missing `Vary: Origin` with credentialed CORS.** Symptom: intermittent CORS failures that
depend on which edge node answers. Fix: send `Vary: Origin` and set `Access-Control-Allow-Private-Network`
where local network access is genuinely required.

**Retrying `POST` after a timeout.** Symptom: duplicate orders. Fix: idempotency key, or move
to `PUT` with a client-generated identifier.

**`Transfer-Encoding: chunked` leaking into HTTP/2.** Symptom: protocol error on h2. That
header is forbidden in HTTP/2 and HTTP/3; framing is implicit.

**Comparing `Last-Modified` across hosts with skewed clocks.** Symptom: intermittent `304`s
missed. Fix: use `ETag`.

**Trusting `X-Forwarded-For`.** Symptom: rate limits applied to a spoofed address. Fix: strip at
the edge, trust a fixed proxy depth.

**`Set-Cookie` without `SameSite` while assuming `Strict`.** Symptom: session loss after an
inbound link. Fix: set it explicitly; browsers default to `Lax` but not always as you expect
for your flow.

## Anti-patterns

- **Using `POST` for everything.** It makes retries, caching, and idempotency impossible.
- **`403` for "not logged in."** It breaks clients that can recover from `401`.
- **Accepting a `PUT` and appending.** Idempotency is a claim you must be able to defend.
- **Disabling TLS verification "for performance".** This is how CORS-looking problems become
  interception problems.
- **Stateful, per-connection sessions that break on HTTP/2 multiplexing.** Key sessions to a
  token, not a connection.
- **Serving the same HTML from many origins and treating CORS as a solve.** Fix the
  architecture, not the header.
- **Returning `200` from a health check that never touches dependencies.** A green check that
  cannot fail is theater.

## References

- RFC 9110, HTTP Semantics: https://www.rfc-editor.org/rfc/rfc9110
- RFC 9111, HTTP Caching: https://www.rfc-editor.org/rfc/rfc9111
- RFC 9112, HTTP/1.1: https://www.rfc-editor.org/rfc/rfc9112
- RFC 9113, HTTP/2: https://www.rfc-editor.org/rfc/rfc9113
- RFC 9114, HTTP/3: https://www.rfc-editor.org/rfc/rfc9114
- RFC 9000, QUIC Transport: https://www.rfc-editor.org/rfc/rfc9000
- RFC 9218, Extensible Prioritization for HTTP: https://www.rfc-editor.org/rfc/rfc9218
- RFC 7239, Forwarded HTTP Header: https://www.rfc-editor.org/rfc/rfc7239
- RFC 6454, The Web Origin Concept: https://www.rfc-editor.org/rfc/rfc6454
- RFC 6585, Additional Status Codes (429): https://www.rfc-editor.org/rfc/rfc6585
- RFC 7932, Brotli: https://www.rfc-editor.org/rfc/rfc7932
- RFC 8878, Zstandard Content Encoding: https://www.rfc-editor.org/rfc/rfc8878
- RFC 8594, Sunset Header: https://www.rfc-editor.org/rfc/rfc8594
- MDN HTTP overview: https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview
- MDN, CORS: https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS
- MDN, HTTP caching: https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching
- MDN, SameSite: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
- MDN, HTTP cookies: https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies
- MDN, same-origin policy: https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy
- WHATWG Fetch Standard (CORS implementation): https://fetch.spec.whatwg.org/
- web.dev, HTTP/3 explained: https://web.dev/articles/http3