# Kafka Deep Reference

Detail for `message-queue-protocols`. Sources: Kafka protocol documentation at kafka.apache.org/protocol and the consumer/producer configuration pages.

## Wire protocol

Every Kafka client request is an envelope:

```text
RequestOrResponse ::= Size (Request | Response)
Size              ::= int32

Request           ::= ApiKey int16
                     ApiVersion int16
                     CorrelationId int32
                     ClientId string
                     RequestBody

Response          ::= CorrelationId int32
                     ResponseBody
```

`Size` is the number of bytes that follow it, which is how the broker knows where a request ends on a multiplexed connection. `CorrelationId` is chosen by the client and echoed by the broker so the client can match responses to requests. All integers are big-endian (network byte order).

The request and response bodies are schema-defined per API key. Notable APIs:

| API | Purpose |
| --- | --- |
| `Produce` | Send records to a partition |
| `Fetch` | Read records from a partition |
| `ListOffsets` | Query earliest and latest offsets and timestamps |
| `Metadata` | Learn about topics, partitions, leaders, ISR |
| `FindCoordinator` | Locate the group coordinator |
| `JoinGroup` / `SyncGroup` | Join and join-assign a consumer group |
| `Heartbeat` | Stay in the group |
| `OffsetCommit` / `OffsetFetch` | Store and read group offsets |
| `InitProducerId` | Obtain producer id and epoch for idempotence or transactions |
| `AddPartitionsToTxn` / `EndTxn` | Transaction control |

On the wire a record becomes a *record batch*: a base offset, leader epoch, magic byte, a CRC, attributes, last-offset-delta delta and timestamps, then the compressed records themselves. Batches are the unit of replication and of compression; `linger.ms` trades latency for batch size.

## Consumer groups

- A group coordinator (one per group, chosen by a hash of the group id) manages membership and offset storage.
- Consumers send `JoinGroup` with a `group.instance.id` for static membership. With a static member id, a restarting consumer keeps its partitions without a rebalance.
- The **group leader** receives the member list, runs the assignor, and returns the assignment in `SyncGroup`.
- Assignors determine which consumer gets which partition: range, roundrobin, sticky, and cooperative-sticky. The sticky and cooperative variants minimise partition movement on rebalance. These are configured with `partition.assignment.strategy` and are the most direct cause of rebalance-related latency; measure before and after changing.
- A **rebalance** stops consumption for the group until all members rejoin (in the eager protocols) or until ownership transfers (in cooperative). Long processing times inside a poll loop cause rebalances, which cause more churn, which causes more long processing.

Offset storage lives in Kafka's internal `__consumer_offsets` topic, keyed by group id, partition, and consumer id. You can read it, but treat the format as internal.

`auto.offset.reset` decides what a new group with no committed offset does: `earliest` (oldest retained record), `latest` (newest, skipping everything), or `none` (fail fast). `latest` on a fresh consumer group is a silent data-loss bug; prefer `earliest` for reprocessing and explicit for production cutover.

`enable.auto.commit=true` commits offsets on a timer, not on completion. Set `auto.commit.interval.ms` low if you auto-commit, or disable it and commit after processing.

## Replication, ISR, and `acks`

- Each partition has one leader replica that serves reads and writes. Followers replicate from the leader.
- The in-sync replica set (ISR) is the set of replicas that are caught up within the replication lag tolerance. A replica that falls out of ISR and is later caught up re-enters.
- `acks=0`: fire and forget, no response. Fastest, loses data on leader failure.
- `acks=1`: leader writes locally and responds. Loses data if the leader dies before replication.
- `acks=all` (also written `-1`): leader waits for all current ISR replicas. Combined with `min.insync.replicas=2` on a replication factor of 3, a single broker loss does not lose the write.

```properties
# Producer durability posture
acks=all
enable.idempotence=true
max.in.flight.requests.per.connection=5
retries=2147483647
```

`enable.idempotence=true` requires `acks=all`, and the broker rejects `max.in.flight.requests.per.connection` above 5 because higher in-flight counts can reorder on retry. Setting `retries` to a very large number is the idiom with idempotence enabled: retries become safe, so retry indefinitely.

- The idempotent producer assigns a producer id and a per-partition sequence number. A broker that sees a duplicate or out-of-order sequence for that producer drops or re-sequences it.
- Idempotence removes duplicates from retries. It does not make a record atomic across partitions.

## Transactions and exactly-once scope

```properties
transactional.id=order-processor-a
isolation.level=read_committed
enable.auto.commit=false
```

To get Kafka's exactly-once behaviour:

1. The producer uses a `transactional.id`, which is stable across process restarts. On `InitProducerId` the broker fences any prior producer with that id.
2. `sendOffsetsToTransaction` commits consumer offsets inside the same transaction as the records.
3. Consumers set `isolation.level=read_committed`, so they never see records from an open or aborted transaction.

Scope of the guarantee: records and offsets commit or abort together. Nothing outside Kafka participates. The classic gap is a consumer that reads from Kafka in a transaction and writes to Postgres: the Postgres write is outside the transaction, and a crash between them leaves an inconsistency Kafka cannot repair. To close it, use an inbox table with a unique constraint on the record key, inserting in the same database transaction as the business write.

Fencing detail: two processes configured with the same `transactional.id` cannot both write. The newer process's `InitProducerId` bumps the epoch and the older producer's in-flight transactions fail. This prevents a restarted process and a zombie process from both committing, at the cost of the zombie failing loudly.

## Ordering, restated precisely

| Claim | Truth |
| --- | --- |
| Records with the same key go to the same partition | True. Key-to-partition is a hash, deterministic per topic and partition count |
| Changing partition count changes key-to-partition mapping | True. This breaks key co-location and, with in-flight data, can split a key across partitions |
| Adding a partition preserves order for existing keys | False for keys not already co-located; rebalancing during expansion can reorder relative to a consumer mid-rebalance |
| A consumer processing sequentially preserves partition order | True if it never reorders and never commits ahead of processing |
| Global order across a topic | Not provided. It is not a property Kafka has |

Ordering across a rebalance: if partition 3 moves from consumer A to consumer B, consumer B starts at the committed offset. If A committed an offset past records it had not finished, B starts after unfinished work. This is why commit-after-processing matters.

## Retention

- Retention is time-based (`retention.ms`, `log.retention.hours` for the older key) or size-based (`retention.bytes`), per topic or per log segment. It is independent of consumer position: a slow consumer does not extend retention, so a consumer that falls far behind can find its next offset already deleted, which surfaces as `OffsetOutOfRange` on reset.
- The cleanest default for event data is `cleanup.policy=delete` with a retention window matched to your longest replay requirement.
- For a compacted topic (`cleanup.policy=compact`), records with the same key are eventually collapsed to the latest, keeping the log a "latest value per key" map. Compaction runs in the background and is not instantaneous; a consumer can still read older versions during compaction. Do not treat a compacted topic as a key/value store for correctness-critical reads.

## When Kafka is the wrong tool

- You need per-message visibility timeout, re-drive, and a dead-letter API. Kafka has offsets, not message-level state.
- You need flexible routing: fanout by content, priority queues, per-message TTL. Exchanges and queue policies do that; partitions do not.
- Your volume is small and you do not want to operate a cluster. A managed queue is cheaper in attention.
- You need exactly one consumer per message with a clear completion signal. Kafka's model is "each group reads everything", which is the opposite shape.

When Kafka is the right tool:

- High-throughput durable event log with many independent consumers.
- Replay: reset a consumer group to an earlier offset and reprocess.
- Ordering within an entity, provided you key by that entity.
- Decoupling producers from consumers so neither side has to be available when the other is.

## Operational checks

```bash
# Describe a topic: partitions, leader, ISR, and config.
kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic orders

# Consumer group lag (offsets and lag per partition).
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group billing --topic orders

# Reset a consumer group to replay from the beginning.
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group billing --topic orders \
  --reset-offsets --to-earliest --execute
```

```text
Consumer config keys worth knowing (all real, from the Kafka consumer configuration docs):
  group.id
  group.instance.id
  enable.auto.commit
  auto.commit.interval.ms
  auto.offset.reset
  partition.assignment.strategy
  max.poll.records
  max.poll.interval.ms
  session.timeout.ms
  isolation.level
  enable.auto.offset.store
```

`max.poll.interval.ms` is the rebalance trigger. If processing a batch takes longer than this, the broker assumes the consumer died and reassigns its partitions, and the original consumer's next commit fails with a fencing error. Raise `max.poll.interval.ms` or lower `max.poll.records` or process within a loop.

## References

- Kafka protocol documentation - https://kafka.apache.org/protocol
- Kafka consumer configuration - https://kafka.apache.org/documentation/#consumerconfigs
- Kafka producer configuration - https://kafka.apache.org/documentation/#producerconfigs
- Kafka design (the Log, the replication model) - https://kafka.apache.org/documentation/#design
- Kafka consumer groups protocol guide - https://kafka.apache.org/protocol#protocol_consumer_management
- Confluent documentation on delivery semantics - https://docs.confluent.io/platform/current/installation/configuration/producer-configs.html
- AWS MSK getting started - https://docs.aws.amazon.com/msk/latest/developerguide/what-is-msk.html
- Google Cloud Pub/Sub ordering documentation (a different system, useful comparison) - https://cloud.google.com/pubsub/docs/ordering