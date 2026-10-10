# HTTP/3 and QUIC Reference

Detail for `http-protocol-deep`. Sources: RFC 9114 (HTTP/3), RFC 9000 (QUIC), RFC 9001 (QUIC-TLS), RFC 8470 (Using Early Data in HTTP), RFC 9112 Section 7.8 (Upgrade).

## Transport, not a new HTTP

HTTP/3 keeps the semantics of RFC 9110 unchanged. What changes is the transport: messages become QUIC streams instead of text on a TCP connection. Three consequences fall out immediately.

1. **No transport-level head-of-line blocking.** A QUIC stream that loses a packet does not stall other streams. TCP would have stalled all of them.
2. **Per-stream cancellation.** Aborting a request is `RESET_STREAM` on that stream. There is no connection-wide abort.
3. **Built-in encryption and integrity.** QUIC always uses TLS 1.3 (RFC 9001). There is no cleartext mode.

## Stream mapping

| QUIC construct | HTTP/3 use |
| --- | --- |
| Client-initiated bidirectional stream | One request and its response |
| Server-initiated bidirectional stream | Reserved for extended `CONNECT` in current deployments |
| Unidirectional stream, type `0x00` | Control stream: carries `SETTINGS` and `GOAWAY` |
| Unidirectional stream, type `0x01` | Server push |
| Unreliable datagram | Available for experiments; HTTP/3 does not define a mapping for it in RFC 9114 |

Each endpoint opens at most one control stream and must send its `SETTINGS` frame as the first frame on it. Receiving a second control stream is a connection error (`H3_STREAM_CREATION_ERROR`). All stream identifiers issued by an endpoint in one connection type must be sequential.

Field sections in HTTP/3 are encoded with QPACK, not HPACK. QPACK's static and dynamic tables are per connection, and a decoder can evict dynamic table entries at any time, which is why QPACK adds explicit "dynamic table blocked" and "dynamic table capacity update" instructions. Check the current IETF status of the QPACK specification before implementing it yourself; most stacks already do.

## HTTP/3 frame types

| Frame | Id | Section |
| --- | --- | --- |
| `DATA` | `0x0` | RFC 9114 Section 7.2.1 |
| `HEADERS` | `0x1` | RFC 9114 Section 7.2.2 |
| `CANCEL_PUSH` | `0x3` | RFC 9114 Section 7.2.3 |
| `SETTINGS` | `0x4` | RFC 9114 Section 7.2.4 |
| `PUSH_PROMISE` | `0x5` | RFC 9114 Section 7.2.5 |
| `GOAWAY` | `0x7` | RFC 9114 Section 7.2.6 |
| `MAX_PUSH_ID` | `0xd` | RFC 9114 Section 7.2.7 |

Unknown frame types must be ignored and discarded. The `RESERVED_STREAM_TYPE` stream type (`0x02` and other even values with the two high bits set) signals an error when received.

## HTTP/3 error codes (RFC 9114 Section 8.1)

| Code | Id | Meaning |
| --- | --- | --- |
| `H3_NO_ERROR` | `0x0100` | Graceful close, or peer asked to stop sending |
| `H3_GENERAL_PROTOCOL_ERROR` | `0x0101` | Unspecified protocol problem |
| `H3_INTERNAL_ERROR` | `0x0102` | Internal condition in the implementation |
| `H3_STREAM_CREATION_ERROR` | `0x0103` | Stream refused or limit exceeded |
| `H3_CLOSED_CRITICAL_STREAM` | `0x0104` | A stream the peer relies on was closed |
| `H3_FRAME_UNEXPECTED` | `0x0105` | Frame on the wrong stream type or out of order |
| `H3_FRAME_ERROR` | `0x0106` | Frame could not be processed |
| `H3_EXCESSIVE_LOAD` | `0x0107` | Peer is generating excessive load |
| `H3_ID_ERROR` | `0x0108` | Identifier was inconsistent |
| `H3_SETTINGS_ERROR` | `0x0109` | Settings frame could not be processed |
| `H3_MISSING_SETTINGS` | `0x010a` | Control stream opened without `SETTINGS` |
| `H3_REQUEST_REJECTED` | `0x010b` | Server rejected the request without processing it |
| `H3_REQUEST_CANCELLED` | `0x010c` | Request cancelled |
| `H3_REQUEST_INCOMPLETE` | `0x010d` | Stream ended before the request was complete |
| `H3_MESSAGE_ERROR` | `0x010e` | Message could not be processed |
| `H3_CONNECT_ERROR` | `0x010f` | `CONNECT` establishment failed |
| `H3_VERSION_FALLBACK` | `0x0110` | Version negotiation failed |

`H3_REQUEST_REJECTED` has a precise meaning: the server reset the stream before any application code observed stream data, so the request was never processed and may be safely retried. Once any stream data has reached a higher layer, the request is "processed" and a reset is not a retry signal.

## Discovery and negotiation

- ALPN identifier is the string `h3` (serialised as the octets `0x68 0x33`). RFC 9114 Section 3.1 also describes `h3-29` for the earlier draft-29 wire format, which is now obsolete.
- Servers advertise HTTP/3 with an `Alt-Svc` response header on an HTTP/1.1 or HTTP/2 response. The client then attempts HTTP/3 to the advertised host and treats failure as non-fatal.

```text
HTTP/2 200
alt-svc: h3=":443"; ma=2592000
cache-control: max-age=86400
```

`ma` is the advertised lifetime in seconds; clients use it to decide whether to probe again. `Alt-Svc` on the response means the current connection was fine, so the client must not break it. Some clients require a second, separate response carrying `Alt-Svc` before they switch, so do not expect the very first response to trigger adoption.

- HTTP/3 requires UDP reachability to the port. Middleboxes and enterprise networks routinely block UDP 443 even when they pass TCP 443, so an HTTP/3 listener on the same host can be fully unusable.
- `Upgrade` does not exist in HTTP/3. RFC 9113 deprecated the `h2c` upgrade token for HTTP/2, and RFC 9114 Section 4.5 defines no upgrade for HTTP/3. Long-lived protocols ride on the extended `CONNECT` method, which RFC 9114 Section 4.4 discusses; read that section and the `:protocol` pseudo-header handling it describes before implementing it.

## Connection migration

Migration is a QUIC property (RFC 9000 Section 9), inherited by HTTP/3 without changes to the HTTP layer.

- QUIC uses connection IDs, not the network path, to identify a connection. A client that changes address continues the same connection by sending packets with the same connection ID from the new address.
- On detecting an address change, the endpoint sends a probe on the new path and migrates only after the probe is validated. Only the new address is authenticated, not the old one, so an attacker cannot force a migration to an address they control, but a migration to a *less* trustworthy network is possible and is why HTTP/3 deployments on untrusted networks still rely on application-layer encryption for the payload.
- Migration does not work across a change of network interface that also changes the IP version without NAT support, and it cannot cross a stateful firewall that pins the session.
- Migration makes server-side connection accounting harder: a "user" can hold several connection IDs across addresses. Rate limiting by connection identity must not assume one IP equals one session.

## 0-RTT and early data

- QUIC 0-RTT (RFC 9001 Section 4.6) lets a client send application data during the handshake. The mapping onto HTTP is RFC 8470.
- Early data is not protected against replay. A server must treat every early-data request as potentially a replay of an earlier one.
- Send only idempotent requests in early data: `GET`, `HEAD`, and requests that carry no side effects. A non-idempotent request in 0-RTT can be executed twice.
- The replay window is typically bounded (commonly the anti-replay limit enforced by TLS tickets), but the bound is a server configuration choice, not a protocol guarantee.
- A response produced from early data is not authenticated from the client's point of view until the handshake completes. Do not treat it as final.

## Observability

```bash
# Does the server support h3 at all?
openssl s_client -alpn h3 -connect example.com:443 -servername example.com </dev/null

# Check for Alt-Svc advertisement.
curl -sI https://example.com/ | grep -i alt-svc

# Try HTTP/3 directly and print the negotiated version.
curl --http3 -s -o /dev/null -w '%{http_version}\n' https://example.com/

# QUIC connection diagnostics, if a QUIC-aware tool is installed.
qlog --help
```

Verify which HTTP/3 client your stack links before relying on any of the above. Browser and client support for HTTP/3 is still uneven, and a failed HTTP/3 attempt must always fall back to HTTP/2 or HTTP/1.1 without user-visible error.

## Related reading

- RFC 9114, HTTP/3 - https://www.rfc-editor.org/rfc/rfc9114
- RFC 9000, QUIC - https://www.rfc-editor.org/rfc/rfc9000
- RFC 9001, Using TLS to Secure QUIC - https://www.rfc-editor.org/rfc/rfc9001
- RFC 8470, Using Early Data in HTTP - https://www.rfc-editor.org/rfc/rfc8470
- IETF Internet-Draft, WebTransport over HTTP/3 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http3/
- IETF Internet-Draft, WebTransport over HTTP/2 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http2/
- MDN, HTTP/3 - https://developer.mozilla.org/en-US/docs/Web/HTTP/3