---
name: Message Queue Protocols
description: Covers broker wire protocols and what their guarantees actually mean, including AMQP 0-9-1 exchanges, queues, bindings, and publisher confirms, AMQP 1.0 links and settlements, MQTT topics with QoS 0, 1, and 2, retained messages, last will, and sessions, and the Kafka append-only log with consumer groups, offsets, partition ordering, and in-sync replicas. Use when choosing between RabbitMQ, Kafka, SQS, or Redis Streams, when reasoning about at-least-once versus exactly-once, or when debugging duplicate, lost, or reordered messages.
metadata:
  opencode/autoinvoke: true
---

# Message Queue Protocols

A broker protocol is a contract about *where a message goes* and *when the sender may forget about it*. Almost every bug in a queueing system is a violation of that contract by the consumer. This skill teaches you to read each protocol's guarantee precisely, then to design consumers that stay correct even when the guarantee is weaker than you hoped.

## When to use this

- You see duplicates after a deploy, a crash, or a network partition, and you need to know whether that is a bug or the protocol.
- You are choosing between RabbitMQ, Kafka, SQS, and Redis Streams for a new workload.
- You are deciding whether to use MQTT QoS 1 or 2 for an IoT or mobile fleet.
- You are sizing ordering guarantees: "ordered" only within a partition, a queue, or a session.
- You are implementing dead-lettering, prefetch/backpressure, or publisher confirms.
- You are tempted to claim "exactly once" somewhere in your design.

## The one rule that resolves most confusion

**Delivery guarantees are properties of a specific consumer, in a specific failure window, against a specific broker.** The same broker gives you at-most-once, at-least-once, or effectively-once depending on how the consumer configures acknowledgement and whether the handler is idempotent.

| Guarantee | Precise meaning | Duplicate possible? | Loss possible? |
| --- | --- | --- | --- |
| At-most-once | Message is deleted before the handler runs | No | Yes, on crash after delete |
| At-least-once | Message is deleted only after the handler reports success | Yes | No, unless you delete without processing or the broker loses data |
| Exactly-once in a broker | Broker deduplicates, e.g. Kafka transactions with `read_committed` | No within the transaction | Yes outside the transactional scope |
| Effectively-once | At-least-once delivery plus an idempotent handler with a dedup key | Yes on the wire, no in effect | No |
| Exactly-once end to end | Side effect and enqueue happen in the same atomic unit | No | No, only if the consumer's own side effects are also in that unit |

The honest engineering answer for almost every application is the fourth row: at-least-once on the wire plus an idempotent consumer. Everything else is either impossible in your architecture or requires you to own both halves of a transaction.

## Quick start

RabbitMQ topology in 0-9-1 terms: publish to an exchange, bind queues to it with routing keys, consume from queues.

```text
Publisher --(routing key: order.created)--> direct exchange: orders
                                              | binding: order.created
                                              v
                                        queue: billing
```

```bash
# Inspect topology with the RabbitMQ management CLI. Confirm available flags with --help.
rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers
rabbitmqctl list_exchanges name type durable
rabbitmqctl list_bindings source_name destination_name routing_key
```

Publish with confirms enabled, from Python using `pika`:

```python
import json

import pika

params = pika.URLParameters("amqp://guest:guest@localhost:5672/%2F")
connection = pika.BlockingConnection(params)
channel = connection.channel()

# Turn on publisher confirms: the broker then acks or nacks each publish.
channel.confirm_delivery()
confirmed = channel.basic_publish(
    exchange="orders",
    routing_key="order.created",
    body=json.dumps({"order_id": "A-1", "total_cents": 4200}),
    properties=pika.BasicProperties(
        content_type="application/json",
        delivery_mode=pika.DeliveryMode.Persistent,  # survives broker restart
        message_id="evt-2f9c1e",                      # your dedup key
    ),
    mandatory=True,  # return the message if nothing is bound, instead of dropping it
)
print("confirmed:", confirmed)
connection.close()
```

`confirm_delivery()` before `basic_publish` is the difference between "the broker stored it" and "I hoped the broker stored it".

## AMQP 0-9-1 concepts

AMQP 0-9-1 is the protocol RabbitMQ, Qpid, and compatible brokers implement. Its documentation lives with the broker, not with a standards body.

- **Exchange**: where you publish. Exchanges route; they do not store.
- **Queue**: where messages wait. Consumers read from queues.
- **Binding**: a rule linking an exchange to a queue, optionally matching a routing key pattern.
- **Routing key**: the publisher's opaque string, matched against bindings by exchange type.

```text
exchange type   routing key matching                use for
-------------   ------------------------------     -----------------------------
direct          exact match                         deterministic routing
topic           '#' zero or more levels,            hierarchical topics,
                '+' exactly one level               MQTT-like wildcards
fanout          ignored                             broadcast to all bindings
headers         routing key ignored, AMQP message
                properties matched instead          metadata-based routing
```

A published message with no matching binding is **discarded silently** in a 0-9-1 broker with no mandatory flag. `mandatory=True` on publish makes the broker return the message instead when nothing is bound. Neither is a substitute for confirms.

- **Publisher confirms** are a RabbitMQ (and common 0-9-1 broker) extension to the base 0-9-1 specification. When enabled, the broker acknowledges each publish with `basic.ack` or `basic.nack`, giving per-message durability feedback. Treat confirms as the only real "the broker has it" signal.
- **Consumer acknowledgements** are `basic.ack` (single), `basic.ack` with `multiple=true` (cumulative), or `basic.nack`/`basic.reject` with a `requeue` flag. `nack` with `requeue=true` puts the message back at the head of the queue, which can hot-loop a poison message; bound the retries and dead-letter it.
- **Prefetch** (`basic.qos`) limits unacknowledged messages per consumer or per channel. It is the only backpressure AMQP gives you. Prefetch 1 gives strict serial processing; a large prefetch gives throughput but more redelivery on crash.
- **Durability** has two independent switches: the queue/exchange must be declared durable, and each message must be published with `delivery_mode` persistent. A persistent message on a transient queue disappears with the broker. A durable queue without persistent messages gives you an empty queue after restart.

RabbitMQ-specific queue arguments (these are broker extensions, not AMQP standard): `x-queue-type` (`quorum` or `classic`), `x-max-length` and `x-max-length-bytes`, `x-message-ttl`, `x-dead-letter-exchange`, `x-dead-letter-routing-key`, `x-overflow`. Confirm current accepted values against your broker version's documentation.

```text
Queue declaration with a dead letter exchange and a length cap (RabbitMQ):
  x-queue-type        = quorum
  x-max-length        = 100000
  x-overflow          = reject-publish
  x-dead-letter-exchange = dlx
```

## AMQP 1.0 concepts

AMQP 1.0 is a different protocol from AMQP 0-9-1 despite the name. It is a layered specification, not a broker protocol with exchanges.

- **Connection**, then **Session**, then **Link**. A link binds a source to a target and carries messages.
- **Node** is the addressing model: a client attaches to a container, links to a source or target node. Exchange-plus-binding routing is replaced by address resolution outside the protocol, or by a router in between.
- **Settlement** is the acknowledgement model. A receiver settles a delivery with **Accepted**, **Released** (return to sender, do not redeliver to this receiver), **Rejected** (do not redeliver anywhere), or **Modified** (redeliver with an updated delivery count or TTL).
- **Outcome** is the asynchronous variant of the same, via a disposition frame.
- Transactions are a separate layer, defined in Part 4, and require both peers to support them.

The practical consequence: 0-9-1 clients cannot talk to 1.0 servers without a gateway. Migration between the two is a project, not a config change. See `references/amqp.md`.

## MQTT

MQTT is a publish/subscribe protocol designed for constrained devices and unreliable networks. Version 3.1.1 and 5.0 are OASIS Standards.

### Topics

Topics are `/`-separated UTF-8 strings. Wildcards are only legal in a *subscription*, never in a `PUBLISH`:

```text
sensors/+/temperature      '+' matches exactly one level
sensors/#                  '#' matches the level and all deeper levels, must be last
```

`$`-prefixed topics are conventionally reserved by brokers for their own metadata. Do not publish there.

### QoS

| QoS | Guarantee | Packet exchange | Failure window |
| --- | --- | --- | --- |
| 0 | At most once | `PUBLISH` | Loss if the connection drops before delivery |
| 1 | At least once | `PUBLISH` then `PUBACK` | Duplicate if the `PUBACK` is lost |
| 2 | Exactly once between client and broker | `PUBLISH` → `PUBREC` → `PUBREL` → `PUBCOMP` | Four-step handshake per message; costs a round trip and state |

QoS 2 is exactly once *between one client and one broker*. It says nothing about what a consumer attached to that broker does with the message, and nothing about the delivery from broker to that consumer. Delivery from broker to subscriber is always at-least-once. This is the single most misquoted guarantee in MQTT, and your application must still be idempotent.

QoS flows are negotiated per direction and per message: a publish at QoS 2 to a subscription whose maximum QoS is 1 is delivered at QoS 1. A subscriber's granted QoS is `min(publish QoS, subscription maximum QoS)`.

### Retained messages, will, sessions

- **Retained message**: the broker stores the last message published to a topic and delivers it immediately to every new subscriber of that topic. Empty payload clears it. Use for last-known-state topics such as `device/42/state`. Do not use for events: a retained event is delivered to every subscriber that ever connects, forever, which is usually wrong.
- **Last will and testament**: set on `CONNECT`, published by the broker if the connection closes abnormally. Used for presence and for cleaning up crashed publishers. The broker publishes the will on abnormal close, not on a clean disconnect with `DISCONNECT`.
- **Session state**: MQTT 3.1.1 has `cleanSession`; MQTT 5.0 splits it into `Clean Start` (for the session) and `Session Expiry Interval` (how long the broker keeps it after disconnect). With a persistent session, QoS 1 and 2 messages for subscribed topics are queued while the client is offline, and `Session Present` in `CONNACK` tells you whether queued messages are waiting.
- **Keep alive**: `Keep Alive` in `CONNECT` in seconds; the client must send something (usually `PINGREQ`) within half that interval or the broker closes the connection.

### When MQTT fits

MQTT is the right choice when the publisher is a constrained device (MCU, sensor, vehicle, kiosk), the network is lossy or metered, the fan-out is publish/subscribe to many interested subscribers rather than to a named work queue, and the topic hierarchy is the natural routing model. It is the wrong choice when you need a durable work queue with per-message acknowledgement and per-message visibility timeout: MQTT has neither a visibility timeout nor a re-drive API. See `references/mqtt.md`.

## Kafka basics

Kafka is a partitioned, replicated, append-only log. It is not a queue; you read by offset, and consumers manage their own position.

```text
topic: orders  (3 partitions)

partition 0        partition 1        partition 2
offset 0 .. N      offset 0 .. N      offset 0 .. N
   |                   |                   |
 consumer A        consumer B         consumer A        <- group "billing"
```

- **Partition**: the unit of parallelism and of ordering. Total order exists only within one partition.
- **Offset**: a monotonically increasing integer within a partition. Consumer position is `(topic, partition, offset)`.
- **Consumer group**: Kafka assigns each partition to at most one consumer in a group. More consumers than partitions means idle consumers.
- **Partition keying**: the record key determines the partition. Same key, same partition, therefore same order. No key means round-robin (or sticky batching), which gives no ordering guarantee at all.
- **Replication and ISR**: each partition has one leader and follower replicas. The in-sync replica set is the set currently caught up. `acks=all` requires all ISR replicas to acknowledge; `acks=1` requires only the leader.
- **`min.insync.replicas`** sets the minimum ISR size for the leader to be considered available.

Ordering scope, stated precisely:

| Statement | True? |
| --- | --- |
| Kafka preserves record order within a partition | Yes |
| Kafka preserves record order across partitions of a topic | No |
| A single consumer reading several partitions preserves a global order | No, unless it merges by offset across partitions, which is not the same as log order |
| Retries can reorder records unless the idempotent producer is enabled | Correct. `enable.idempotence=true` with `max.in.flight.requests.per.connection<=5` prevents reordering from retries |

Consumer configuration that changes guarantee semantics:

```properties
enable.auto.commit=true
auto.commit.interval.ms=5000
auto.offset.reset=earliest
group.instance.id=processor-3
isolation.level=read_committed
```

- `auto.offset.reset` is `earliest`, `latest`, or `none`. `none` means a new group with no committed offset fails rather than silently skipping data.
- `isolation.level=read_committed` hides records from aborted transactions and from open transactions. `read_uncommitted` sees everything.
- Auto-commit commits offsets based on time, not on processing completion, so it can commit an offset for a message whose handler has not finished. Commit manually after processing.

### Exactly-once in Kafka, precisely

Kafka's exactly-once is scoped. `enable.idempotence=true` deduplicates producer retries by producer id and sequence number per partition, which removes duplicates from retries. Adding transactions (`transactional.id`, `sendOffsetsToTransaction`) makes the record and the consumer offset commit atomic, so a `read_committed` consumer sees all or none. None of this touches your database. If your handler writes to Postgres, the Kafka transaction does not roll it back. The end-to-end pattern is an inbox table with a unique constraint on the message key inside the same database transaction as the business write.

## Decision table

| Requirement | RabbitMQ (AMQP 0-9-1) | Kafka | SQS | Redis Streams |
| --- | --- | --- | --- | --- |
| Model | Broker with queues and routing | Distributed log | Managed queue | In-memory log with consumer groups |
| Message routed to | Named queues via exchange bindings | All consumers in a group read their own offsets | One queue per URL | Consumers in a group read their own offsets |
| Ordering scope | One queue (single consumer at a time unless configured otherwise) | One partition | FIFO queues: one MessageGroupId at a time | All entries in the stream |
| Retention | Until consumed | By time or size, independent of consumption | Until deleted, typically by retention policy | Until trimmed or deleted |
| Replay after consumption | No | Yes, by resetting offsets | No | Yes, by reading from an earlier ID |
| Acknowledgement | Per-message ack/nack; confirms for publishers | Offset commit | Visibility timeout plus delete | `XACK` after `XREADGROUP` |
| Duplicate risk | At-least-once unless you ack before handling | At-least-once unless idempotence plus transactions | At-least-once within the visibility window | At-least-once unless you `XACK` |
| Ordering key | Routing key plus queue | Record key | `MessageGroupId` | Single stream |
| Natural fan-out to many consumers | Exchanges and bindings, competing or publishing | Every group has its own offsets, so yes | Not built in; fan out with SNS or an app | Every group has its own offsets, so yes |
| Operations | Self-hosted or managed; you own clustering | Clustered; you own partition and retention tuning | Fully managed, no broker to run | Runs inside Redis; persistence depends on Redis config |
| Best fit | Work queues, RPC, flexible routing, per-message control | High-throughput event log, replay, many independent consumers | Lowest operational burden, decouple a service | Small to medium, low latency, already running Redis |

Pick by shape, not by benchmark:

- You need per-message routing, priority, delay, or acknowledgements and have few consumers: RabbitMQ.
- You need to replay history, or many independent consumer groups must each see everything: Kafka.
- You want zero broker to run and can live with a single queue with no replay: SQS.
- Redis is already a dependency, latency matters more than durability guarantees, and you want groups plus a simple log: Redis Streams.

For SQS specifically, distinguish the two queue types. Standard queues are best-effort ordered, may deliver duplicates, and have at-least-once semantics. FIFO queues offer ordering per `MessageGroupId` and a deduplication window, but throughput is limited and only the ordering within a group is guaranteed. Long polling (`WaitTimeSeconds` between 0 and 20) reduces empty receives.

Redis Streams, concretely:

```bash
XADD orders:events MAXLEN ~ 1000000 * order_id A-1 total_cents 4200
XREADGROUP GROUP billing billing-1 COUNT 100 BLOCK 5000 STREAMS orders:events >
XACK orders:events 1716000000000-0
XPENDING orders:billing - + 10
XAUTOCLAIM orders:events billing billing-2 60000 0-0
```

The consumer group's pending entries list (PEL) holds messages delivered but not acknowledged. `XPENDING` shows them; `XAUTOCLAIM` reassigns entries abandoned by a dead consumer after an idle threshold. A pending message is invisible to that group until claimed, which is the main operational footgun: a crashed consumer stalls its messages until something claims them.

## Checklist

- Do you know, for each queue, whether it is at-most-once, at-least-once, or effectively-once, and is the answer written down?
- Is the consumer idempotent, with a dedup key that survives retries?
- Is the message published with a durable queue *and* persistent delivery, or a topic whose replication factor is at least what you need?
- Are publisher confirms (or producer `acks`) enabled and their failures handled?
- Is ack/nack deferred until after the side effect, not before?
- Are retries bounded, with a dead-letter destination?
- Is prefetch or a concurrency limit set so a slow consumer cannot take an unbounded number of unacknowledged messages?
- If you need ordering, is the ordering key actually the partition key or message group?
- If you need replay, is the broker a log rather than a queue?
- Is the dead-letter queue monitored, or does it silently grow forever?

## Common mistakes

**Acknowledging before processing.** Symptom: messages vanish on a crash. Root cause: ack in the receive handler before the work is durable. Fix: ack after the side effect commits, and make the handler idempotent so the duplicate is harmless.

**Retrying forever with `requeue=true`.** Symptom: CPU pinned on one poison message, queue depth flat. Root cause: a validation error that can never succeed. Fix: count attempts, dead-letter after a bound, and alert on the dead-letter queue.

**Assuming a non-durable queue plus persistent messages is durable.** Symptom: empty queue after a broker restart. Fix: both switches, together.

**Using a transient queue for a workload that must survive restarts.** Symptom: message loss on deploy. Fix: declare durable (or quorum) queues and persistent messages, and test a restart.

**Believing MQTT QoS 2 is end-to-end exactly-once.** Symptom: duplicate application effects. Root cause: QoS 2 covers client to broker only. Fix: idempotent consumers.

**Assuming Kafka orders across a topic.** Symptom: interleaved events for the same entity. Fix: key records by entity id, and design consumers to be per-partition independent.

**Enabling `enable.auto.commit` and thinking it is commit-after-processing.** Symptom: events skipped after a crash because the offset advanced past unprocessed records. Fix: manual commit after processing, or a bounded `auto.commit.interval.ms` plus idempotence.

**Leaving Redis Stream entries unclaimed after a consumer crash.** Symptom: `XPENDING` grows, group stalls. Fix: `XAUTOCLAIM` with an idle threshold, monitored.

**Choosing SQS standard when you needed FIFO.** Symptom: reordering under load. Fix: FIFO queue with `MessageGroupId`, accepting the throughput ceiling.

## Anti-patterns

- Treating a queue as a place where a message disappears once read. Retention and replay are design parameters.
- Claiming "exactly once" for any system whose consumer also writes to a database, without naming the transaction that makes it true.
- Publishing before configuring confirms, then discovering loss during an incident.
- Using a routing key that carries business meaning and then changing the meaning without migrating bindings.
- One queue for every consumer (competing consumers) when you needed a broadcast; use exchanges or a log.
- Unbounded retries with no dead-letter destination.
- Treating broker "delivery" as "processing", and metrics that report only the former.
- Using a fanout exchange as a load balancer and wondering why one queue starves.
- Sizing consumers above partition count and celebrating the idle ones.

## References

- OASIS AMQP Version 1.0 (Part 0 overview, Part 2 transport, Part 3 messaging) - https://docs.oasis-open.org/amqp/core/v1.0/os/amqp-core-overview-v1.0-os.html
- AMQP 0-9-1 reference (RabbitMQ) - https://www.rabbitmq.com/amqp-0-9-1-reference.html
- AMQP 0-9-1 specification (RabbitMQ-hosted PDF) - https://www.rabbitmq.com/resources/specs/amqp0-9-1.pdf
- RabbitMQ reliability guide - https://www.rabbitmq.com/reliability.html
- RabbitMQ consumer acknowledgements - https://www.rabbitmq.com/confirms.html
- RabbitMQ dead letter exchanges - https://www.rabbitmq.com/dlx.html
- OASIS MQTT Version 3.1.1 - https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html
- OASIS MQTT Version 5.0 - https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html
- MQTT specification index - https://mqtt.org/mqtt-specification/
- Kafka protocol documentation - https://kafka.apache.org/protocol
- Kafka consumer configuration - https://kafka.apache.org/documentation/#consumerconfigs
- Kafka producer configuration - https://kafka.apache.org/documentation/#producerconfigs
- AWS SQS developer guide - https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
- Redis Streams documentation - https://redis.io/docs/latest/develop/data-types/streams/
- OWASP Automated Threats to Web Applications (T3) - https://owasp.org/www-project-automated-threats-to-web-applications/