# MQTT Deep Reference

Detail for `message-queue-protocols`. MQTT 3.1.1 and 5.0 are OASIS Standards: https://mqtt.org/mqtt-specification/

## Packet types

| Packet | Direction | Purpose |
| --- | --- | --- |
| `CONNECT` | client to server | Open connection, set credentials, keep alive, will, session |
| `CONNACK` | server to client | Accept, return code, session-present flag, server-assigned values |
| `PUBLISH` | either | Send an application message with a topic, payload, QoS, and retain flag |
| `PUBACK` | either | QoS 1 acknowledgement |
| `PUBREC` | either | QoS 2: receiver accepted, awaiting release |
| `PUBREL` | either | QoS 2: sender releases the message |
| `PUBCOMP` | either | QoS 2: receiver confirms release complete |
| `SUBSCRIBE` / `SUBACK` | client to server, then server to client | Subscribe with QoS and no-local options |
| `UNSUBSCRIBE` / `UNSUBACK` | | Unsubscribe |
| `PINGREQ` / `PINGRESP` | | Keep alive |
| `DISCONNECT` | client to server | Clean close; in 5.0 can carry a reason code |

The QoS 2 four-step handshake is what makes exactly-once delivery possible between client and broker: `PUBLISH` → `PUBREC` → `PUBREL` → `PUBCOMP`. The receiver stores the message id on `PUBLISH`, and on receiving a duplicate `PUBLISH` with the same id while `PUBREL` is outstanding, responds with `PUBREC` again rather than delivering twice. The session state for in-flight QoS 2 messages is what makes this survive reconnects with a persistent session.

## Topics and matching

```text
Topic:  sensors/building-a/floor-3/temperature

Exact subscription:  sensors/building-a/floor-3/temperature
Single-level:       sensors/+/floor-3/temperature
Multi-level:        sensors/#
System (convention): $SYS/broker/uptime
```

- `+` matches exactly one level and must occupy an entire level.
- `#` matches zero or more remaining levels and must be the last character of the topic filter.
- Wildcards are legal only in subscription topic filters. A `PUBLISH` topic name containing `+` or `#` is a protocol error.
- Topic names are case-sensitive.
- Topic filters starting with `$` match only filters that also start with `$`, which keeps broker-internal topics (conventionally under `$SYS`) out of wildcard subscriptions.

## QoS negotiation and semantics

QoS flows both directions independently.

- Maximum QoS for incoming publishes: `Maximum QoS` in `CONNACK` (5.0; `Server Maximum QoS` in some broker docs).
- Maximum QoS for outgoing publishes to the client: `Receive Maximum` in `CONNECT` (5.0).
- Retained Delivery: whether retained messages honour the subscription's QoS.

Delivered QoS is `min(publish QoS, subscription max QoS)`, except that a retained message delivered at subscription QoS 0 is sent with QoS 0 regardless.

| QoS | Cost | Loss window | Duplicate window |
| --- | --- | --- | --- |
| 0 | One packet | Network drop or client disconnect before delivery | None |
| 1 | Two packets (PUBLISH, PUBACK) | Drop before PUBACK means the client never got it | Lost PUBACK means the broker resends |
| 2 | Four packets (PUBLISH, PUBREC, PUBREL, PUBCOMP) | Handshake completion failure | None between client and broker |

Crucially, delivery from broker to subscriber is at-least-once for QoS 1 and QoS 2. The subscriber's `PUBACK` confirms receipt, but the broker's own store-and-forward to another consumer, or a retry after a subscriber disconnect, can still duplicate. Application handlers must be idempotent regardless of the QoS chosen.

## Session state

MQTT 3.1.1 has a single `Clean Session` flag on `CONNECT`. MQTT 5.0 splits the behaviour:

- `Clean Start` on `CONNECT` decides whether to start a fresh session or resume an existing one.
- `Session Expiry Interval` decides how long after disconnect the broker keeps the session.
- Session state includes subscriptions, QoS 1 and 2 messages not yet fully acknowledged, and QoS 2 messages in flight.
- `CONNACK` includes a `Session Present` flag: 1 means the broker resumed stored state; 0 means it started clean.
- With `Session Expiry Interval = 0` and disconnect, the session ends immediately.

Practical rule: use a persistent session when an offline device must not lose commands sent to it. Use a clean session for a consumer that can tolerate missing messages or that will backfill from its own store.

## Retained messages

The broker stores the last `PUBLISH` with `retain = 1` for a topic (with a non-empty payload) and delivers it immediately to new subscribers that match the topic filter. Publishing an empty payload with `retain = 1` clears the retained message.

Uses:

- Last known device state: `retain` the state so a subscriber that connects late gets the current value.
- Online/offline presence: retain a presence topic rather than relying solely on will.

Avoid:

- Retaining events. Every future subscriber receives the last event as if it were current, which corrupts any consumer that assumes a retained message is the full history or a live trigger.
- Retaining on a topic with many concurrent publishers. The last write wins, and with rapid updates most retained writes are wasted.

Some brokers support retained-message limits and message expiry on retention (5.0 `Message Expiry Interval`). Check your broker's configuration for the exact keys.

## Last will and testament

Set on `CONNECT`. The broker publishes the will to the will topic and QoS when the connection closes abnormally (socket drop, keep-alive timeout, or broker error). A clean `DISCONNECT` does not trigger the will.

In MQTT 5.0 the will can carry a `Will Delay Interval`, so the broker waits that long before publishing the will, giving a fast reconnect a chance to cancel it. This is the standard way to avoid transient presence flapping on flaky networks.

Typical pattern: will topic `devices/<id>/status` with payload `offline` and retain set; on connect, publish `online` to the same retained topic.

## Keep alive

`Keep Alive` in `CONNECT` is in seconds. The client must send some control packet within one and a half times the keep-alive interval; the broker disconnects otherwise. The server picks the effective keep-alive and returns it in `CONNACK` when it wants a different value. A keep-alive of zero disables the mechanism.

## MQTT over WebSockets

Browsers cannot open raw TCP, so MQTT-over-WebSockets carries the same MQTT packets inside WebSocket binary frames. Details:

- The connection is a WebSocket handshake to a path the broker designates, for example `/mqtt`.
- The WebSocket subprotocol token is negotiated during the handshake. Brokers commonly accept `mqtt` for 3.1.1 and 5.0 and some accept `mqttv3.1` for the earlier 3.1. Confirm the exact token your broker expects from its documentation or by attempting the handshake.
- The first WebSocket frame from the client must contain a complete MQTT `CONNECT` packet. The broker responds with `CONNACK`. All subsequent MQTT packets are sent as WebSocket binary messages, one packet per message.
- Only the transport changes. QoS, topics, retained messages, wills, and sessions behave identically to TCP.

Because the broker exposes a WebSocket endpoint, it is reachable from any origin that can make HTTPS. Enforce authentication and, where the broker supports it, restrict origins at the proxy.

## Choosing MQTT

MQTT fits when:

- Publishers are constrained devices with limited memory, battery, or intermittent connectivity.
- The network is lossy, metered, or frequently unavailable, so retransmission and reconnect semantics matter.
- Fan-out is publish/subscribe to many interested subscribers, matched by topic hierarchy.
- You need last-known-state via retained messages or presence via will.
- Message sizes are small (tens to low thousands of bytes) and payloads fit one packet.

MQTT does not fit when:

- You need per-message visibility timeout and a re-drive API. QoS 1 redelivery on reconnect is the only redelivery control MQTT gives you.
- You need message retention after consumption. MQTT has no log; the broker holds only queued messages and retained state.
- You need strong ordering guarantees. MQTT guarantees order per topic per QoS flow, but multiple publishers to the same topic interleave without a defined global order.
- You need durable, replayable history. Use a broker with retention or Kafka.
- You need server-side transactions. The standard is publish/subscribe delivery with per-message QoS; multi-message atomicity is not a QoS concept.

For very constrained devices over unreliable links with sleepy nodes, MQTT-SN (MQTT for sensor networks) exists as a separate specification with a UDP binding. Check whether your broker supports it before assuming MQTT itself fits.

## Observability and tuning

Useful broker metrics to watch:

- Connections by state, and connection rate (detect flapping clients).
- Messages in, messages out, and messages dropped (QoS 0 loss and queue overflow both land here).
- Retained message count and total retained size.
- Subscriptions per client and topics with no subscribers (a publishing client with no subscribers still burns airtime).
- Slow-consumer disconnects, often caused by QoS 0 and QoS 1 publishes faster than the client can read over a slow link. Tuning the "maximum queued messages" setting per client and the outbound queue limit changes whether a client is disconnected or queued; check your broker's keys.

```text
Common client knobs (names vary by broker and library; check yours):
  keepalive
  clean_session / clean_start / session_expiry_interval
  max_inflight_messages
  max_queued_messages
  reconnect_min_delay / reconnect_max_delay
  will_topic / will_payload / will_qos / will_retain / will_delay_interval
```

## References

- OASIS MQTT Version 3.1.1 (Standard) - https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html
- OASIS MQTT Version 5.0 (Standard) - https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html
- MQTT specification index (includes MQTT-SN and the WebSocket transport) - https://mqtt.org/mqtt-specification/
- Eclipse Mosquitto documentation - https://mosquitto.org/man/mosquitto-8.html
- HiveMQ MQTT Essentials - https://www.hivemq.com/mqtt-essentials/
- RFC 6455, The WebSocket Protocol - https://www.rfc-editor.org/rfc/rfc6455
- Web Platform Tests, Server-sent events - https://wpt.fyi/results/web/server-sent-events