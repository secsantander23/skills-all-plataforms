# RAG Evaluation

## Layer 1: retrieval

Needs no model. Build it first.

```python
from dataclasses import dataclass


@dataclass
class EvalQuery:
    qid: str
    question: str
    relevant_chunk_ids: set[str]
    tenant_id: str


def recall_at_k(hits: list[Hit], relevant: set[str], k: int) -> float:
    top = {h.chunk_id for h in hits[:k]}
    if not relevant:
        return 0.0
    return len(top & relevant) / len(relevant)


def hit_rate_at_k(hits: list[Hit], relevant: set[str], k: int) -> float:
    return 1.0 if any(h.chunk_id in relevant for h in hits[:k]) else 0.0


def mrr(hits: list[Hit], relevant: set[str]) -> float:
    for rank, hit in enumerate(hits, start=1):
        if hit.chunk_id in relevant:
            return 1.0 / rank
    return 0.0
```

- **Recall@k**: fraction of required chunks that made the top k. This is the ceiling for
  groundedness. Low recall means generation cannot be right.
- **Hit rate@k**: did at least one relevant chunk appear. Easier to fix than full recall, and a
  good first milestone.
- **MRR / nDCG**: how high the best chunk ranked. Use nDCG when you have graded relevance.

Also track **answer-support rate**: does any retrieved chunk contain the reference answer string
(or a labeled supporting span)? It catches the case where the right document is retrieved but
the right chunk is not.

## Layer 2: generation

Needs either a reference answer or a judge.

| Metric | Definition | Watch for |
|---|---|---|
| Faithfulness | Fraction of claims supported by a cited chunk | Model cites chunk 3 for a claim in chunk 1 |
| Answer relevance | Does the answer address the question | Verbose answers that dodge |
| Correctness | Match to a reference, or a judge verdict | Judge bias toward verbosity |
| Citation precision | Cited chunks actually support the claim | Citation volume, not just citation presence |
| Citation recall | Claims that carry a citation at all | Unsupported sentences |
| Abstention accuracy | Refuses when unanswerable, answers when answerable | Both directions matter |

Build an **unanswerable** slice of the eval set. RAG systems are biased toward answering, and
without unanswerable cases you cannot measure that bias.

## LLM-as-judge practice

- Pin the judge model and its parameters; record them with every score.
- Ask for a verdict plus a one-sentence reason, so you can audit disagreements.
- Score a human-labeled subset regularly and report judge agreement.
- Do not let the judge see the retrieved context unless you are judging faithfulness — a judge
  with the answer will grade from memory.
- For faithfulness, prefer extracting claims from the answer first, then judging each claim
  against its cited chunk. Whole-answer grading hides unsupported sentences inside a good answer.

```python
CLAIMS_PROMPT = """Break the answer below into atomic factual claims.
Return one claim per line. Do not merge or split claims that were already atomic.

ANSWER:
{answer}
"""

JUDGE_PROMPT = """A claim was made from a retrieved document.

Claim: {claim}
Chunk: {chunk}

Is the claim supported by the chunk? Answer SUPPORTED or UNSUPPORTED, then one sentence why.
"""
```

## End to end

The metric that matters is task success in the product: did the user get a usable answer, and
did they accept it. Track:

- Answer acceptance or copy rate, if your UI allows it.
- Escalation to a human.
- Refinement clicks (user rephrasing immediately).
- Latency percentiles for the retrieval and generation phases separately.

## Sourcing the eval set

Best to worst:

1. Real query logs with clicks or accepted answers.
2. Tickets, support macros, and FAQ entries written by humans.
3. Questions written by a domain expert against the corpus.
4. Model-generated questions. Use them to find gaps, never as the final measurement.

Keep a held-out slice you never tune against. Report the split.

## Regression harness

Version the corpus, the chunker config, the embedding model version, and the index build. A
retrieval regression with no config change is usually stale or partially re-indexed data.
Store the build metadata with each eval run.