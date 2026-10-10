---
name: Real-Time Protocols
description: Covers push and streaming protocols beyond WebSockets, including Server-Sent Events format and reconnection with Last-Event-ID, long polling, WebTransport concepts and tradeoffs, WebRTC data channels with offer/answer, ICE, STUN, and TURN, MQTT over WebSockets, and gRPC streaming, plus a selection guide. Use when choosing how to push updates to browsers, when SSE reconnects lose or replay events, when evaluating WebRTC or WebTransport, or when debugging stream disconnects and NAT traversal.
metadata:
  opencode/autoinvoke: true
---

# Real-Time Protocols

"Real-time" covers several unrelated mechanisms: unidirectional server-to-client streams, request/response kept open by the server, general-purpose bidirectional sockets, and peer-to-peer media paths. They differ in what they cost on the wire, on the server, and in the network. This skill helps you pick by constraint rather than by popularity.

## When to use this

- You need to push updates to a browser and are choosing between SSE and WebSocket.
- SSE clients reconnect and either miss events or replay everything; you need to fix the resume semantics.
- You are evaluating WebTransport or WebRTC and need to know what they actually buy you.
- Long polling is exhausting your connection pool or your per-origin HTTP/1.1 connection budget.
- You need bidirectional streaming with backpressure from many clients and want to compare gRPC streaming with WebSocket.
- You are behind NAT and need to reason about STUN, TURN, and connection migration.

## Quick start

A Server-Sent Events endpoint, with the fields that matter.

```python
# app/events.py
import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse


async def events(request: Request):
    last_event_id = request.headers.get("last-event-id", "0")

    async def stream():
        yield "retry: 3000\n\n"          # reconnect delay hint in milliseconds
        yield f"id: {last_event_id}\n"    # echo so the client has a resume cursor
        yield "event: ready\n"
        yield "data: " + json.dumps({"connected": True}) + "\n\n"

        async for message in broker.subscribe(request):
            if await request.is_disconnected():
                break
            yield f"id: {message.id}\n"
            yield "event: order.updated\n"
            yield "data: " + json.dumps(message.payload) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # stop nginx from buffering the response
        },
    )
```

```javascript
// Browser client
const source = new EventSource("/events");

source.addEventListener("order.updated", (event) => {
  console.log(event.lastEventId, JSON.parse(event.data));
});

source.addEventListener("error", () => {
  // The browser reconnects automatically and sends Last-Event-ID.
  // readyState is CONNECTING (0) while retrying, CLOSED (2) if it gave up.
  console.log("SSE readyState:", source.readyState);
});
```

```bash
# Watch the raw SSE stream, including headers, from a terminal.
curl -N -H 'Accept: text/event-stream' https://example.com/events
```

## Server-Sent Events (SSE)

SSE is defined in the HTML Living Standard, not in an RFC. It is a `text/event-stream` HTTP response that stays open, and the browser parses the body into events.

```text
: this is a comment line, ignored by the parser

retry: 5000
data: {"orderId":"A-1","status":"shipped"}
data: this second data line is concatenated with a newline
id: 1724
event: order.updated

data: a bare event with the default "message" type
```

Parsing rules, all of which surprise people:

- A blank line dispatches the accumulated event. Nothing is dispatched until the blank line arrives, so flushing matters.
- A line starting with `:` is a comment. Use it as a keep-alive heartbeat to defeat idle timeouts.
- Each `data:` line appends a value; multiple `data:` lines are joined with a newline character, not removed.
- `event:` sets the event type. Without it, the type is `message` and `addEventListener('message', ...)` fires.
- `id:` sets the last-event-id. The browser sends it as the `Last-Event-ID` request header on automatic reconnection. A line containing only `NUL` resets the id to the empty string.
- `retry:` sets the reconnection time in milliseconds. Sending it once at stream start is enough; it persists for that stream and across reconnections.
- A field name with no colon and an empty value is a field with an empty value. `data` alone is a `data` field with no value, equivalent to an empty data line.
- Lines are terminated by CRLF, CR, or LF, so a stray newline inside a field breaks it. Serialise with a proper SSE writer, not string concatenation.
- UTF-8 only. JSON payloads must be single-line, which is a natural fit.

### Server responsibilities for correct resume

SSE alone gives you resumption, not correctness. The server must keep a replay buffer keyed by id.

1. Give every event a monotonically increasing id in a durable or at-least-recent store.
2. On connect, read `Last-Event-ID` (or a `?lastEventId=` query parameter if you also need to support non-browser clients).
3. Replay everything after that id, then switch to the live stream.
4. If the requested id is older than your buffer, send a `reset` event and have the client resynchronise with a fresh fetch. Silently skipping the gap is a correctness bug that presents as missing updates.

```text
event: reset
data: {"reason":"buffer_expired"}

event: order.updated
data: {"orderId":"A-1","status":"shipped"}
```

### Practical constraints

- **One-way.** The browser sends data to the server over a separate normal request, or over a WebSocket. There is no client-to-server stream in SSE.
- **Same-origin only.** `EventSource` cannot be given CORS headers or custom headers, so a cross-origin SSE endpoint does not work in practice. Use a same-origin endpoint, or proxy it.
- **Six connections per origin.** Browsers limit concurrent HTTP/1.1 connections to six per origin (HTTP/2 removes this limit). This is why a single shared `EventSource` multiplexing event types beats one connection per widget.
- **No binary.** Payloads are UTF-8 text. Encode binary as base64 if you must.
- **Buffering.** Proxies buffer responses by default. Disable it per hop (`X-Accel-Buffering: no` for nginx, appropriate `Cache-Control: no-cache, no-transform`), or your events arrive in batches minutes apart.

## Long polling

Long polling is an ordinary request that the server holds open until it has something to send, then responds and the client immediately reopens. It is not a protocol; it is a usage pattern.

```javascript
let since = null;
let stopped = false;

async function poll() {
  while (!stopped) {
    const url = since === null ? "/events" : `/events?since=${encodeURIComponent(since)}`;
    const response = await fetch(url, { signal: AbortSignal.timeout(60_000) });
    const batch = await response.json();
    for (const event of batch) {
      since = event.id;
      handle(event);
    }
  }
}
```

Strengths: works everywhere, uses the same auth, caching, and infrastructure as your REST API, and survives proxies that break upgrades. Weaknesses: one connection and one in-flight request per stream, so a dashboard with many live widgets multiplies connections, and every event is a full HTTP round trip with headers.

Use long polling when you need broad compatibility, need bidirectional only occasionally, or your infrastructure blocks long-lived upgrades. Do not use it when you have thousands of concurrent subscribers or sub-second update targets.

## WebSocket

WebSocket (RFC 6455) is a persistent bidirectional connection that starts as an HTTP `Upgrade` handshake. It is the default real-time choice for a reason: it works through proxies, needs only one connection, and is bidirectional.

The framing is what matters when you read the bytes:

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-------+-+-------------+-------------------------------+
|F|R|R|R| opcode|M| Payload len |    Extended payload length    |
|I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
|N|V|V|O|       |S|             |   (if payload len==126/127)   |
| |1|2|3|       |K|             |                               |
+-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
```

- Client-to-server frames **must** be masked. Servers never mask. This exists to prevent cache poisoning by intermediaries.
- Control frames (opcode `0x8` close, `0x9` ping, `0xA` pong) must have payload length 125 or less and must not be fragmented.
- Opcodes: `0x0` continuation, `0x1` text, `0x2` binary, `0x8` close, `0x9` ping, `0xA` pong.
- Close codes: `1000` normal, `1001` going away, `1002` protocol error, `1003` unsupported data, `1007` invalid payload, `1008` policy violation, `1009` message too big, `1011` internal error, `1015` TLS handshake failure (never sent on the wire).

Use `1000` for a normal close and a specific application code in the 3000-3999 range when you have one. Close codes 1000 through 2999 are reserved for the protocol and its extensions.

## WebTransport

WebTransport is an IETF Internet-Draft, not yet an RFC. Verify the current status at the datatracker before relying on it. Two drafts exist: one mapping WebTransport onto HTTP/3 and one onto HTTP/2.

Concepts:

- A **session** replaces the single WebSocket connection. Multiple concurrent bidirectional streams can run over it.
- Streams can be **reliable and ordered** (like a TCP stream) or **unreliable with bounded retransmission** (like a datagram with a delivery deadline).
- **Datagrams** are unreliable, unordered messages with a size limit, useful for state updates where a late packet is worthless.
- It runs over HTTP/3 with the `webtransport` HTTP/3 setting, so a connection can carry ordinary HTTP/3 requests alongside WebTransport streams.
- WebTransport is unavailable over HTTP/1.1 and cannot be polyfilled. Browser support is real but narrower than WebSocket, and it requires HTTP/3 to be available, which enterprise networks frequently block.

Tradeoffs against WebSocket:

| | WebSocket | WebTransport |
| --- | --- | --- |
| Transport | HTTP/1.1 or HTTP/2 upgrade | HTTP/3 (QUIC) |
| Multiplexing | One stream per connection | Many streams per session |
| Unreliable send | Not built in; build a layer | Native datagrams |
| Per-stream backpressure | No | Yes |
| Connection migration | No | Yes, via QUIC |
| Browser availability | Universal | Narrower |
| Server-side stack | Mature everywhere | Fewer options |
| Works over HTTP/1.1 | Yes | No |

Choose WebTransport if you control both ends, need many concurrent low-latency streams, want unreliable delivery for state updates, and can accept the browser and infrastructure constraints. Otherwise WebSocket.

## WebRTC data channels

WebRTC gives you a peer-to-peer path. For data-only applications the pieces are:

```text
signalling (your own transport)  : SDP offer  ->  SDP answer
ICE (RFC 8445)                   : STUN (RFC 5389) to discover a candidate pair
                                  : TURN (RFC 5766) relay when direct fails
DTLS                              : authenticated encryption of the channel
SCTP over DTLS                   : reliable and unreliable streams
```

**Signalling is not standardised.** You choose the transport (often a WebSocket carrying JSON SDP) and you are responsible for matching session descriptions to peers, handling renegotiation, and tearing down. This is a substantial amount of your own code.

```javascript
// Offer/answer exchange. The signalling transport is yours to design.
const pc = new RTCPeerConnection({
  iceServers: [{ urls: "stun:stun.example.net:3478" }],
});

pc.createDataChannel("app", { ordered: true });

pc.onicecandidate = ({ candidate }) => {
  if (candidate) sendSignalling({ type: "ice", candidate });
};

const offer = await pc.createOffer();
await pc.setLocalDescription(offer);
sendSignalling({ type: "offer", sdp: offer.sdp });

// On the answering side:
pc.ondatachannel = ({ channel }) => {
  channel.onmessage = (event) => handle(event.data);
};
```

When to use WebRTC for data:

- Peer-to-peer, so the server relays signalling only and never touches the payload. This matters when the server's bandwidth and cost are the constraint.
- You need to traverse symmetric NAT that a client-server protocol would fail on, and TURN is available as a fallback.
- You are already doing media (voice or video) and want the data channel over the same connection.

Costs: a substantial amount of protocol work you did not want to own, a TURN relay you pay for when direct paths fail, and a connection setup measured in round trips across several protocols. For a browser-to-server data stream, this is almost always the wrong choice. For browser-to-browser media plus data, it is the right one.

## MQTT over WebSockets

Browsers cannot open a raw TCP socket, so MQTT-over-WebSockets carries MQTT packets inside WebSocket binary frames. The MQTT protocol is unchanged: topics, QoS, retained messages, wills, and sessions all behave identically.

```javascript
// mqtt.js over WebSockets. Verify current option names with the library docs.
const client = mqtt.connect("wss://broker.example.com:8084/mqtt", {
  protocolVersion: 5,
  clientId: `web-${crypto.randomUUID()}`,
  clean: true,
  reconnectPeriod: 2000,
});
```

Use MQTT over WebSockets when you already run an MQTT broker and want browser clients on the same topics and QoS as your devices. Otherwise the extra protocol is a cost with no benefit. See the MQTT reference in the `message-queue-protocols` skill for QoS semantics.

## gRPC streaming

gRPC is a binary RPC framework over HTTP/2 with four call types: unary, server-streaming, client-streaming, and bidirectional streaming. Streaming calls are a real-time option when both ends speak gRPC.

| Call type | Flow | Use for |
| --- | --- | --- |
| Unary | one request, one response | Normal request/response |
| Server-streaming | one request, many responses | Event streams from a service |
| Client-streaming | many requests, one response | Client uploads a stream |
| Bidirectional streaming | both directions interleaved | Interactive sessions, chat, sync |

Strengths: built-in flow control from HTTP/2 windows, typed contracts via protobuf, deadlines propagated across hops, and efficient binary framing. Weaknesses: gRPC-Web (the browser transport) supports unary and server-streaming but not client or bidirectional streaming, so browser clients are limited. Browsers also cannot set arbitrary headers on gRPC-Web, which constrains authentication schemes that depend on them.

Use gRPC streaming for internal service-to-service real-time. For browsers, use SSE for server-to-client and WebSocket for bidirectional.

## Selection guide

| Constraint | Choose | Why |
| --- | --- | --- |
| Server to browser, one way, no browser support worries | SSE | Automatic reconnect, trivial server, one connection |
| Server to browser, need custom headers or cross-origin | WebSocket | Full request control, works cross-origin with the right headers |
| Bidirectional, browser and server, no exotic needs | WebSocket | Universal support, one connection, mature tooling |
| Many concurrent low-latency streams, both ends controlled | WebTransport | Multiplexing, datagrams, migration |
| Browser to browser, or media plus data, server cost matters | WebRTC data channels | Peer-to-peer path |
| Internal services, typed, flow control matters | gRPC streaming | HTTP/2 windows, protobuf, deadlines |
| Already have an MQTT broker, browser clients | MQTT over WebSockets | One topic model across devices and browsers |
| Legacy clients, extreme compatibility requirement | Long polling | Works everywhere |

## Checklist

- Does the SSE endpoint set `Cache-Control: no-cache, no-transform` and disable proxy buffering at every hop?
- Is there a heartbeat comment every 15 to 30 seconds to defeat idle timeouts?
- Does reconnection resume from `Last-Event-ID` with a bounded replay buffer, and does an expired buffer produce an explicit reset rather than a silent gap?
- Are you within the six-connection-per-origin HTTP/1.1 budget, or is everything multiplexed on one connection?
- For WebSocket, is the ping/pong interval set so dead connections are detected rather than lingering?
- For WebSocket, do you send a meaningful close code and a reason?
- For WebTransport, have you verified actual browser and infrastructure support rather than assuming?
- For WebRTC, have you measured the TURN relay ratio? A high ratio means the direct path is failing.
- Is there a documented fallback when the preferred protocol is unavailable?

## Common mistakes

**No heartbeat on SSE.** Symptom: the connection silently dies and the browser never notices for minutes. Fix: send `: ping\n\n` on a timer and treat a missed write as a dead connection.

**Proxy buffering SSE.** Symptom: events arrive in batches with long stalls. Fix: disable buffering at every hop, not just the first.

**Not resuming from `Last-Event-ID`.** Symptom: every reconnect loses events since the disconnect. Fix: read the header, replay from your buffer, and handle the too-old case explicitly.

**Assuming SSE works cross-origin.** Symptom: the `EventSource` constructor throws or immediately errors. Fix: `EventSource` cannot be configured; use a same-origin endpoint or a proxy.

**One `EventSource` per widget.** Symptom: the browser's six-connection limit is exhausted and everything stalls. Fix: one connection multiplexing event types, dispatched by `event:` field.

**Using `onmessage` with custom event types.** Symptom: handler never fires. Fix: `onmessage` only receives the default `message` type; use `addEventListener` with your `event:` name.

**Multiline JSON in an SSE `data:` field.** Symptom: the parser breaks the event across lines. Fix: keep each payload on one `data:` line; let the library join continuation lines.

**Letting WebSocket connections linger after a client vanishes.** Symptom: file descriptors and memory grow until the process dies. Fix: heartbeat ping/pong and reap on failure.

**Assuming a WebTransport or WebRTC feature is universally available.** Symptom: it works in your browser and fails for one user. Fix: feature-detect and provide a WebSocket fallback.

**Sending a WebSocket close without a code.** Symptom: the peer cannot distinguish a clean shutdown from a network failure. Fix: send `1000` or an application code with a reason.

## Anti-patterns

- Reaching for WebRTC for a browser-to-server data stream because it feels faster. The signalling and TURN costs dominate.
- Choosing WebTransport for its features without checking whether your users' networks permit UDP to your origin.
- Treating long polling as "real time" and then scaling it to tens of thousands of concurrent streams.
- Using SSE for client-to-server messages and building a parallel WebSocket for the same stream.
- Assuming `Last-Event-ID` replay is automatic server-side. It is entirely your responsibility.
- Broadcasting every event to every client when a per-client subscription filter is available.
- Assuming a message sent over an unreliable data channel is lost. It may be delivered late, which for state updates is equivalent to delivered.
- Adding a heartbeat without ever checking the result of the write.
- Choosing a protocol by benchmark rather than by the constraints of your clients and network.

## References

- HTML Living Standard, Server-sent events - https://html.spec.whatwg.org/multipage/server-sent-events.html
- MDN, Using server-sent events - https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- MDN, EventSource - https://developer.mozilla.org/en-US/docs/Web/API/EventSource
- MDN, WebSocket API - https://developer.mozilla.org/en-US/docs/Web/API/WebSocket
- RFC 6455, The WebSocket Protocol - https://www.rfc-editor.org/rfc/rfc6455
- IETF Internet-Draft, WebTransport over HTTP/3 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http3/
- IETF Internet-Draft, WebTransport over HTTP/2 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http2/
- MDN, WebRTC API - https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API
- WebRTC specification (W3C and IETF) - https://www.w3.org/TR/webrtc/
- RFC 8445, Interactive Connectivity Establishment (ICE) - https://www.rfc-editor.org/rfc/rfc8445
- RFC 5389, STUN - https://www.rfc-editor.org/rfc/rfc5389
- RFC 5766, TURN - https://www.rfc-editor.org/rfc/rfc5766
- MDN, WebTransport API - https://developer.mozilla.org/en-US/docs/Web/API/WebTransport_API
- gRPC documentation - https://grpc.io/docs/what-is-grpc/core-concepts/
- gRPC-Web - https://github.com/grpc/grpc-web
- MQTT specification index - https://mqtt.org/mqtt-specification/