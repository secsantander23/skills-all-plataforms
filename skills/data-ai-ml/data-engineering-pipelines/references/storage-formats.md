# Storage Formats, Partitioning, and Table Formats

## Format selection

| Format | Layout | Reads | Writes | Updates | Choose when |
|---|---|---|---|---|---|
| CSV / JSON | text | full scan | trivial | rewrite | Interchange only |
| Parquet | columnar | column pruning | moderate | rewrite | Analytical scans |
| ORC | columnar | column pruning | moderate | rewrite | Hive-era stacks |
| Avro | row-oriented | full record | trivial | record level | Streams with schema evolution |
| Delta Lake | columnar plus transactions | column pruning | moderate | row level | Concurrent writes, mixed engines |
| Iceberg | columnar plus snapshots | column pruning | moderate | row level | Many concurrent readers |

Row-oriented formats are right when you read and update whole records one at a time. Columnar
formats are right when you read a few columns across millions of rows. Using Parquet for a
point lookup gives up row-level updates for no benefit.

## Columnar mechanics worth knowing

A columnar file is a sequence of row groups. Each row group stores column chunks plus per-column
statistics (min, max, and null counts).

- **Column pruning** happens at the file level: a query reading two of fifty columns reads
  roughly two columns of bytes.
- **Predicate pushdown** uses the statistics to skip row groups entirely. It only helps when the
  filter column is stored, and when the values are well clustered.
- **Compression** is applied per column, so columns that repeat (status, country) compress well
  and free-form text compresses moderately.
- Writing sorted by a common filter column makes predicate pushdown dramatically more effective.

## File size and the small-files problem

Target 128 MB to 1 GB per file. Symptoms of being outside that range:

| Symptom | Cause | Fix |
|---|---|---|
| Job time scales with file count | Many small files | Compact to the target size within the same partition |
| Metadata operation timeouts | Huge partition or table counts | Reduce partition count, compact |
| Memory pressure during planning | Many open files | Compact, and raise the reader's file descriptor limit |
| Reads far slower than the bytes suggest | Many tiny files | Same as above |

Compaction must write into the **same partition**. Writing compacted output into a differently
partitioned path breaks pruning and splits the data into two places readers do not expect.

## Partitioning rules

1. Partition on the columns queries actually filter by. Check your query logs.
2. Keep the partition count bounded. Millions of partitions strain metastores and planners
   everywhere.
3. Never partition on a high-cardinality column. User id as a partition means one directory per
   user.
4. Target at least about 1 GB of data per file, which implies choosing a date grain that keeps
   volumes reasonable. Daily is a common starting point; hourly only if volume justifies it.
5. Prefer partition columns with low null rates. A null partition is a bucket that grows without
   bound.
6. Test pruning explicitly: compare the bytes scanned with and without the filter. Query plans
   that show no partition pruning are a silent 100x regression.

Static versus dynamic partitioning:

| | Static | Dynamic |
|---|---|---|
| Destination | Fixed in the query | Computed per record |
| Use when | One partition per run (a date from the schedule) | Many partitions per run |
| Risk | Data lands in the wrong partition if the run date is wrong | Mixed writers to one partition |

Mixing the two for the same table is a reliable way to create two directories that look like the
same partition and silently double-count.

## Open table formats

Add an open table format when you need one or more of:

- Concurrent writers without one winning.
- Row-level updates and deletes.
- Snapshot isolation for readers.
- Schema enforcement that rejects incompatible writes.
- Time travel: read the table as it was at a prior timestamp.
- Partition evolution: change the partition scheme without rewriting everything.
- More than one engine reading and writing the same table.

Delta Lake and Apache Iceberg cover this space. Both are open; both are supported by several
query engines. Verify current engine support for your specific combination before committing.

## Writing safely

Readers must never observe a partial file. Two safe patterns:

1. Write to a temporary name, then atomically rename or copy into the final name.
2. Write into a staging directory and publish a manifest that names the completed files.

Never write in place into a location a reader may be scanning.

## Delta Lake example

```python
import delta
import pandas as pd

# One writer per table at a time, enforced by a lock or optimistic concurrency.
df = pd.read_parquet("incoming/events/")
(
    delta.write_deltalake(
        "warehouse/events",
        df,
        mode="append",
        partition_by=["event_date"],
        schema_mode="merge",  # evolve the schema as columns appear
    )
)
```

Verify the `mode`, `partition_by`, and `schema_mode` arguments against the library's own
documentation for your version before relying on them.

## Row-level update example

```sql
-- Iceberg-style: DML against the table, with the engine handling the commit protocol.
DELETE FROM warehouse.customers WHERE deleted_at IS NOT NULL;

UPDATE warehouse.orders
SET status = 'cancelled'
WHERE id IN (SELECT order_id FROM warehouse.cancellations);
```

The point of the table format is that this works safely with concurrent readers and writers. On
plain Parquet in object storage, the same statement is a rewrite of the whole partition and is
racy against anyone else reading it.