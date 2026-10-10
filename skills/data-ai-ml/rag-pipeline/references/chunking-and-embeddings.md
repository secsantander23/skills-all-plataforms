# Chunking and Embeddings In Depth

## Chunk size, honestly

There is no universally right chunk size. It depends on:

- Whether the answer is a local fact or requires synthesis across a section.
- How long your documents are and how structured they are.
- How much context budget the generation step can afford.
- The embedding model's input limit.

Tune it as a parameter of the retrieval eval, not as a constant of faith. Sweep a small grid
(256 / 512 / 1024 tokens, overlap 0 / 64 / 128) and record recall@k and answer-support rate.
Report what you measured on your corpus.

Small chunks:

- Higher precision per chunk, less dilution of the embedding.
- Risk: chunks become unanswerable fragments ("See the table above", "As noted in 3.2").
- Fix with parent-child retrieval: embed small, return the parent section.

Large chunks:

- Self-contained context, fewer chunks to retrieve.
- Risk: the embedding averages several topics, so the specific fact does not surface.
- Risk: less context budget per answer.

## Structure-aware chunking

```text
document
 ├── H1 Getting started
 │    ├── H2 Install            <- section boundary
 │    │    text...
 │    └── H2 Configure          <- section boundary
 │         text...
 └── H1 Troubleshooting         <- section boundary
      text...
```

Emit one chunk per deepest heading level that still exceeds the minimum size, and merge upward
when a section is too small to stand alone. Attach the heading path as `section_path` so the
retrieved text is self-describing.

## Parent-child (small-to-big)

```
index:   embeddings of small leaf chunks   -> precise matching
return:  the parent section of the leaf     -> usable context
```

This decouples matching granularity from context granularity, which is the main tension in
chunk sizing. Store `parent_id` on every leaf chunk.

## Tables, code, and lists

Never let a fixed splitter handle these.

- **Tables**: emit the header row with every row, or flatten the table to key-value sentences
  and keep the original as the returned parent.
- **Code**: chunk on top-level definitions, include the import block, and record the file path
  and symbol name.
- **Lists**: keep list items together with their lead-in sentence.

Flattening example for a table:

```
text: "Plan: Pro. Monthly price: 49 USD. Seats: 5. Support: email."
```

That sentence retrieves far better than a raw row of numbers.

## Metadata that pays for itself

Every chunk should carry:

| Field | Used for |
|---|---|
| `doc_id`, `chunk_id` | Citation, deletion, idempotent re-ingest |
| `source_url`, `title` | Citation display |
| `section_path` | Self-describing context |
| `updated_at` | Recency gating |
| `tenant_id` / `acl` | Authorization at query time |
| `content_hash` | Skip unchanged chunks |
| `embedding_model_version` | Safe model migration |
| `token_count` | Context budget math |

## Embedding model selection

Evaluate on your corpus:

1. Take 100 to 300 real queries.
2. For each, mark the chunks that a correct answer requires.
3. For each candidate embedding model, embed the corpus and measure recall@k and MRR.
4. Add the cost of re-embedding the entire corpus to the decision.

Watch for:

- **Language coverage.** An English-only model on a multilingual corpus fails quietly.
- **Domain vocabulary.** Embedding models trained on web text may under-index on your internal
  jargon. Include the jargon in the query-side text, or evaluate for it explicitly.
- **Dimension.** Higher dimension improves discrimination and costs storage and search time.
  Measure before reducing.
- **Contextual vs static models.** Some embeddings are computed with surrounding document
  context, which helps disambiguate short chunks but costs more at ingest.

## Updating the corpus

```
for each source in the feed:
    new_hash = hash(normalized_text(source))
    stored   = index_state.get(source.id)

    if stored is None:
        enqueue full_ingest(source)
    elif stored.content_hash != new_hash or stored.embedding_model_version != CURRENT_VERSION:
        enqueue re_ingest(source, doc_id=source.id)
    else:
        skip
```

`re_ingest` deletes every chunk with that `doc_id` before upserting the new set, so a shrunk
document does not leave orphaned chunks behind. Make the delete and upsert idempotent so a
replayed queue message is harmless.