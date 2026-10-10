# WebRTC and WebTransport Reference

Detail for `realtime-protocols`. WebTransport is an IETF Internet-Draft, not an RFC; verify current status at the datatracker.

## WebTransport stack

```text
+--------------------------------------------+
| application: streams and datagrams         |
+--------------------------------------------+
| WebTransport (Internet-Draft)              |
+--------------------------------------------+
| HTTP/3 (RFC 9114)                          |
+--------------------------------------------+
| QUIC (RFC 9000) with TLS 1.3 (RFC 9001)    |
+--------------------------------------------+
| UDP                                         |
+--------------------------------------------+
```

Concepts from the drafts:

- **Session**: created over an HTTP/3 request that carries the `webtransport` HTTP/3 setting. A session is a container for streams and datagrams.
- **Bidirectional stream**: client-initiated and server-initiated halves, like a TCP connection, ordered and reliable by default.
- **Unidirectional stream**: one direction only.
- **Datagram**: unreliable, unordered. `send()` versus `sendUnreliable()`. Reliability settings per stream and per datagram.
- **Flow control**: per stream and per session, credit based, so a slow consumer does not stall others.
- **Session statistics**: a `getStats()` API returning packets, bytes, and stream state. There is no standardised API surface across browsers yet, so feature-detect.

The HTTP/3 mapping lives in `draft-ietf-webtrans-http3` and the HTTP/2 mapping in `draft-ietf-webtrans-http2`. Both are drafts. Confirm which one your target browsers and server stacks implement before designing around either.

## Why WebTransport exists

The problems it addresses relative to WebSocket:

1. **Head-of-line blocking.** WebSocket rides a single ordered byte stream over HTTP/1.1 or HTTP/2, so a lost packet stalls every message. QUIC gives independent streams, so one stalled stream does not block others.
2. **No unreliable send.** WebSocket offers no way to say "this state update is worthless if it arrives late". Datagrams do.
3. **Connection migration.** QUIC keeps the session across a network change. WebSocket dies.
4. **Handshake latency.** WebTransport builds on HTTP/3 and can benefit from QUIC 0-RTT, subject to the replay caveats in the `http-protocol-deep` skill.

The costs: no HTTP/1.1 fallback, no browser polyfill, narrower browser support, and fewer mature server implementations. Add a WebSocket fallback if you ship this to unknown clients.

## WebRTC data channels

### The protocol stack

```text
application data channel
SCTP (RFC 9260) over DTLS        -> association, streams, reliability
DTLS (RFC 9147 for DTLS 1.2)    -> authenticated encryption
ICE (RFC 8445)                   -> candidate pairs and connectivity checks
STUN (RFC 5389) / TURN (RFC 5766) -> reachability and relay
signalling (your transport)      -> SDP offer/answer and ICE candidates
```

The SCTP-over-DTLS encapsulation used by WebRTC is specified by an IETF Internet-Draft in the packet transport working group. Check its published status at the datatracker before citing a specific RFC number for it.

### Data channel options

```javascript
const reliableOrdered = pc.createDataChannel("a", { ordered: true });
const unordered       = pc.createDataChannel("b", { ordered: false });
const maxRetransmits  = pc.createDataChannel("c", {
  ordered: false,
  maxRetransmits: 0,        // unreliable, drop rather than retry
});
const maxPacketLifeTime = pc.createDataChannel("d", {
  ordered: false,
  maxPacketLifeTime: 100,   // unreliable, give up after 100 ms
});
```

- `ordered: true` gives ordered delivery within the channel, across retransmissions.
- `maxRetransmits` and `maxPacketLifeTime` are mutually exclusive and both disable retransmission beyond the limit. With either, delivery is not guaranteed and reordering can occur.
- `negotiated: true` with an explicit `id` lets both sides agree on the stream id out of band, which is useful when the channel must exist before the offer completes.
- Data channels appear in `pc.sctp` statistics, distinct from RTP media statistics.

### ICE, STUN, and TURN

```javascript
const pc = new RTCPeerConnection({
  iceServers: [
    { urls: "stun:stun.example.net:3478" },
    {
      urls: "turn:turn.example.net:3478?transport=udp",
      username: "turn-user",
      credential: "turn-credential",
    },
    {
      urls: "turns:turn.example.net:5349?transport=tcp",
      username: "turn-user",
      credential: "turn-credential",
    },
  ],
  iceCandidatePoolSize: 4,
});
```

| Component | Purpose | Notes |
| --- | --- | --- |
| Host candidate | Your local address | Works only on the same LAN |
| Server-reflexive candidate | Your public address, discovered via STUN | Fails behind symmetric NAT |
| Relay candidate | A TURN relay address | Always works if TURN is reachable, at the cost of bandwidth and latency |

- STUN discovers the public mapping. It never relays data.
- TURN relays data when a direct path fails. It costs you bandwidth and adds latency, and it is the single biggest operational cost of WebRTC at scale.
- `turns` is TURN over TLS on 5349, useful where UDP is blocked.
- `iceCandidatePoolSize` pre-gathers candidates to cut the setup time.
- If a `turn:` URL has `?transport=udp` and UDP is blocked, the connection fails. Always configure both a `turn:` and a `turns:` entry.
- Renegotiation happens when media tracks are added or removed. For a data-only application you renegotiate rarely, so keep the setup minimal.

Measure the relay ratio (`candidate-pair` statistics show which candidate type won). A high relay ratio means the direct path is failing, which is a signal about your users' networks, not about the protocol.

### Signalling, which is your problem

No standard exists. A workable design:

```json
{ "type": "offer", "sdp": "v=0\r\n..." }
{ "type": "answer", "sdp": "v=0\r\n..." }
{ "type": "ice", "candidate": { "candidate": "...", "sdpMid": "0", "sdpMLineIndex": 0 } }
{ "type": "bye" }
```

Things signalling must handle that are easy to get wrong:

- **Trickle ICE vs complete ICE.** Trickle sends each candidate as it is found and cuts setup time. Non-trickle waits for gathering to finish. Either is valid; the peer must agree.
- **Glare.** Both sides offering at once. Break the tie with a deterministic rule, such as comparing peer ids, or by re-offering after a `rollback`.
- **Session expiry.** Idle sessions leak state on both sides. Send `bye` on unmount and expire on a timer.
- **Renegotiation races.** Serialize negotiations with a queue, or two concurrent `setLocalDescription` calls throw.
- **Reconnect.** A peer that loses its network needs a fresh ICE session and, in most cases, a fresh peer connection.

## Choosing between WebTransport, WebRTC, and WebSocket for data

| Requirement | WebSocket | WebTransport | WebRTC data channel |
| --- | --- | --- | --- |
| Browser to server | Yes | Yes | Possible but not the point |
| Browser to browser | Needs a signalling server | Needs a signalling server | Yes, server relays setup only |
| Server touches payload | Yes | Yes | No, unless TURN is used |
| Media (audio, video) | No | No | Yes |
| Unreliable delivery | No | Yes (datagrams) | Yes |
| Connection migration | No | Yes | Yes |
| Works over HTTP/1.1 | Yes | No | No |
| Browser availability | Universal | Narrower | Widely available with WebRTC |
| Implementation cost | Low | Medium | High |

The default answer for an application needing bidirectional data between a browser and a server is still WebSocket. WebRTC earns its cost when the data must go peer-to-peer or must share a path with media. WebTransport earns its cost when you control both ends and need multiplexing and datagrams.

## References

- IETF Internet-Draft, WebTransport over HTTP/3 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http3/
- IETF Internet-Draft, WebTransport over HTTP/2 - https://datatracker.ietf.org/doc/draft-ietf-webtrans-http2/
- RFC 9114, HTTP/3 - https://www.rfc-editor.org/rfc/rfc9114
- RFC 9000, QUIC - https://www.rfc-editor.org/rfc/rfc9000
- MDN, WebTransport API - https://developer.mozilla.org/en-US/docs/Web/API/WebTransport_API
- W3C, WebRTC specification - https://www.w3.org/TR/webrtc/
- MDN, RTCPeerConnection - https://developer.mozilla.org/en-US/docs/Web/API/RTCPeerConnection
- MDN, RTCDataChannel - https://developer.mozilla.org/en-US/docs/Web/API/RTCDataChannel
- RFC 8445, Interactive Connectivity Establishment - https://www.rfc-editor.org/rfc/rfc8445
- RFC 5389, STUN - https://www.rfc-editor.org/rfc/rfc5389
- RFC 5766, TURN - https://www.rfc-editor.org/rfc/rfc5766
- RFC 9260, Stream Control Transmission Protocol - https://www.rfc-editor.org/rfc/rfc9260
- RFC 9147, DTLS 1.2 - https://www.rfc-editor.org/rfc/rfc9147
- IETF packet transport WG, SCTP over DTLS - https://datatracker.ietf.org/wg/packettrans/documents/