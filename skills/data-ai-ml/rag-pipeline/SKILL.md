---
name: RAG Pipeline
description: Covers ingestion and chunking, embedding and vector index choices, semantic versus hybrid retrieval, reranking, context assembly and citations, grounding and hallucination mitigation, retrieval and generation evaluation metrics, incremental updates, and common RAG failure modes. Use when the user asks about building or debugging a retrieval-augmented generation system, tuning chunk size or top-k, choosing a vector store, reducing hallucinated answers, or measuring whether RAG is actually working.
metadata:
  opencode/autoinvoke: true
---

# RAG Pipeline

Retrieval-Augmented Generation is two systems that people keep describing as one. Retrieval
finds evidence; generation turns evidence into an answer. When a RAG feature is wrong, almost
always one of the two halves is wrong, and almost always you can tell which by measuring them
separately. Build the measurement first.

```
  INGEST        ->  CHUNK  ->  EMBED   ->  INDEX
  sources          units      vectors     searchable store
                                                   |
  ANSWER  <-  GENERATE  <-  CONTEXT  <-  RETRIEVE <-+
              grounded      assembled     ranked
```

## When to use this

- You are grounding a model on your own documents, database rows, or tickets.
- Answers are confident but wrong, and you suspect retrieval rather than the prompt.
- The corpus changes frequently and re-indexing everything is too slow or too expensive.
- You need citations or evidence in the output for review or compliance.
- You need to decide whether RAG is the right tool at all versus fine-tuning or a better search.

## First question is always "is this RAG or is this a lookup"

| Situation | Better tool |
|---|---|
| Structured rows filtered by exact columns | SQL query against a warehouse or OLTP database |
| Exact identifiers, codes, error strings | Keyword or exact-match search, not embeddings |
| Long documents where the answer is one fact | RAG with narrow, well-chunked retrieval |
| Questions requiring synthesis across many documents | RAG with reranking, or structured aggregation |
| Stable behavior that must be exactly repeatable | Code, not a model |
| Domain style or format, not new facts | Fine-tuning or prompt examples |

Embeddings are lossy. They compress meaning into a vector and cannot reliably reproduce an
exact token. If the user will type an order number, a stack trace, or a legal citation, give
them lexical search.

## Pipeline overview

```
sources (pdf, html, db, tickets)
   |  extract + clean + dedupe
   v
normalized documents  --->  metadata (source, title, url, updated_at, ACL)
   |  chunk
   v
chunks (id, text, parent_id, metadata, embedding)
   |
   +--> dense index  (vector search)
   +--> lexical index (BM25 / full text)
   +--> metadata filters
   |
   v
retrieval: fuse candidates -> rerank -> top-k
   |
   v
context assembly (budget, order, dedupe, parent expansion)
   |
   v
generation with grounding instructions + citation mapping
   |
   v
answer + evidence ids + abstention when evidence is weak
```

## Ingestion and chunking

Chunking decides what the retriever can find. Everything downstream is bounded by it. If a
fact never appears inside a single chunk, no embedding model will recover it.

| Strategy | Good for | Failure mode |
|---|---|---|
| Fixed-size windows | Plain prose, unknown structure | Splits tables, lists, and definitions mid-thought |
| Recursive split on separators | Markdown, HTML, prose | Still breaks code and tables |
| Structure-aware | Headings, sections, legal clauses | Requires clean source markup |
| Semantic / topic | Long argumentation, transcripts | Expensive, unstable chunk boundaries |
| Parent-child (small embed, large return) | Long documents, precise citations | More index entries |
| Row-per-record | Database tables, tickets | Row must be self-contained to be useful |
| Table / metadata sidecar | Tables and lists | Facts separated from their header |

Practical rules:

- **Start at 400 to 800 tokens per chunk with overlap of 10 to 20 percent**, then tune on your
  eval set. These are starting points, not recommendations.
- **Prefer structure over length.** A section under its own heading beats an arbitrary window.
- **One idea per chunk.** If a chunk needs "and" to summarize, split it.
- **Prepend context.** Put the document title, section path, and source id at the top of each
  chunk. Small models cannot infer that "the table above" refers to a different document.
- **Carry metadata on every chunk.** Source, title, url, updated_at, tenant or ACL, and a stable
  chunk id you can cite.

```python
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str
    # "Section 3.2 > Deployment > Rolling updates"
    section_path: str
    source_url: str
    updated_at: str
    tenant_id: str
    token_count: int
    parent_id: str | None = None


@dataclass
class Chunker:
    max_tokens: int = 512
    overlap_tokens: int = 64

    def split(self, section_text: str) -> list[str]:
        """Split one section on sentence boundaries with token-budget overlap."""
        pieces = _split_sentences(section_text)
        out: list[str] = []
        current: list[str] = []
        current_len = 0
        for piece in pieces:
            piece_len = _rough_token_count(piece)
            if current and current_len + piece_len > self.max_tokens:
                out.append(" ".join(current))
                # Seed the next chunk with the tail of the current one.
                tail: list[str] = []
                tail_len = 0
                while current and tail_len < self.overlap_tokens:
                    candidate = current.pop(0)
                    tail.append(candidate)
                    tail_len += _rough_token_count(candidate)
                current = list(tail)
                current_len = tail_len
            current.append(piece)
            current_len += piece_len
        if current:
            out.append(" ".join(current))
        return out


def _split_sentences(text: str) -> list[str]:
    """Very small sentence splitter. Swap for a real segmenter for production."""
    return [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]


def _rough_token_count(text: str) -> int:
    """Whitespace approximation. Use the provider tokenizer for real budgets."""
    return max(1, len(text) // 4)
```

For PDFs and other documents with layout, prefer libraries that extract structure rather than
a naive text dump: a page dump produces headers, footers, and column noise that pollutes every
embedding downstream.

## Embeddings

| Question | Guidance |
|---|---|
| Which model? | Evaluate on your own corpus with a retrieval metric, not on a public leaderboard. Multilingual needs need a multilingual model. |
| Which dimension? | Start with the model's default. Reducing dimension trades recall for storage and speed. |
| Normalize? | Most libraries normalize internally; cosine similarity assumes it. Check the model's documentation. |
| What to embed? | Title plus body, or body alone. Test both. |
| Re-embed on model change? | Yes, always. Vectors from different models are not comparable. |

Document every embedding model with the corpus. Mixing vector spaces in one index silently
destroys recall and looks like a tuning problem.

```python
import os

from openai import OpenAI

client = OpenAI()
EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    batch_size = 256
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        out.extend(item.embedding for item in resp.data)
    return out
```

Check the embedding endpoint reference for batch limits, token limits, and dimension options
before you size your ingestion job.

## Index types

| Index | Query | Strengths | Weaknesses |
|---|---|---|---|
| Flat / exact | Brute-force distance | Exact, no training, great under ~1M vectors | Linear in corpus size |
| IVF (inverted file) | K-means cell then scan | Fast at scale | Needs training, recall depends on `nprobe` |
| HNSW | Graph walk | Fast, high recall, common default | Memory heavy, build cost, no native deletion story |
| PQ (product quantization) | Compressed vectors | Very large corpora, low memory | Measurable recall loss |
| BM25 / inverted index | Lexical scoring | Exact terms, ids, code, rare words | Misses synonyms and paraphrase |
| Hybrid | Both, fused | Best of each | More moving parts |

Two practical notes:

- Approximate indexes trade recall for speed, and the trade is controlled by a build or query
  parameter. **Measure recall before you decide you need a bigger index.** Many "we need a
  better vector database" conclusions are really "our recall was 0.6 and we never looked".
- Plan for deletion up front. Some index types make it expensive to remove vectors. If your
  corpus must honor deletes or tenant offboarding, verify the store's deletion semantics before
  you commit.

## Retrieval

Start simple: dense top-k, filter by tenant, done. Then add complexity only when a measured
failure justifies it.

| Technique | Fixes |
|---|---|
| Dense only | Paraphrase, semantic similarity, cross-language |
| Lexical only | Exact ids, codes, rare names, error strings |
| Hybrid fusion | Both of the above |
| Metadata filtering | Scope, tenant, recency, doc type |
| Query expansion | Vocabulary mismatch between question and document |
| Multi-query | Recall on questions with several valid phrasings |
| Reranking | Precision: surfaces the actually relevant chunk into the top few |
| Parent expansion | Context that was split across chunks |

Reranking is usually the highest-leverage single addition. A cross-encoder reads the query and
a candidate together and scores relevance far more accurately than a bi-encoder embedding,
which encodes them independently. The cost is that you can only rerank the candidates you
already retrieved, so it composes with a broad first pass rather than replacing it.

```python
from dataclasses import dataclass


@dataclass
class Hit:
    chunk_id: str
    text: str
    score: float
    source: str
    metadata: dict


def retrieve(
    question: str,
    vector_index,
    keyword_index,
    tenant_id: str,
    k_dense: int = 30,
    k_lexical: int = 30,
    final_k: int = 6,
) -> list[Hit]:
    dense = vector_index.search(
        vector=embed_texts([question])[0],
        top_k=k_dense,
        filters={"tenant_id": tenant_id},
    )
    lexical = keyword_index.search(question, top_k=k_lexical, filters={"tenant_id": tenant_id})

    # Reciprocal rank fusion. Simple, parameter-light, and hard to get wrong.
    rrf: dict[str, float] = {}
    for rank, hit in enumerate(dense, start=1):
        rrf[hit.chunk_id] = rrf.get(hit.chunk_id, 0.0) + 1.0 / (60 + rank)
    for rank, hit in enumerate(lexical, start=1):
        rrf[hit.chunk_id] = rrf.get(hit.chunk_id, 0.0) + 1.0 / (60 + rank)

    pool = {h.chunk_id: h for h in dense + lexical}
    candidates = [pool[cid] for cid, _ in sorted(rrf.items(), key=lambda kv: -kv[1])]
    return rerank(question, candidates)[:final_k]


def rerank(question: str, candidates: list[Hit], top_n: int = 6) -> list[Hit]:
    """Plug in a cross-encoder reranker. Model choice depends on your language and budget."""
    raise NotImplementedError("wire in a reranker here")
```

The fusion constant `60` is the conventional rank offset in reciprocal rank fusion. Tune it if
you have a reason to, but treat any value in the same order as equivalent in practice.

## Context assembly

This step is where careful work gets thrown away.

- **Budget, do not truncate blindly.** Fill the context window deliberately. Drop the lowest
  scoring chunks, not the tail of the highest scoring one.
- **Order deliberately.** Models attend differently to the start and end of a long context.
  Put the strongest evidence where it will be read, and put the instruction last.
- **Deduplicate.** Near-identical chunks from one document waste budget and can skew the answer.
- **Attach citations at assembly time.** Keep the mapping from answer span to chunk id before
  you hand the text to the model. Retrofitting provenance is painful.
- **Expand to parent when a chunk was a fragment.** A retrieved sentence is often useless without
  its section.

```text
Answer the question using only the sources below. Cite the source id after each claim.
If the sources do not answer the question, reply exactly: NOT_FOUND.

Sources:
[S1] (handbook.pdf, Section 4.2, updated 2026-01-11)
...chunk text...
[S2] (runbook.md, Rolling updates)
...chunk text...
```

## Grounding and hallucination

Ranked mitigations, strongest first:

1. **Require refusal.** Give the model a cheap, explicit way to say "not in the sources" and
   measure how often it takes it. A system that never abstains is not grounded.
2. **Constrain the answer shape.** For extraction, force a schema. Unstructured answers drift.
3. **Quote before paraphrase.** Ask the model to return the exact supporting sentence with the
   answer. Then verify the quote actually appears in the retrieved chunk, in code.
4. **Verify citations programmatically.** Check that every cited chunk id was actually returned
   by the retriever, and that the cited text exists in that chunk.
5. **Freshness gates.** Exclude chunks older than a domain-appropriate cutoff, or route to a
   human when the only evidence is stale.
6. **Pre-filter with a classifier** for obvious out-of-domain questions. Cheaper than letting the
   model reason about it.

```python
def citations_are_real(answer_citations: list[str], retrieved: list[Hit]) -> bool:
    allowed = {h.chunk_id for h in retrieved}
    return all(cid in allowed for cid in answer_citations)


def quotes_are_real(quotes: list[str], retrieved: list[Hit]) -> list[str]:
    """Return the quotes that could not be found verbatim in their cited chunk."""
    by_id = {h.chunk_id: h.text for h in retrieved}
    bad: list[str] = []
    for quote, chunk_id in quotes:
        normalized = " ".join(quote.split()).lower()
        source = " ".join(by_id.get(chunk_id, "").split()).lower()
        if normalized not in source:
            bad.append(quote)
    return bad
```

## Evaluation: measure both halves

This is the single most useful habit in RAG. A correct answer with wrong evidence and a wrong
answer with correct evidence are different bugs with different fixes.

| Layer | Metric | What it tells you |
|---|---|---|
| Retrieval | Recall@k over known relevant chunks | Is the evidence in the candidate set? |
| Retrieval | MRR or nDCG | Is the right chunk near the top? |
| Retrieval | Answer-support rate | Does a retrieved chunk actually contain the answer string? |
| Generation | Faithfulness / groundedness | Is every claim supported by a cited chunk? |
| Generation | Answer correctness | Is it right, against a reference or a judge? |
| Generation | Citation precision | Do the cited chunks support the claim? |
| Generation | Abstention quality | Correct refusal rate on unanswerable questions |
| End to end | Task success | Did the user get what they came for |

Build the retrieval eval first. It is cheap, deterministic, and it tells you where to spend
effort. If recall is low, no amount of prompt work helps. If recall is high and faithfulness is
low, the problem is generation.

## Incremental updates

Re-embedding a whole corpus on every change is the usual reason RAG projects stall.

- **Content hash each source.** Skip unchanged documents.
- **Delete by document, not by chunk, when the store allows it.** Store `doc_id` on every chunk
  so re-ingest can remove exactly the previous version.
- **Version and tombstone.** Write the new version, then mark the old one deleted. Do not
  silently overwrite.
- **Queue-based ingestion.** New and changed sources land on a queue and are processed
  asynchronously, so the write path stays fast and ingestion failures are retried.
- **Re-embed on model change.** Version the embedding model alongside the corpus. Vectors from
  different models cannot be mixed.

```text
source change
   |
   v
content hash differs from stored hash?  --no-->  done
   | yes
   v
enqueue re-ingest for doc_id
   |
   v
embed new chunks -> upsert -> mark old chunks of doc_id deleted
   |
   v
update stored hash and embedding_model_version
```

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Model ignores a fact that is clearly in the corpus | Chunk never retrieved, or chunk has no metadata | Check recall@k before anything else |
| Answers cite sources that do not say that | Context too long, or no citation constraint | Shrink top-k, require quote-then-claim |
| Answers are confidently wrong on recent data | Stale chunks ranking above fresh ones | Add recency to scoring or filter |
| Great on demos, poor on real users | Eval set is synthetic | Build the eval set from real queries |
| Only exact keyword queries work | Dense index is beating a lexical index at exact match | Add hybrid retrieval |
| One tenant sees another tenant's documents | Metadata filter missing on the query path, not the index path | Enforce the filter in code, test it |
| Costs explode as the corpus grows | Everything embedded, nothing filtered | Filter before embedding, cache |
| Index build takes hours | Recomputing everything on every run | Content hashing plus incremental upsert |
| Retrieval is fine, quality is poor | Prompt or model, not retrieval | Move effort to the generation half |

## Anti-patterns

- **"Add more chunks."** More context past the useful point reduces accuracy and raises cost.
  Retrieve less, retrieve better.
- **One giant chunk per document.** Embeddings average over long text and lose the specific fact.
- **Tuning the prompt when recall is broken.** First prove the evidence is retrieved.
- **No abstention path.** A system that must answer something is a system that will hallucinate.
- **Citations generated by the model and trusted blindly.** Verify them in code.
- **Embedding model swaps without a full re-embed.** Silently destroys recall.
- **Storing chunks with no source id.** You cannot cite, deduplicate, or delete later.
- **Treating the vector store as the system of record.** The database is. The index is derived.
- **Filtering only at the query layer of the UI.** Authorization belongs in the retrieval call.

## Checklist

- Chunk strategy matches source structure, not a fixed constant.
- Every chunk carries source, title, section, url, updated_at, and tenant metadata.
- Hybrid retrieval is in place if users type exact ids or codes.
- Recall@k is measured on a real eval set before any prompt work.
- Context assembly has a deliberate budget, order, and dedupe policy.
- The generation prompt has an explicit refusal instruction.
- Citations are verified against retrieved chunk ids in code.
- Incremental re-ingest is keyed on a content hash.
- Tenant and ACL filters are enforced in the retrieval call and covered by tests.
- Embedding model version is recorded with the corpus.

## References

- OpenAI embeddings: https://platform.openai.com/docs/guides/embeddings
- OpenAI embeddings API reference: https://platform.openai.com/docs/api-reference/embeddings
- OpenAI file search tool: https://platform.openai.com/docs/guides/tools-file-search
- Anthropic search results content blocks: https://docs.anthropic.com/en/docs/build-with-claude/search-results
- Anthropic citations: https://docs.anthropic.com/en/docs/build-with-claude/citations
- Gemini embeddings: https://ai.google.dev/gemini-api/docs/embeddings
- Gemini file search: https://ai.google.dev/gemini-api/docs/file-search
- Anthropic prompt caching (context reuse): https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Model Context Protocol for tool-accessible retrieval: https://modelcontextprotocol.io/
- NIST AI Risk Management Framework, Generative AI profile: https://www.nist.gov/itl/ai-risk-management-framework