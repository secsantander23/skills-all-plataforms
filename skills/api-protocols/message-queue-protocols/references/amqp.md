# AMQP Deep Reference

Detail for `message-queue-protocols`. AMQP 0-9-1 is documented by the broker that implements it (RabbitMQ's reference is the de facto standard). AMQP 1.0 is an OASIS Standard with published parts.

## The two AMQPs are not related

This is the single most important thing to know before designing anything.

| | AMQP 0-9-1 | AMQP 1.0 |
| --- | --- | --- |
| Status | Broker protocol written for RabbitMQ in 2006 | OASIS Standard, approved 2012 |
| Layering | Single monolithic protocol | Layered: transport, messaging, transactions, security |
| Addressing | Exchange, routing key, binding, queue | Nodes, with address resolution outside the protocol |
| Sessions | Channel-scoped | Connection, session, link |
| Acknowledgement | `basic.ack` / `basic.nack` / `basic.reject` | Settlement (`Accepted`, `Released`, `Rejected`, `Modified`) and outcomes |
| Transactions | Not in the base spec | Part 4, `dtxn-*` performatives |
| Interop | Cannot speak to a 1.0 server | Cannot speak to a 0-9-1 server |

Migration between them requires a gateway or a rewrite, not a setting.

## AMQP 0-9-1 command model

Commands are numbered classes with methods, multiplexed on a channel.

```text
connection.start / connection.start-ok
connection.tune / connection.tune-ok   channel_max, frame_max, heartbeat
connection.open / connection.open-ok

channel.open / channel.open-ok
exchange.declare / exchange.declare-ok
queue.declare / queue.declare-ok
queue.bind / queue.bind-ok
basic.qos / basic.qos-ok
basic.consume / basic.consume-ok
basic.publish        (no response by default)
basic.deliver        (server to client, with delivery-tag)
basic.ack / basic.nack / basic.reject
basic.return         (mandatory return)
basic.get / basic.get-ok / basic.get-empty
confirm.select / confirm.select-ok
tx.select (RabbitMQ extension)
```

`connection.tune` negotiates `channel_max`, `frame_max`, and `heartbeat`. Heartbeat frames are negotiated in tens of seconds; a value of zero disables them. A heartbeat is two frames per channel every timeout interval, and the peer is expected to drop the connection after missing two intervals.

### Publisher confirms

Confirm mode is negotiated with `confirm.select`. After that, every published message carries a delivery tag in the `basic.ack` or `basic.nack` from the broker. Confirms cover the broker accepting responsibility for the message, including persisting it if the queue is durable and the message is persistent.

Several messages may be confirmed together with `multiple=true` on `basic.ack`, meaning all tags up to and including that one are acknowledged. A `basic.nack` means the broker could not take responsibility; the message may or may not have been stored, so treat the message id as potentially already stored and rely on your dedup key.

RabbitMQ confirms behaviour on persistent messages with durable queues, transient messages, and unroutable messages is documented per broker version. Read the broker's confirms page before relying on a specific guarantee.

### Consumer acknowledgements

```text
basic.ack    delivery-tag [multiple]
basic.nack   delivery-tag [multiple] requeue
basic.reject delivery-tag requeue
```

- Ack *after* the side effect is durable, not before.
- `nack requeue=true` puts the message back at the head of the queue, which for many consumers means an immediate retry and a hot loop on a poison message. Use a dead-letter exchange or a retry queue with a delay.
- Manual acks are per-consumer; the tag is scoped to the channel that delivered it.

### Prefetch and backpressure

```text
basic.qos prefetch-size [prefetch-count] [global]
```

Prefetch bounds how many messages the broker may have outstanding to a consumer. It is the only flow control AMQP offers. A good policy: unacked-persistent messages also count against memory limits in many brokers, so unlimited prefetch of persistent messages can exhaust memory. Set an explicit count and observe whether it limits throughput in your workload.

### Priority, expiration, and dead lettering

- Priority: `queue.declare` with a `x-max-priority` argument (RabbitMQ). Implementations that support it map the message priority to internal weighted delivery. Messages are delivered highest priority first among ready messages, not strictly, and lower-priority messages can be starved.
- Per-message expiry: message property `expiration` in milliseconds as a string (RabbitMQ). Per-queue expiry: `x-message-ttl` in milliseconds. Expired messages are discarded or dead-lettered, whichever the queue is configured to do.
- Dead lettering: `x-dead-letter-exchange` and optionally `x-dead-letter-routing-key` on the queue. Rejection, expiry, or a delivery-limit breach cause the broker to republish to that exchange. This is a RabbitMQ extension, not base 0-9-1. Confirm behaviour per broker version.

```text
Queue arguments (RabbitMQ):
  x-dead-letter-exchange     = dlx
  x-dead-letter-routing-key = dead
  x-message-ttl             = 3600000
  x-max-length              = 100000
  x-queue-type              = quorum
```

Quorum queues are a RabbitMQ queue type designed for data safety and availability, replicated across a set of nodes with Raft. Their API and operational behaviour (including policies and delivery limits) differ from classic mirrored queues and continue to evolve. Check your broker's documentation for the current feature set.

## AMQP 1.0 structure

The OASIS Standard is split into parts:

| Part | Covers |
| --- | --- |
| Part 0 | Overview and conformance |
| Part 1 | Type system and encoding |
| Part 2 | Transport layer (framing, sessions, links, flow control) |
| Part 3 | Messaging layer (message format, outcomes, transactions reference) |
| Part 4 | Transactions |
| Part 5 | Security layers (TLS and SASL) |

- **Container** is the AMQP 1.0 term for an endpoint. There is no client/broker asymmetry in the protocol; a "broker" is just a node that terminates links.
- **Connection** negotiates TLS and SASL and carries **sessions**. A session carries **links** and delivers frames in order for them.
- **Link** binds a source node to a target node for a specific message format and delivery mode. Unsettled deliveries are tracked by delivery id and delivery number.
- **Flow control** in AMQP 1.0 uses a link-credit window: the receiver issues credit and the sender may deliver only that many unsettled messages, adjusting with `link.flow`. This is message-count credit, unlike HTTP/2's byte credit.
- **Security**: TLS or SASL (PLAIN in RFC 4616, SCRAM-SHA-1 in RFC 5802) negotiated at connection open.

### Outcomes and settlements

| Outcome | Receiver's meaning | Effect |
| --- | --- | --- |
| `Accepted` | Processed successfully | Message is settled as consumed |
| `Released` | Not processed, return to sender | Sender decides; typically re-delivered |
| `Rejected` | Invalid, never redeliver | Message dropped or dead-lettered by the node |
| `Modified` | Redeliver with changes | Delivery count or TTL updated; useful for deferred retries |

The settlement mode (`snd-settle-mode`, `rcv-settle-mode`) is negotiated per link. With `snd-settle-mode = settled` (pre-settled), the sender transfers and settles in one step, trading reliability for latency. With `rcv-settle-mode = unsettled`, the receiver must settle explicitly. Choosing unsettled and setting link credit to 1 with explicit settlement is the closest analogue to manual ack.

## Choosing between them

- RabbitMQ (0-9-1) is the pragmatic default: a huge library ecosystem, flexible exchanges, per-message acks, delayed and dead-letter topologies, RPC via `basic.consume` on a reply queue, and browser-friendly STOMP and MQTT gateways.
- AMQP 1.0 brokers (for example Qpid, or 1.0-speaking connectors) are chosen when you need the layered model, custom message formats, link-level flow control, or transactional messaging, or when an organisational standard requires 1.0.
- For high-throughput event logs with replay, neither is the right tool. Use Kafka.

## References

- AMQP 0-9-1 specification PDF (RabbitMQ-hosted) - https://www.rabbitmq.com/resources/specs/amqp0-9-1.pdf
- RabbitMQ AMQP 0-9-1 reference - https://www.rabbitmq.com/amqp-0-9-1-reference.html
- RabbitMQ confirms - https://www.rabbitmq.com/confirms.html
- RabbitMQ consumer acknowledgements and prefetch - https://www.rabbitmq.com/confirms.html#consumer-acknowledgement-mode
- RabbitMQ dead letter exchanges - https://www.rabbitmq.com/dlx.html
- RabbitMQ quorum queues - https://www.rabbitmq.com/quorum-queues.html
- RabbitMQ streams (a different, Kafka-like model on RabbitMQ) - https://www.rabbitmq.com/streams.html
- OASIS AMQP 1.0 Part 0 overview - https://docs.oasis-open.org/amqp/core/v1.0/os/amqp-core-overview-v1.0-os.html
- OASIS AMQP 1.0 Part 3 messaging - https://docs.oasis-open.org/amqp/core/v1.0/os/amqp-core-messaging-v1.0-os.html
- OASIS AMQP 1.0 Part 5 security - https://docs.oasis-open.org/amqp/core/v1.0/os/amqp-core-security-v1.0-os.html
- RFC 4616, SASL PLAIN - https://www.rfc-editor.org/rfc/rfc4616
- RFC 5802, SASL SCRAM-SHA-1 - https://www.rfc-editor.org/rfc/rfc5802