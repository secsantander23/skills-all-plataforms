# Request Smuggling Reference

Detail for `http-protocol-deep`. Ground rules are in RFC 9112 Section 6 (Message Body) and Section 11.2 (Request Smuggling); the HTTP/2-side requirements are in RFC 9113 Section 8.1.1 (Malformed Messages) and Section 8.2 (HTTP Fields).

## The mechanism in one paragraph

An intermediary chain reuses one backend connection for many front-end requests. Each hop must independently decide where a request ends. If the front end and the back end disagree by even one byte, the front end's notion of the request boundary and the back end's diverge permanently. The attacker then crafts a request whose *declared* length and *actual* structure differ so that the bytes at the end of request one become request two, executed as if the attacker had sent it directly.

## CL.TE

Front end uses `Content-Length`. Back end uses `Transfer-Encoding: chunked`.

```text
POST /api/orders HTTP/1.1\r\n
Host: shop.example.com\r\n
Content-Type: application/x-www-form-urlencoded\r\n
Content-Length: 65\r\n
Transfer-Encoding: chunked\r\n
\r\n
0\r\n
\r\n
GET /internal/admin HTTP/1.1\r\n
Host: shop.example.com\r\n
\r\n
```

Front end reads 65 octets and forwards one request. Back end decodes `0\r\n\r\n` as the end of the body, treats the remaining bytes as the next request on the pooled connection, and routes it internally.

## TE.CL

Front end uses `Transfer-Encoding`. Back end uses `Content-Length`.

```text
POST /api/orders HTTP/1.1\r\n
Host: shop.example.com\r\n
Content-Type: application/x-www-form-urlencoded\r\n
Content-Length: 4\r\n
Transfer-Encoding: chunked\r\n
\r\n
5e\r\n
GET /internal/admin HTTP/1.1\r\n
Host: shop.example.com\r\n
Content-Length: 0\r\n
\r\n
0\r\n
\r\n
```

The front end strips the chunked framing and emits the decoded body upstream; the back end stops after 4 octets of the decoded content and leaves the rest buffered as a second request.

## TE.TE and header obfuscation

Both ends see `Transfer-Encoding`, but disagree about what the value means. Observed techniques, all of which a strict parser rejects outright:

| Technique | Why strict parsers disagree |
| --- | --- |
| Duplicate header lines | One reader takes the first, another takes the last |
| Obfuscated token, for example `Transfer-Encoding: chunked`, `xchunked`, `chunked `, ` chunked`, `cHuNkEd` | Readers differ on which substrings count as a coding and whether case and whitespace are significant |
| Comma variations | `chunked,chunked`, `chunked , chunked`, `chunked,,chunked` |
| Vertical tab or form feed as intra-value whitespace | Some readers normalise, some do not |
| Line folding inside the value | Obsolete line folding, deprecated by RFC 9112 Section 5.2 |

A parser that accepts any of these is already non-conformant. The fix is a single strict parser, not a blocklist.

## H2.CL and H2.TE across a downgrade proxy

An HTTP/2 front end talking to an HTTP/1.1 back end must translate. Three translation errors create the same primitive.

- **Forwarding `Transfer-Encoding` into HTTP/2.** RFC 9113 Section 8.2.2 makes any connection-specific field in an HTTP/2 message malformed; a compliant HTTP/2 endpoint terminates the connection.
- **Forwarding duplicate or inconsistent `Content-Length`.** HTTP/2 carries `content-length` as an ordinary field, and it can appear more than once unless the implementation merges field lines correctly. Two values, or one value whose digits differ from the streamed body, produce a split mismatch on the HTTP/1.1 side.
- **Not stripping hop-by-hop fields.** RFC 9113 Section 8.2.2 names `Connection`, `Proxy-Connection`, `Keep-Alive`, `Transfer-Encoding`, and `Upgrade`. `TE` is allowed but only with the value `trailers`.

The reverse direction is equally dangerous: an HTTP/2 field value containing a character that is legal in HPACK but illegal in HTTP/1.1. RFC 9113 Section 8.2.1 lists the minimum validation every endpoint must perform:

- Field names must not contain octets in `0x00-0x20`, `0x41-0x5a`, or `0x7f-0xff`. This excludes all non-visible ASCII, space, and uppercase.
- Except for pseudo-headers, field names must not contain a colon.
- Field values must not contain NUL, LF, or CR anywhere.
- Field values must not begin or end with SP or HTAB.

RFC 9113 states explicitly that failing to validate fields "can be exploited for request smuggling attacks", which is what makes this a protocol requirement rather than a lint.

## Response queue poisoning

The mirror image. If the front end and back end disagree about where a *response* ends, the front end attributes response bytes to the wrong client. Consequences include a secret from one user's response being delivered to another, and a cache being populated with content meant for someone else. RFC 9112 Section 6.3 point 5 specifies the proxy behaviour: close the upstream connection, discard the response, and send `502 Bad Gateway`.

## Response splitting and CRLF injection

Related but distinct: smuggling hides a request from the front end; splitting forges a response boundary. Echoing unvalidated user input into a header or into a chunk-size line lets an attacker terminate the response early and inject a second, entirely attacker-controlled response.

```text
HTTP/1.1 200 OK\r\n
X-Echo: value\r\n
Injected-Header: yes\r\n
\r\n
```

Sanitise every value that becomes a header name, header value, chunk size, or status line component. Reject CR and LF anywhere they cannot legally appear.

## Defensive checklist

1. Reject `400` and close when both `Content-Length` and `Transfer-Encoding` are present.
2. Require the final transfer coding to be `chunked`; reject `400` otherwise.
3. Reject whitespace before the colon in a field name; strip it from responses.
4. Reject or normalise `obs-fold` before interpreting a field value.
5. Reject duplicate `Content-Length` unless all values are identical.
6. Accept only one coding list parse: lowercase, trim, split on comma, drop empty entries.
7. Never forward hop-by-hop fields; always append `Via`.
8. Prefer terminating and re-emitting over relaying raw bytes between protocol versions.
9. Enforce maximum request line, header section, header count, and body size before any allocation proportional to attacker input.
10. Alert on two `Content-Length` headers, on `Transfer-Encoding` in a request to a server that never advertised chunked support, and on any request that resets a pooled backend connection mid-stream.

## Testing

```bash
# Both headers present: expect 400 and a closed connection.
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Transfer-Encoding: chunked' \
  -H 'Content-Length: 4' \
  --data-binary $'0\r\n\r\n' http://target.example.com/

# Whitespace before the colon: expect 400 from a conformant server.
printf 'GET / HTTP/1.1\r\nHost : target.example.com\r\nConnection: close\r\n\r\n' | nc target.example.com 80

# Obsolete line folding: expect 400 or a normalised value, never silent acceptance.
printf 'GET / HTTP/1.1\r\nHost: target.example.com\r\nX-A: one\r\n\ttwo\r\nConnection: close\r\n\r\n' | nc target.example.com 80
```

A controlled CL.TE probe against a system you own can confirm the behaviour end to end:

```bash
curl -sS --path-as-is http://target.example.com/ \
  -H 'Host: target.example.com' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Content-Length: 42' \
  -H 'Transfer-Encoding: chunked' \
  --data-binary $'0\r\n\r\nGET /smuggle-probe HTTP/1.1\r\nHost: target.example.com\r\n\r\n'
```

Run this only against infrastructure you own or have written authorisation to test. Treat any response other than a `400` with a closed connection as a finding.

## References

- RFC 9112 Section 6 (Message Body) - https://www.rfc-editor.org/rfc/rfc9112#section-6
- RFC 9112 Section 11.2 (Request Smuggling) - https://www.rfc-editor.org/rfc/rfc9112#section-11.2
- RFC 9113 Section 8.1.1 (Malformed Messages) - https://www.rfc-editor.org/rfc/rfc9113#section-8.1.1
- RFC 9113 Section 8.2 (HTTP Fields) - https://www.rfc-editor.org/rfc/rfc9113#section-8.2
- PortSwigger Web Security Academy, HTTP request smuggling - https://portswigger.net/web-security/http-request-smuggling
- OWASP, HTTP Request Smuggling Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Request_Smuggling_Cheat_Sheet.html