# Delivery Guarantees: What Each One Actually Means

Detail for `message-queue-protocols`. Read this before quoting a guarantee in a design doc or an SLA.

## The vocabulary

**At-most-once** ("fire and forget", QoS 0, `acks=0`). The broker accepts responsibility at send time and never redelivers. Loss is possible on any failure after the send: a broker crash, a network partition, a consumer crash before processing.

**At-least-once**. The broker retains the message until it learns the consumer handled it. Duplicates are possible whenever the acknowledgement is lost after the consumer already processed the message. This is the default and the correct default for business events.

**At-most-once plus redelivery timeout** (SQS). The message becomes invisible for a visibility timeout, then reappears. If the consumer finished but the delete did not land, it runs again.

**Effectively-once** (the practical default). At-least-once delivery plus an idempotent handler keyed on a stable message id, often backed by a uniqueness constraint. Duplicates exist on the wire; the effect is once.

**Exactly-once**. Two different things share this name:

| Kind | Mechanism | Scope |
| --- | --- | --- |
| Transport-level exactly-once | QoS 2 (client to broker only); Kafka idempotent producer (retries, per producer per partition) | A single link |
| Transactional exactly-once | Kafka transactions with `read_committed` | Records and consumer offsets in one Kafka transaction |
| End-to-end exactly-once | Consumer's side effect in the same atomic unit as the enqueue (a database transaction) | Only where both sides share a transactional store |

Only the third is what a business cares about, and it requires the consumer's side effect and the message consumption to share a transaction. With a broker and a database that cannot participate in one transaction, you get effectively-once and nothing more.

## Duplicate sources, exhaustively

Every one of these produces a duplicate under at-least-once semantics:

1. Consumer processes, then crashes before ack/delete/commit.
2. Ack is sent but lost in transit; the broker retries.
3. Consumer is rebalanced or restarted after acking a batch but before all work completed (offset auto-commit on a timer).
4. Producer retries after a partial send (mitigated by an idempotent producer).
5. Visibility timeout expires while the handler is still running, so a second consumer picks up the same message.
6. The handler itself retries, or a transaction deadlock causes the client library to retry.
7. Deploy: a graceful shutdown that requeues in-flight messages while the original handler also completes.
8. At-least-once fanout: two different consumers in a queue that both believe they own the message because of a visibility race.

None of these are bugs in the broker. Design for them.

## Loss sources, exhaustively

1. Producer never enabled confirms, and the send failed.
2. Transient (non-durable) queue or topic, then a restart.
3. Persistent message published to a transient queue.
4. Retention expired a record a lagging consumer had not read.
5. Poison message nacked with requeue forever, then dead-lettered and never reprocessed.
6. Consumer acked before the side effect was durable.
7. Publisher published with `mandatory=False` and no bindings existed; the broker discarded it.
8. Redis Stream entry trimmed by `MAXLEN` before the consumer group reached it.
9. SQS retention period shorter than your longest outage.

## The dedup pattern that works

Give every message a stable, globally unique id at creation. Consume at-least-once. Make the side effect conditional on first sighting that id.

```sql
CREATE TABLE consumed_events (
  event_id     text PRIMARY KEY,
  consumed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
  order_id     text PRIMARY KEY,
  total_cents  integer NOT NULL
);

CREATE OR REPLACE FUNCTION apply_order_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- The insert is the dedup gate. A duplicate event hits the primary key
  -- conflict and the whole transaction rolls back, so nothing is applied twice.
  INSERT INTO consumed_events (event_id) VALUES (NEW.event_id);
  INSERT INTO orders (order_id, total_cents)
  VALUES (NEW.order_id, NEW.total_cents)
  ON CONFLICT (order_id) DO NOTHING;
  RETURN NULL;
END;
$$;
```

The correctness argument: the dedup row and the business row are in one transaction. Either both land or neither does. A duplicate insert into `consumed_events` aborts the transaction, so the business write never happens. This works with any at-least-once broker because the guarantee lives in your database, not in the message path.

Two ways to make it cleaner, depending on your database: use `INSERT ... ON CONFLICT DO NOTHING` on the inbox table and only apply the business write if the insert reported a new row, or use a trigger as above so the application code cannot forget the guard.

## When you can honestly claim exactly-once

Say "exactly-once" only when all of these are true:

- The producer's send and the business side effect are in the same atomic unit, or the consumer's side effect and the offset or ack are.
- Consumers use manual commit after the side effect.
- The dedup key is generated once, at the source, and is genuinely unique.
- The outbox or inbox table is on a primary key constraint.

Otherwise say "at-least-once with an idempotent consumer" or "effectively-once". Both are respectable engineering positions; "exactly-once" used loosely is not, because it hides the fact that someone still has to deduplicate.

## Documenting a guarantee

Write it as a sentence an engineer could test:

```text
Bad:  "The queue guarantees exactly-once delivery."
Good: "Events are delivered at-least-once. Each event carries an immutable
       event_id assigned at creation. The consumer inserts that id into the
       consumed_events table in the same database transaction as the business
       write; a duplicate aborts the transaction. Observed duplicates in
       processed_events are therefore zero, verified by the nightly
       reconciliation job."
```

## Testing guarantees

- Duplicate test: kill the consumer after the side effect commits and before the ack. Restart. Confirm the second delivery is a no-op.
- Loss test: kill the producer between send and confirm. Confirm the publisher surfaced the failure rather than proceeding.
- Reorder test: publish with several keys under load and confirm per-key ordering holds and cross-key order is not assumed.
- Retention test: leave a consumer stopped longer than retention and confirm it fails loudly rather than skipping data.

## References

- RabbitMQ reliability guide (definitions of the guarantees) - https://www.rabbitmq.com/reliability.html
- RabbitMQ consumer acks and publisher confirms - https://www.rabbitmq.com/confirms.html
- Kafka delivery semantics - https://kafka.apache.org/documentation/#semantics
- Kafka consumer configuration (`isolation.level`, `auto.commit.interval.ms`) - https://kafka.apache.org/documentation/#consumerconfigs
- AWS SQS exactly-once processing - https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html
- MQTT Version 5.0, QoS definitions - https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html
- Redis Streams documentation (`XACK`, pending entries) - https://redis.io/docs/latest/develop/data-types/streams/
- Martin Kleppmann, Designing Data-Intensive Applications, chapter on delivery guarantees - https://dataintensive.net/