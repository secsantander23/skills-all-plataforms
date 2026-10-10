# Multi-Agent Patterns

## Choosing a pattern

| Pattern | Extra model calls | Shared state | Best for |
|---|---|---|---|
| Router | 1 | None | Distinct domains, simple intent split |
| Supervisor | Many | Supervisor holds the context | Workers that need the thread |
| Parallel fan-out | N, concurrent | Aggregator | Independent subtasks |
| Sequential | Fixed | Passed as output | Refine-in-stages |
| Debate or critique | N, multiplicative | Shared | Judgment where one pass is weak |
| Recursive decomposition | Unbounded | Tree | Open-ended research |

Start with router. Move to supervisor only when a worker genuinely needs the conversation
thread. Parallel only when subtasks are truly independent and write to disjoint destinations.

## Router

```python
ROUTE_PROMPT = """Classify the request into exactly one route.

billing    invoices, refunds, payment methods, charges
technical  errors, outages, API behavior, integrations
account    login, permissions, seats, profile
other      anything else

Reply with one word.
"""


def route(user_text: str, classifier) -> str:
    verdict = classifier(ROUTE_PROMPT, user_text)
    return verdict if verdict in ROUTES else "other"
```

Always validate the classifier output against the allowed set and fall back. A free-form route
string means a whole sub-agent system is unreachable.

## Supervisor

```
        supervisor
        /    |     \
   workerA workerB workerC
        \    |     /
        supervisor (synthesize)
```

- The supervisor holds the goal and the accumulated findings.
- Workers receive a scoped goal, their own tool set, and a strict output contract.
- Workers return structured findings, not transcripts.
- The supervisor has a step budget across all workers, not per worker.

Failure modes: supervisor context grows unbounded; a worker returns prose the supervisor cannot
use; two workers contradict each other and the supervisor picks arbitrarily. Define the output
contract up front and let the supervisor surface unresolved conflicts instead of inventing a
resolution.

## Parallel fan-out

```python
import asyncio


async def fan_out(tasks: list[asyncio.Task]) -> list[object]:
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception)]
```

Rules:

- Workers write to disjoint destinations, or you have lost-update bugs.
- `return_exceptions=True` so one failure does not cancel the batch.
- Cap concurrency. Unbounded fan-out against a rate-limited API fails.
- Aggregate deterministically, and label each result with its source.

## Cost

Multi-agent cost is multiplicative. A supervisor making five calls, each worker making six tool
calls, is thirty-one model calls before synthesis. That is fine if it measurably beats a single
agent with a better prompt, and wasteful otherwise. Benchmark the single-agent version first and
keep the numbers.

## Coordination

| Problem | Symptom | Fix |
|---|---|---|
| Lost update | Two agents write the same record | Ownership by key, or a lock per key |
| Duplicated work | Two agents do the same task | Deterministic partitioning by input |
| Contradiction | Workers report different values | One authority per fact, others report references |
| Zombie run | Worker died, supervisor waits | Per-worker deadline and a terminal state |
| Unbounded nesting | Depth grows at runtime | Hard depth limit of two |

## When not to use it

- The routing decision is a handful of enum values: use a switch.
- The steps are known and fixed: use a pipeline.
- Latency budget is tight and the work is small: parallel calls have overhead.
- You cannot evaluate it: without an eval set, extra agents make failures harder to locate.

Multi-agent is a response to a measured limitation of a single agent, not a default setting.