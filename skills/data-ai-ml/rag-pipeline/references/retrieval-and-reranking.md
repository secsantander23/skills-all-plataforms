# Retrieval and Reranking

## The two-stage shape

```
  query
    |
    v
  stage 1: cheap, broad recall     (ANN vector search + lexical search + filters)
    |  50 to 200 candidates
    v
  fusion                            (reciprocal rank fusion or weighted scores)
    |  20 to 50 candidates
    v
  stage 2: expensive, precise       (cross-encoder rerank)
    |  3 to 10 chunks
    v
  context assembly
```

Reranking cannot find what stage 1 missed. It only reorders. If recall@100 is low, raising
final_k from 5 to 10 is not a reranking problem.

## Reciprocal rank fusion

```python
def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[str]:
    """rankings: several lists of ids, each already ordered best-first."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
```

Why fusion beats score blending: raw scores from a vector store and from BM25 are not on the
same scale and change with the query. Rank is stable, so fusing ranks is robust and nearly
free to tune.

## Cross-encoder rerankers

A bi-encoder embedding encodes the query and the document independently, then compares vectors.
A cross-encoder encodes the query and document **together**, so it can model their interaction
directly. That is far more accurate but costs one forward pass per candidate, which is why it
runs on a small candidate set.

Practical notes:

- Batch candidates. Cross-encoder rerankers are fast when batched.
- Trim each candidate before scoring. Rerankers have input limits and long candidates dilute.
- Score and sort, then take top-k. Do not return the reranker's ordering alone; it has no
  notion of diversity or metadata constraints.
- Choose a reranker model in your languages and domain, and re-evaluate when you change it.

## Query-side preprocessing

Cheap wins before you touch the index:

- **Filter by metadata early.** Tenant, doc type, and date cuts the candidate space and cost.
- **Expand acronyms** your users type but documents do not.
- **Strip conversational filler** ("hey can you tell me about...").
- **Detect a non-question.** A user pasting a stack trace wants search, not generation.

Multi-query and HyDE style techniques exist and can help recall on hard questions. They also
multiply latency and cost. Use them for a measured failing class, not by default.

## Diversity

If you return top-k by score, the results are often five chunks of the same document. Apply a
per-document cap and a similarity cap before selecting the final context.

```python
def diversify(hits: list[Hit], max_per_doc: int = 2, k: int = 6) -> list[Hit]:
    per_doc: dict[str, int] = {}
    out: list[Hit] = []
    for hit in hits:
        seen = per_doc.get(hit.source, 0)
        if seen >= max_per_doc:
            continue
        per_doc[hit.source] = seen + 1
        out.append(hit)
        if len(out) >= k:
            break
    return out
```

## Tuning procedure

1. Build a query set with known relevant chunk ids (from real logs, or hand-labeled).
2. Measure recall@k at the candidate stage. Target depends on corpus difficulty; measure the
   ceiling you can reach by tuning `k_dense`, `k_lexical`, filters, and fusion.
3. If the ceiling is still low, the fix is the index, embeddings, or chunking. Not reranking.
4. Once the candidate ceiling is high, add reranking and tune `final_k` on answer quality.
5. Report both numbers in every experiment write-up. A change that raises recall but lowers
   groundedness is a regression.

## Common retrieval bugs

- **Filters applied after the vector search** instead of inside it, so you retrieve across
  tenants and filter down to nothing.
- **Embedding the query with different preprocessing than the documents.** Whitespace, casing,
  and truncation differences silently degrade similarity.
- **Empty query embedding** on short inputs. Guard against zero-length queries.
- **Top-k too small for a hard question**, then concluding RAG does not work on that topic.
- **No dedupe**, so the same document fills the whole context.