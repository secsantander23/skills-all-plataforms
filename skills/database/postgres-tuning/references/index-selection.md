# Index Selection and Maintenance Reference

## Does the planner even consider my index?

```sql
-- Enable per-node instrumentation for one session.
SET enable_seqscan = on;
SET enable_indexscan = on;
SET enable_bitmapscan = on;
SET enable_indexonlyscan = on;

-- Inspect what the planner thinks of each relation's indexes.
SELECT relname, indexrelname, idx_scan,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC;
```

An index with `idx_scan = 0` after a full day of representative traffic is a candidate for
removal, but confirm it is not used by rare monthly jobs, by a reporting replica, or by a
prepared statement that only ran before statistics were loaded.

## Index that is never used because of the query shape

```sql
-- Predicate wrapped in a function: the index cannot be used.
SELECT * FROM orders WHERE date(created_at) = DATE '2024-01-01';
-- Fix with an expression index:
CREATE INDEX orders_created_date_expr_idx ON orders (date(created_at));

-- Implicit type cast on the indexed column.
-- The planner must coerce, which can defeat a text index.
SELECT * FROM users WHERE email = 'a@example.com'; -- email is citext or text

-- Leading wildcard.
SELECT * FROM documents WHERE title LIKE '%migration%';
-- Fix with a trigram GIN index, not a B-tree.
```

## Creating indexes without blocking production

```sql
CREATE INDEX CONCURRENTLY orders_customer_created_idx ON orders (customer_id, created_at DESC);
```

- `CONCURRENTLY` takes two table passes and waits for existing transactions, so it is slow
  and it cannot run inside a transaction block. Most migration tools need an explicit opt-out
  from transactional wrapping for this statement.
- If it fails or is cancelled, it leaves an invalid index that the planner will ignore.
  Remove it with `DROP INDEX CONCURRENTLY` and retry.
- Drop one with `DROP INDEX CONCURRENTLY orders_customer_created_idx;`

## Reducing index write cost

- `INCLUDE` columns cost leaf-page space but do not become seek keys.
- `fillfactor` leaves free space in index pages so updates can stay on-page:
  ```sql
  CREATE INDEX sessions_token_idx ON sessions (token) WITH (fillfactor = 80);
  ```
  This helps on tables with a high update rate and is useless on append-only tables. It costs
  space.
- `REINDEX INDEX CONCURRENTLY name;` rebuilds without blocking reads.

## Index bloat and measurement

```sql
-- Rough bloat estimate for a B-tree. Build the extension first:
-- CREATE EXTENSION IF NOT EXISTS pgstattuple;
-- SELECT * FROM pgstatindex('orders_customer_created_idx');
```

Without extensions, compare index size against the live tuple count:

```sql
SELECT relname,
       n_live_tup,
       pg_size_pretty(pg_relation_size(relid)) AS heap_size,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;
```

`pg_total_relation_size` minus the heap size is the index plus TOAST plus bloat. A ratio far
above what your index definitions imply means vacuum is not keeping up, not that you need a
smaller type.

## Statistics depth and correlation

```sql
-- Set statistics target per column when a column has a long tail or few distinct values
-- that the planner keeps misestimating.
ALTER TABLE orders ALTER COLUMN customer_id SET STATISTICS 1000;

-- Correlation near +/-1 means physical order matches logical order (good for BRIN and
-- for sequential access). Near 0 means random, which is the worst case for BRIN.
SELECT attname, correlation
FROM pg_stats
WHERE tablename = 'events'
ORDER BY abs(correlation) DESC;
```

## Searchable and containment indexes

```sql
-- tsvector with an explicit configuration so the expression is IMMUTABLE.
ALTER TABLE documents
    ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english'::regconfig, coalesce(title, '') || ' ' || coalesce(body, ''))
    ) STORED;

CREATE INDEX documents_search_vector_idx ON documents USING gin (search_vector);

SELECT id, title
FROM documents
WHERE search_vector @@ plainto_tsquery('english'::regconfig, 'zero downtime migration');
```

```sql
-- JSONB containment and key existence.
CREATE INDEX documents_body_gin_idx ON documents USING gin (body jsonb_path_ops);

SELECT id FROM documents
WHERE body @> '{"status": "published"}'::jsonb;
```

`jsonb_path_ops` gives a smaller, faster index for containment operators at the cost of not
supporting the key-existence operators. Read the GIN operator-class matrix in the official
GIN documentation before choosing.

## Trigram similarity

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX users_name_trgm_idx ON users USING gin (name gin_trgm_ops);

SELECT id, name, similarity(name, 'Jhon Smyth') AS score
FROM users
WHERE name % 'Jhon Smyth'          -- similarity above the pg_trgm threshold
ORDER BY score DESC
LIMIT 10;
```

## Text search configuration

```sql
CREATE INDEX documents_fts_idx
    ON documents
    USING gin (to_tsvector('simple'::regconfig, body));

SELECT ts_rank(to_tsvector('simple'::regconfig, body),
               plainto_tsquery('simple'::regconfig, 'migration rollback'))
FROM documents
WHERE to_tsvector('simple'::regconfig, body)
      @@ plainto_tsquery('simple'::regconfig, 'migration rollback');
```

Prefer a stored generated column over repeating the expression, so the planner can match the
index expression exactly.

## Foreign keys and indexes

- The referencing table needs an index on the foreign key columns if you ever delete or
  update parent rows, or join starting from the child.
- The referenced table needs a unique index on the referenced key, which its primary key
  already provides.
- Missing indexes on the child cause cascading deletes to scan the child table for every
  deleted parent row. That shows up as one very slow `DELETE`.
