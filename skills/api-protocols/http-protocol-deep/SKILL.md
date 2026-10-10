---
name: HTTP Protocol Deep Dive
description: Covers HTTP at the wire level, including RFC 9110 message semantics, RFC 9112 HTTP/1.1 framing and field parsing, Content-Length versus chunked transfer coding, request smuggling classes, HTTP/2 frames, HPACK, streams and flow control, HTTP/3 over QUIC, protocol upgrade, and the reframing duties of intermediaries. Use when debugging proxy or gateway behaviour, when a body parses differently at two hops, when a 400 or 411 appears unexpectedly, when auditing header parsing, or when reasoning about HTTP/2 and HTTP/3 internals.
metadata:
  opencode/autoinvoke: true
---

# HTTP Protocol Deep Dive

HTTP is a framing protocol first and a semantics protocol second. Almost every confusing production bug in a proxy, gateway, WAF, or API stack comes from two parties in the same request path disagreeing about where one message ends and the next begins. This skill teaches you to read the bytes, apply the framing rules in precedence order, and locate the disagreement.

## When to use this

- A request works against one host and fails against another, or works in `curl` but not behind a load balancer.
- You get `400 Bad Request`, `411 Length Required`, `413`, or a connection reset on a body you believe is valid.
- You are writing or reviewing a reverse proxy, gateway, WAF, or framework HTTP parser.
- You need to reason about `Transfer-Encoding`, chunked bodies, trailers, or `Connection` header handling.
- You are tuning HTTP/2 (flow control, `SETTINGS_MAX_CONCURRENT_STREAMS`, HPACK) or debugging HTTP/3 adoption.
- You are implementing protocol upgrade (`Upgrade`, `CONNECT`, WebSocket handshake) or RFC 6455-style long-lived transports.

## Quick start

Read a raw exchange over plain TCP. This is the fastest way to see framing that a client library hides from you.

```bash
# Send a literal HTTP/1.1 request byte for byte, with CRLF line endings.
printf 'GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n' | nc example.com 80
```

```text
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
Content-Length: 1256
Connection: close

<!doctype html>
```

Over TLS, use `openssl s_client`. `-quiet` hides the certificate chain, `-crlf` makes the line endings real CRLFs, and `-ign_eof` keeps the pipe open.

```bash
printf 'GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n' \
  | openssl s_client -quiet -connect example.com:443 -servername example.com 2>/dev/null
```

See exactly which HTTP versions and ALPN tokens a server accepts:

```bash
openssl s_client -alpn h2,http/1.1 -connect example.com:443 -servername example.com </dev/null
```

Negotiating a cleartext HTTP/2 connection with prior knowledge, then dumping the client preface in hex:

```bash
printf 'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n' | nc example.com 8080 | xxd | head -3
```

## The message as defined by RFC 9110 and RFC 9112

An HTTP/1.1 message is a start line, an optional field section, an empty line, and an optional content section. Everything else is convention layered on top.

```text
POST /v1/orders HTTP/1.1\r\n
Host: api.example.com\r\n
Content-Type: application/json\r\n
Content-Length: 27\r\n
\r\n
{"sku":"A-1","qty":2}  (27 octets)
```

Start lines (RFC 9112 Section 3 and Section 4):

```abnf
request-line   = method SP request-target SP HTTP-version CRLF
status-line    = HTTP-version SP status-code SP [ reason-phrase ] CRLF
HTTP-version   = "HTTP/" DIGIT "." DIGIT
```

Three request-target forms exist (RFC 9112 Section 3.2). `origin-form` (`/path?q=1`) is what clients send to servers. `absolute-form` (`http://host/path`) is what clients send to proxies and what `CONNECT` tunnels hide. `authority-form` is used by `CONNECT`, and `asterisk-form` (`OPTIONS *`) is used for a server-wide options query.

A field line (RFC 9110 Section 5.2, RFC 9112 Section 5.1):

```abnf
field-line     = field-name ":" OWS field-value OWS
field-name     = token
OWS            = *( SP / HTAB )
token          = 1*tchar
tchar          = "!" / "#" / "$" / "%" / "&" / "'" / "*"
               / "+" / "-" / "." / "^" / "_" / "`" / "|" / "~"
               / DIGIT / ALPHA
```

Two parsing rules are security requirements, not style preferences:

1. **No whitespace is allowed between the field name and the colon.** RFC 9112 Section 5.1 requires a server to reject with `400 Bad Request` any request where `Content-Length : 5` appears with a space. A proxy must strip such whitespace from responses before forwarding. If your parser is lenient here, it disagrees with a stricter peer, and that disagreement is a smuggling primitive.
2. **Obsolete line folding (`obs-fold`) is deprecated.** A `CRLF` followed by `SP` or `HTAB` inside a field value must either be rejected with `400` or normalized to one or more `SP` before the value is interpreted or forwarded. Never interpret the continuation as-is.

Field names are case-insensitive (`RFC 9110 Section 5.1`). Field values may be case-sensitive, depending on the individual field definition. Combine repeated fields per the field's own rules; several fields are defined as comma-separated lists using the `#rule` ABNF extension (RFC 9110 Section 5.6.1), and several are defined to allow comma-joined values from repeated lines (the historical `Set-Cookie` exception).

## Body framing: the precedence order that decides where a message ends

RFC 9112 Section 6.3 defines message body length as an ordered list. Implementations that get this order wrong are the direct cause of desynchronisation bugs. Apply it literally:

| Order | Condition | Result |
| --- | --- | --- |
| 1 | Response to `HEAD`, or status `1xx`, `204`, or `304` | Body length is zero, whatever the headers say |
| 2 | Successful response to `CONNECT` | Everything after the blank line is tunnel data; ignore `Content-Length` and `Transfer-Encoding` |
| 3 | Both `Transfer-Encoding` and `Content-Length` present | `Transfer-Encoding` wins; treat as an error and close the connection after responding |
| 4 | `Transfer-Encoding` present, `chunked` last coding | Body ends at the terminating zero-size chunk plus trailers |
| 4b | `Transfer-Encoding` present in a response, `chunked` not last | Body ends when the server closes the connection |
| 4c | `Transfer-Encoding` present in a request, `chunked` not last | Body length is undeterminable: respond `400` and close |
| 5 | No `Transfer-Encoding`, invalid `Content-Length` | Unrecoverable framing error: `400` for a request, `502` for a proxy-observed response |
| 6 | Valid `Content-Length`, no `Transfer-Encoding` | Body is exactly that many octets |
| 7 | Request with none of the above | Body length is zero |
| 8 | Response with none of the above | Body is close-delimited (reads until the server closes) |

Consequences you must design around:

- **Requests are never close-delimited.** The absence of both fields means the request ends at the blank line. A client that wants to stream a request body must send `Transfer-Encoding: chunked` or a valid `Content-Length`.
- **`Content-Length` in a request with a body but no framing is a `411 Length Required` case** for servers that require it. RFC 9112 explicitly notes that some services return `411` even though they understand chunked, because a gateway in front of them needs the length up front.
- **Duplicate `Content-Length` values are only tolerated when they are all identical.** A comma-separated list is accepted only if every value parses and all values match. `Content-Length: 5` plus `Content-Length: 6` is invalid framing, and `Content-Length: 5, 5` is accepted as `5`.
- **Chunk sizes are hexadecimal.** Parse them into a wide integer type. RFC 9112 Section 7.1 warns that very large numerals cause overflows or precision loss.

Chunked grammar (RFC 9112 Section 7.1):

```abnf
chunked-body   = *chunk last-chunk trailer-section CRLF
chunk          = chunk-size [ chunk-ext ] CRLF chunk-data CRLF
chunk-size     = 1*HEXDIG
last-chunk     = 1*("0") [ chunk-ext ] CRLF
chunk-data     = 1*OCTET
chunk-ext      = *( BWS ";" BWS chunk-ext-name [ BWS "=" BWS chunk-ext-val ] )
trailer-section = *( field-line CRLF )
```

Note the structural ambiguity: `chunk-size CRLF chunk-data CRLF` means the octet count is read as hex, then exactly that many octets are consumed, then one more `CRLF`. A decoder that instead scans for the next `\r\n` will desynchronise on any chunk whose payload contains `\r\n`.

```text
HTTP/1.1 200 OK\r\n
Content-Type: text/plain\r\n
Transfer-Encoding: chunked\r\n
Trailer: X-Checksum\r\n
\r\n
1d\r\n                              (29 octets follow)
Hello, world. This is a chunk.\r\n
0\r\n
X-Checksum: 9f86d081\r\n
\r\n
```

Trailer fields are received separately from the header section. A recipient may retain and forward them, but it must merge a trailer into the header section only when that field's definition explicitly permits merging (RFC 9110 Section 6.5).

## Request smuggling: how two parsers desynchronise

Request smuggling (RFC 9112 Section 11.2) is an exploitation of differences in message parsing between recipients. Front end and back end disagree about where request one ends, so a smuggled prefix inside request one is later read as the start of request two by the component that reuses the connection. The classes, by which header each side trusts:

| Class | Front end trusts | Back end trusts | Typical split |
| --- | --- | --- | --- |
| CL.TE | `Content-Length` | `Transfer-Encoding: chunked` | Front end forwards the declared `Content-Length` bytes; the back end stops at the first `0` chunk and treats the remainder as a new request |
| TE.CL | `Transfer-Encoding` | `Content-Length` | Back end trusts the smaller `Content-Length`, leaving the rest of the chunked stream in the buffer |
| TE.TE | Both, but with different parsing of the coding list | Both | Disagreement over duplicated, reordered, or differently normalised coding tokens, or over invalid whitespace inside the value |
| H2.CL | HTTP/2 front end | HTTP/1.1 back end | HTTP/2 has no `Transfer-Encoding`, so the converter must synthesise a length; if it forwards a stale or duplicated `Content-Length`, the back end splits differently |
| H2.TE | HTTP/2 front end | HTTP/1.1 back end | A converter that forwards `Transfer-Encoding` into HTTP/2, which RFC 9113 Section 8.2.2 requires to be treated as malformed |

Concrete CL.TE bytes. The front end sees `Content-Length: 56` and takes the whole blob as one request. The back end decodes chunks and finds `0\r\n\r\n` early, treating the remainder as a second request.

```text
POST / HTTP/1.1\r\n
Host: front.example.com\r\n
Content-Type: application/x-www-form-urlencoded\r\n
Content-Length: 56\r\n
Transfer-Encoding: chunked\r\n
\r\n
0\r\n
\r\n
GET /admin HTTP/1.1\r\n
Host: front.example.com\r\n
\r\n
```

Defensive rules that remove entire classes:

- **Reject rather than reconcile.** If both `Content-Length` and `Transfer-Encoding` are present, respond `400 Bad Request` and close the connection. RFC 9112 Section 6.3 point 3 allows processing by `Transfer-Encoding` alone, but requires the connection to close afterwards either way. Rejecting outright is simpler and safer.
- **Normalise before comparing.** Lowercase coding tokens, collapse internal whitespace, split on commas, and reject anything whose final coding is not `chunked`.
- **Keep a single parser in the chain.** Run every hop through one vetted parser, or terminate HTTP/1.1 and re-emit HTTP/2 (or vice versa) instead of relaying raw bytes.
- **Strip hop-by-hop fields.** `Connection`, `Keep-Alive`, `Proxy-Connection`, `Transfer-Encoding`, and `Upgrade` are connection-specific (RFC 9110 Section 7.6.1). A proxy forwarding them creates a second source of truth. `Via` must be added.
- **Prefer Content-Length over chunked when you know the size.** Some gateways in front of you reject chunked with `411`.

Run `curl` with a body you fully control to check whether a hop accepts `Transfer-Encoding`:

```bash
curl -v --http1.1 --raw \
  -H 'Transfer-Encoding: chunked' \
  -H 'Content-Length: 5' \
  --data-binary $'0\r\n\r\nGET /admin HTTP/1.1\r\n\r\n' \
  http://front.example.com/
```

If the response is `400` and the connection closes, your front end is doing the right thing. Anything else means the deployment is exposed.

## HTTP/1.1 connections: persistence, concurrency, pipelining

HTTP/1.1 connections are persistent by default (RFC 9112 Section 9.3). A connection closes when either side sends `Connection: close`, or when either side decides to close. Because HTTP/1.1 allows only one outstanding request per connection, clients scale by opening several connections, and browsers enforce a per-origin limit on concurrent HTTP/1.1 connections.

Pipelining (RFC 9112 Section 9.3.2) sends request 2 before response 1 arrives. Responses still arrive in order, so a slow first request blocks every later response on that connection. It is effectively dead on the public internet because intermediaries that buffer or retry break the ordering assumptions, and because the added failure modes were never worth the gain. RFC 9113 cites this head-of-line blocking as the motivation for HTTP/2.

```abnf
Connection   = #connection-option
connection-option = token
```

`Connection` names the other fields that are hop-by-hop. If `Connection: X-Custom` appears, `X-Custom` is also hop-by-hop and must be stripped by intermediaries. Failing to do so is the classic cause of a header that disappears after one proxy hop.

## HTTP/2 framing

HTTP/2 (RFC 9113) replaces the text framing with binary frames on a single multiplexed TCP connection. Every frame starts with a fixed nine-octet header.

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                 Length (24)                   |    Type (8)     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Flags (8)   |   Reserved (1)  |         Stream Identifier (31)|
+-----------------------------------------------+-+-+-+-+-+-+-+-+
|                   Frame Payload (...)         ...
+--------------------------------------------------...
```

| Type | Id | Purpose |
| --- | --- | --- |
| `DATA` | `0x0` | Request or response content |
| `HEADERS` | `0x1` | Field section, and optional trailers |
| `PRIORITY` | `0x2` | Legacy priority signal, deprecated by RFC 9113 |
| `RST_STREAM` | `0x3` | Abruptly terminate one stream |
| `SETTINGS` | `0x4` | Connection parameters; must be acknowledged |
| `PUSH_PROMISE` | `0x5` | Reserved a server-initiated stream |
| `PING` | `0x6` | Liveness and RTT measurement |
| `GOAWAY` | `0x7` | Graceful connection shutdown |
| `WINDOW_UPDATE` | `0x8` | Grant flow-control credit |
| `CONTINUATION` | `0x9` | Rest of a field block |

Stream rules that surprise people:

- Stream identifiers are unsigned 31-bit integers. **Client-initiated streams are odd, server-initiated streams are even**, and zero is reserved for connection-level frames. Identifiers are strictly increasing and never reused; opening a higher-numbered stream implicitly closes skipped lower ones.
- Field names must be lowercase in HTTP/2. Uppercase in an HTTP/2 field block is malformed.
- A field block must be sent as a contiguous run of `HEADERS` (or `PUSH_PROMISE`) plus zero or more `CONTINUATION` frames, with nothing interleaved, and the final frame carrying `END_HEADERS`. An endpoint that receives frames out of order here has a compression-state desync and must fail the connection with `COMPRESSION_ERROR`.
- Only `DATA` frames consume flow-control credit. The initial window is 65535 octets per stream and per connection; the maximum is 2^31-1. `WINDOW_UPDATE` carries a 31-bit increment, so a receiver must not send a zero increment. An endpoint that does not read and process frames promptly can deadlock itself by withholding the `WINDOW_UPDATE` frames that unblock its own peer.
- Default `SETTINGS_MAX_FRAME_SIZE` is 16384 octets of payload, excluding the nine-octet header. Values from 2^14 to 2^24-1 are legal.
- `ENABLE_PUSH` defaults to 1, and RFC 9113 removed meaningful server push from the required behaviour. Most clients set it to 0.

Connection error codes (RFC 9113 Section 7): `NO_ERROR` `0x0`, `PROTOCOL_ERROR` `0x1`, `INTERNAL_ERROR` `0x2`, `FLOW_CONTROL_ERROR` `0x3`, `SETTINGS_TIMEOUT` `0x4`, `STREAM_CLOSED` `0x5`, `FRAME_SIZE_ERROR` `0x6`, `REFUSED_STREAM` `0x7`, `CANCEL` `0x8`, `COMPRESSION_ERROR` `0x9`, `CONNECT_ERROR` `0xa`, `ENHANCE_YOUR_CALM` `0xb`, `INADEQUATE_SECURITY` `0xc`, `HTTP_1_1_REQUIRED` `0xd`.

See `references/http2-framing.md` for the full SETTINGS registry, the stream state machine, HPACK details, and a byte-level worked example.

## HTTP/3 over QUIC

HTTP/3 (RFC 9114) maps HTTP onto QUIC (RFC 9000), with TLS 1.3 handled by QUIC-TLS (RFC 9001). The transport change removes TCP head-of-line blocking: a stream that loses a packet does not stall other streams on the same connection.

```text
ALPN protocol identifier: h3
Discovery: Alt-Svc response header, or an h3 URI scheme, or SRV/ HTTPS records.
```

- Each request/response pair is a QUIC bidirectional stream. Aborting a request is `RESET_STREAM` on that stream only.
- Control and push streams are unidirectional streams whose first octet is a stream type: `0x00` for control, `0x01` for push. Each side opens exactly one control stream and sends `SETTINGS` as the first frame on it.
- HTTP/3 defines its own frames (`DATA` `0x0`, `HEADERS` `0x1`, `CANCEL_PUSH` `0x3`, `SETTINGS` `0x4`, `PUSH_PROMISE` `0x5`, `GOAWAY` `0x7`, `MAX_PUSH_ID` `0xd`) and its own error codes starting at `0x0100` (`H3_NO_ERROR`, `H3_GENERAL_PROTOCOL_ERROR`, `H3_INTERNAL_ERROR`, and so on).
- Connection migration is inherited from QUIC: a connection ID changes as the client's address changes, so a mobile client that moves networks keeps its streams. QUIC probes for an alternate path before switching, and only the new address is proven, not the old one, which is why a QUIC migration cannot move to a fully unverified network path.
- 0-RTT exists at the transport layer (RFC 9001 early data) with the HTTP mapping in RFC 8470. Early data is replayable by definition. Only send idempotent requests in it, and treat any early-data response as untrusted until handshake confirmation.
- `Upgrade` to another protocol is not defined for HTTP/3. WebSockets over HTTP/3 uses the extended `CONNECT` method instead (RFC 9114 Section 4.4).

See `references/http3-quic.md` for the stream mapping, `Alt-Svc` advertisement, and migration behaviour.

## Upgrade and CONNECT

The HTTP/1.1 `Upgrade` mechanism (RFC 9110 Section 7.8) switches protocols on an existing connection. The client sends `Upgrade` and `Connection: upgrade`; the server replies `101 Switching Protocols`, after which the connection is no longer HTTP.

```text
GET /chat HTTP/1.1
Host: example.com
Connection: Upgrade
Upgrade: websocket
Sec-WebSocket-Version: 13
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==

HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```

`CONNECT` (RFC 9110 Section 9.3.6) builds a tunnel instead. A `2xx` response to `CONNECT` means everything after the blank line is opaque tunnel bytes; clients must ignore any `Content-Length` or `Transfer-Encoding` in that response.

## Intermediaries must reframe correctly

A reverse proxy is both a server and a client. Every hop owns framing entirely. When you translate between HTTP/1.1 and HTTP/2 or HTTP/3 you must:

- Decode the body (chunked to octets) and re-encode it for the next hop, setting exactly one framing header.
- Strip hop-by-hop fields and connection-specific fields. For HTTP/2 targets this means removing `Connection`, `Keep-Alive`, `Proxy-Connection`, `Transfer-Encoding`, and `Upgrade` (RFC 9113 Section 8.2.2). `TE` may appear only with the value `trailers`.
- Remove any duplicate `Content-Length` before forwarding.
- Rewrite `Host` to the `:authority` pseudo-header and vice versa.
- Re-evaluate body size limits after decompression. A 1 KB compressed body can expand to a very large payload, and the limit that matters is on decompressed bytes.
- Forward trailers deliberately, remembering that a proxy may not merge them into headers.

Validate field names and values strictly. RFC 9113 Section 8.2.1 requires, at minimum, that a field name contain nothing in the ranges `0x00-0x20`, `0x41-0x5a`, or `0x7f-0xff`, contain no colon (except pseudo-headers), and that a field value contain no NUL, CR, or LF and neither start nor end with SP or HTAB. It states plainly that failure to validate fields can be exploited for request smuggling.

## Diagnostic commands

```bash
# Show exactly what the client sends and what the server returns, including headers.
curl -v --http1.1 https://example.com/ -o /dev/null

# Force a specific HTTP version and see the negotiated one.
curl --http1.1 -s -o /dev/null -w '%{http_version}\n' https://example.com/
curl --http2   -s -o /dev/null -w '%{http_version}\n' https://example.com/
curl --http3   -s -o /dev/null -w '%{http_version}\n' https://example.com/

# Inspect a raw chunked response body without curl re-framing it.
curl -s --raw -H 'Accept-Encoding: identity' https://example.com/ | xxd | head -20

# Watch frames on an HTTP/2 connection (h2, nghttp2 and nghttpd are separate packages).
nghttp -v https://example.com/

# TLS and ALPN details.
openssl s_client -alpn h2,http/1.1 -connect example.com:443 -servername example.com </dev/null

# Dump the first bytes of any TCP stream (needs privileges on Linux/macOS).
sudo tcpdump -A -s0 'tcp port 80' -w http.pcap
```

## Checklist

- Does every hop in your chain agree, byte for byte, on where each message ends?
- Do you reject requests carrying both `Content-Length` and `Transfer-Encoding`, and close the connection?
- Do you reject whitespace before the colon in field names, and handle `obs-fold` explicitly?
- Are hop-by-hop fields stripped and `Via` appended at every proxy?
- Is the body read with a bounded, overflow-safe chunk-size parser?
- Are compressed bodies size-limited after decompression?
- If you terminate HTTP/2 or HTTP/3, do you enforce the lowercase field names, no-`Transfer-Encoding` rule, and the `CONTINUATION` contiguity rule?
- Do you send `Transfer-Encoding: chunked` only to HTTP/1.1 peers, and never alongside `Content-Length`?
- Are trailers forwarded deliberately rather than silently merged?

## Common mistakes

**Sending both `Content-Length` and `Transfer-Encoding`.** Symptom: intermittent `400` from one hop, cache poisoning, or requests bleeding into the next request on the same connection. Root cause: an estimate of the length was left in place when streaming was enabled. Fix: pick one, and close the connection if both ever arrive.

**Accepting `Transfer-Encoding: chunked, identity` or malformed variants.** Symptom: the parser picks the first coding while a downstream parser picks the last, so the byte offsets diverge. Fix: normalise, and require `chunked` to be the final coding or the message is invalid.

**Treating a request with no framing header as having a body until the connection closes.** Symptom: the server waits forever or consumes the next request as body. Fix: per RFC 9112 point 7, a request body with neither header has length zero.

**Chunk-size overflow.** Symptom: hangs or truncated bodies with multi-gigabyte uploads. Root cause: parsing the hexadecimal chunk size into a 32-bit integer. Fix: parse into a 64-bit or wider type and apply your own limit.

**Sending `Transfer-Encoding` to an HTTP/2 backend.** Symptom: `PROTOCOL_ERROR` or connection close. Fix: RFC 9113 Section 8.2.2 makes any connection-specific field in an HTTP/2 message malformed.

**Leaving `Set-Cookie`-style comma semantics unhandled in a proxy.** Symptom: cookies disappear or merge after a proxy hop. Fix: join repeated field lines with `", "` except for fields defined as comma-separated lists, where joining with `","` without a space is required.

**Streaming the response to the client while buffering the request from the upstream.** Symptom: the request body is truncated because the write side filled. Fix: fully drain the upstream request before forwarding the downstream response, or use a proxy that handles it.

## Anti-patterns

- Treating HTTP as "headers plus a body" and skipping the ordered precedence in RFC 9112 Section 6.3.
- Fixing a smuggling alert by blocking one payload pattern instead of fixing the parser disagreement.
- Writing a custom HTTP parser to "be more tolerant" for one legacy client. Tolerance is the vulnerability.
- Pipelining HTTP/1.1 requests in production.
- Enabling server push on HTTP/2. RFC 9113 deprioritised it and clients disable it by default.
- Opening HTTP/2 streams without honouring the peer's `SETTINGS_MAX_CONCURRENT_STREAMS`, causing `REFUSED_STREAM` churn.
- Assuming `Upgrade` works everywhere. It does not apply to HTTP/2 or HTTP/3, and RFC 9113 deprecates the `h2c` upgrade token.
- Sending a body over HTTP/2 and forgetting that intermediaries must not see `Transfer-Encoding` at all.

## References

- RFC 9110, HTTP Semantics - https://www.rfc-editor.org/rfc/rfc9110
- RFC 9111, HTTP Caching - https://www.rfc-editor.org/rfc/rfc9111
- RFC 9112, HTTP/1.1 - https://www.rfc-editor.org/rfc/rfc9112
- RFC 9113, HTTP/2 - https://www.rfc-editor.org/rfc/rfc9113
- RFC 9114, HTTP/3 - https://www.rfc-editor.org/rfc/rfc9114
- RFC 7541, HPACK: Header Compression for HTTP/2 - https://www.rfc-editor.org/rfc/rfc7541
- RFC 9000, QUIC: A UDP-Based Multiplexed and Secure Transport - https://www.rfc-editor.org/rfc/rfc9000
- RFC 9001, Using TLS to Secure QUIC - https://www.rfc-editor.org/rfc/rfc9001
- RFC 8470, Using Early Data in HTTP - https://www.rfc-editor.org/rfc/rfc8470
- RFC 7301, TLS Application-Layer Protocol Negotiation (ALPN) - https://www.rfc-editor.org/rfc/rfc7301
- RFC 9292, Binary Representation of HTTP Messages - https://www.rfc-editor.org/rfc/rfc9292
- MDN, HTTP/2 - https://developer.mozilla.org/en-US/docs/Web/HTTP/2
- MDN, HTTP headers reference - https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers
- IANA, HTTP parameters and registries - https://www.iana.org/assignments/http-parameters/http-parameters.xhtml
- OWASP, HTTP Request Smuggling Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Request_Smuggling_Cheat_Sheet.html