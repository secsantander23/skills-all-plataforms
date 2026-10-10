# HTTP/2 Framing Reference

Detail for `http-protocol-deep`. Everything here is defined in RFC 9113 (HTTP/2) and RFC 7541 (HPACK).

## Frame header layout

```text
+-----------------------------------------------+
|                 Length (24)                   |
+-----------------------------------------------+
|   Type (8)    |   Flags (8)   |
+-----------------------------------------------+
|  R (1) |         Stream Identifier (31)       |
+-----------------------------------------------+
|                   Frame Payload (..)          |
+-----------------------------------------------+
```

- `Length` is the payload length in octets, unsigned 24-bit. The nine-octet header is not counted. Values above 16384 (`2^14`) require the peer to have raised `SETTINGS_MAX_FRAME_SIZE`.
- `Reserved` must be sent unset and ignored on receipt.
- Stream identifier `0` means the frame applies to the whole connection.

## Flags per frame type

| Frame | Flags | Meaning |
| --- | --- | --- |
| `DATA` | `END_STREAM` `0x1`, `PADDED` `0x8` | `0x1` closes the sending side of the stream |
| `HEADERS` | `END_STREAM` `0x1`, `END_HEADERS` `0x4`, `PADDED` `0x8`, `PRIORITY` `0x20` | `END_HEADERS` marks the last fragment of the field block |
| `SETTINGS` | `ACK` `0x1` | `ACK` means this is an acknowledgement, carries no payload |
| `PING` | `ACK` `0x1` | `ACK` is a response to a non-ACK PING |
| `PUSH_PROMISE` | `END_HEADERS` `0x4`, `PADDED` `0x8` | Includes a promised stream id |
| `CONTINUATION` | `END_HEADERS` `0x4` | Carries the remainder of a field block |

Unused flags must be ignored on receipt and left unset when sending.

## SETTINGS registry

| Setting | Id | Default | Notes |
| --- | --- | --- | --- |
| `SETTINGS_HEADER_TABLE_SIZE` | `0x1` | 4096 | Max HPACK dynamic table size the sender will use |
| `SETTINGS_ENABLE_PUSH` | `0x2` | 1 | Clients set to 0; server push is deprecated in practice |
| `SETTINGS_MAX_CONCURRENT_STREAMS` | `0x3` | unlimited | Applies to streams the peer initiates |
| `SETTINGS_INITIAL_WINDOW_SIZE` | `0x4` | 65535 | Flow-control window for new streams; not retroactive |
| `SETTINGS_MAX_FRAME_SIZE` | `0x5` | 16384 | Range `2^14` to `2^24-1`, payload only |
| `SETTINGS_MAX_HEADER_LIST_SIZE` | `0x6` | unlimited | Advisory; advisory means you may ignore it |

The preface `SETTINGS` must be acknowledged by the peer. An endpoint may send new `SETTINGS` at any time; the receiver must apply them on receipt and acknowledge them. Reducing `SETTINGS_HEADER_TABLE_SIZE` is known to be poorly interoperable; RFC 9113 advises against it.

## Stream identifiers and states

Identifiers are strictly increasing unsigned 31-bit integers. Odd numbers are client-initiated, even numbers server-initiated. A stream may never be reused, so a very long-lived connection eventually exhausts the range; a server that runs out sends `GOAWAY` and the client reconnects.

```text
idle -> reserved (local)   : server sends PUSH_PROMISE
idle -> reserved (remote) : client receives PUSH_PROMISE
idle -> open              : client sends HEADERS, or server receives HEADERS
open -> half-closed (local)  : endpoint sends END_STREAM
open -> half-closed (remote) : peer sends END_STREAM
half-closed (local)  -> closed : peer sends END_STREAM, or RST_STREAM
half-closed (remote) -> closed : we send END_STREAM, or RST_STREAM
any      -> closed          : RST_STREAM
```

Receiving a frame on an `idle` stream, or on a server-initiated stream as a client, is a connection error `PROTOCOL_ERROR`. In `half-closed (remote)`, receiving anything other than `WINDOW_UPDATE`, `PRIORITY`, or `RST_STREAM` is a stream error `STREAM_CLOSED`.

## Flow control

Two windows: one per stream, one for the connection. Only `DATA` payloads count, not frame headers and not other frame types. Credit is granted with `WINDOW_UPDATE`, whose 31-bit increment must be non-zero; because the increment is bounded, a receiver with a large window must send several updates. The window may be temporarily reduced below zero and must never exceed 2^31-1.

The deadlock to avoid: an endpoint that stops reading its TCP receive buffer. `WINDOW_UPDATE` frames sit behind unread data, so a lazy reader throttles its own peer.

## HPACK (RFC 7541)

A header block is a binary encoding with three states per direction:

1. A static table of 61 common name/value pairs, fixed and shared by all endpoints.
2. A dynamic table, per direction, holding recently used pairs, bounded by `SETTINGS_HEADER_TABLE_SIZE` and starting at 4096 octets. Size accounting is name length plus value length plus 32 octets of overhead.
3. A Huffman code for strings, always available and always allowed.

Representation types use a prefix bit:

| Prefix | Type |
| --- | --- |
| `1xxxxxxx` | Indexed header field, 7-bit index |
| `01xxxxxx` | Literal with incremental indexing, 6-bit index prefix |
| `001xxxxx` | Literal without indexing, 4-bit index prefix |
| `0001xxxx` | Literal never indexed, 4-bit index prefix |
| `0000xxxx` | Dynamic table size update, 5-bit prefix |

Security consequences that matter in operations:

- HPACK is stateful per connection. A desynchronised encoder and decoder do not fail loudly; they produce wrong header values. There is no checksum on a header block.
- The dynamic table is a compression side channel. Header values that vary per request (identifiers, tokens) can leak through table synchronisation timing. Two mitigations: mark sensitive values "never index" (`0001` prefix), and keep sensitive values at the end of the header section since the whole section must be contiguous anyway.
- A receiver that skips HPACK decompression for a discarded field block will corrupt its own dynamic table. RFC 9113 requires decompressing even frames you intend to discard, and failing the connection with `COMPRESSION_ERROR` if you cannot.

## HTTP/2 message mapping

Pseudo-header fields are not normal fields. Requests use `:method`, `:scheme`, `:authority`, `:path`, plus regular fields; responses use `:status` plus regular fields. All names must be lowercase. Pseudo-headers must precede regular fields. Unknown pseudo-headers must be ignored; a recognised pseudo-header sent on the wrong message type is malformed.

```text
:method    = GET
:scheme    = https
:authority = api.example.com
:path      = /v1/orders?state=open
accept     = application/json
```

`TE` may appear in a request with no value other than `trailers`. `Cookie` may be split across multiple fields to avoid compression-crushing it into a single Huffman blob; receivers join them with `"; "`.

Connection-specific fields (`Connection`, `Keep-Alive`, `Proxy-Connection`, `Transfer-Encoding`, `Upgrade`) make the message malformed. A client that receives `GOAWAY` with last-stream-id `N` must treat streams above `N` as never having been processed and may retry them on a new connection; streams at or below `N` must not be retried, which is exactly why a retry loop keyed on "did I see a response" is unsafe.

## Worked example: a request with a padded DATA frame

```text
SETTINGS (type 0x4, flags 0x0, stream 0) length 0
HEADERS (type 0x1, flags 0x4 END_HEADERS, stream 1) length 5
DATA   (type 0x0, flags 0x1 END_STREAM | 0x8 PADDED, stream 1) length 9
```

The `DATA` payload is: pad length octet `0x04`, four pad octets that receivers MUST ignore, then the four content octets `body`.

## Prioritization

RFC 9113 Section 5.3.1 declares the RFC 7540 priority signalling scheme unsuccessful: it was complex, inconsistently implemented, and largely ignored. The `PRIORITY` frame is retained for interoperability but the bulk of its semantics are deprecated. Prioritization beyond this is expressed with the newer extensible scheme defined in `draft-ietf-httpbis-priority`, which is still an Internet-Draft rather than an RFC. Check its current status before designing around it.

## Related reading

- RFC 9113 - https://www.rfc-editor.org/rfc/rfc9113
- RFC 7541 - https://www.rfc-editor.org/rfc/rfc7541
- RFC 8446 (TLS 1.3) - https://www.rfc-editor.org/rfc/rfc8446
- RFC 9113 Appendix B lists the notable changes from RFC 7540 - https://www.rfc-editor.org/rfc/rfc9113#appendix-B
- nghttp2 documentation - https://nghttp2.org/documentation/
- MDN HTTP/2 - https://developer.mozilla.org/en-US/docs/Web/HTTP/2