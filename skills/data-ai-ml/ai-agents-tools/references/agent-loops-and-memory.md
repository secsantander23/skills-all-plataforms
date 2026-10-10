# Agent Loops, Memory, and Guardrails

## Loop variants

### Single shot

```text
input -> model -> structured output -> done
```

One round trip. The default for extraction, classification, and single lookups.

### Bounded loop

```text
input -> model -> (tool call | final answer)
                   ^                 |
                   | tool result     | done
                   +-----------------+
```

Cap steps, wall clock, and tokens. The model decides *which* tool each turn, not *how many*.

### Planner and executor

```text
goal -> plan (list of steps) -> executor runs steps -> final answer
```

Gains: you can show the plan for approval, and you can cache and reuse a plan shape. Costs: an
extra round trip, and plans go stale mid-execution. Include a re-plan path and a maximum number
of re-plans.

### Recursive / tree

Decompose into subgoals and spawn child runs. Powerful for research-style tasks, hard to bound.
Give each subtree its own step and spend budget, and require a terminal answer at every level.

## Memory tiers in practice

| Tier | Typical size | Persistence | Compaction |
|---|---|---|---|
| Working (message list) | Bounded by context window | Per request | Summarize older turns |
| Scratch (session state) | A few KB to a few MB | Session, in your store | Delete at session end |
| Long term (user facts) | Small, curated | Durable, owned by you | User-editable, deletable |
| Semantic (corpus) | Large | Versioned index | Re-index on change |

Rules learned the hard way:

- Every turn re-sends the entire working set. That is your per-turn cost and latency.
- Summarize rather than truncate. Dropping the middle of a conversation loses the thread and
  produces confident, wrong follow-ups.
- Tool results are the biggest growth source. Keep them small, or summarize tool results once the
  model has consumed them.
- Write long-term memory only on explicit signal: the user said it, or a deterministic rule
  produced it.
- Give users a way to see and delete what you stored about them. Not optional in most regulated
  settings.

## Guardrail placement

```
user input
   |
   v
[ input validation ]      length, schema, content policy
   |
   v
[ session authz ]         who may do what, resolved before the model sees tools
   |
   v
[ model ]                 decides action
   |
   v
[ tool arg validation ]   JSON Schema + business rules
   |
   v
[ authorization ]         re-checked at execution time, never cached from the model's plan
   |
   v
[ sandbox ]               network, filesystem, process limits
   |
   v
[ side effect ]           log everything
```

Authorization is checked at execution time, every time. The model proposing an action is not
authorization for it.

## Sandbox checklist

- Outbound network: allowlist by destination, deny by default.
- Filesystem: one workspace directory, no access to credentials or keys.
- Process: no arbitrary subprocess execution from a tool unless explicitly designed for.
- Secrets: never in the prompt, never in tool output, never in logs.
- Time: per-call deadline and a wall-clock cap on the whole run.
- Size: cap tool output size before it enters the context.

## Loop detection

```python
def repeated_call(calls: list[tuple[str, str]], window: int = 3) -> tuple[str, str] | None:
    """Return the (tool, args) that has now repeated `window` times in a row."""
    if len(calls) < window:
        return None
    tail = calls[-window:]
    if len(set(tail)) == 1:
        return tail[0]
    return None
```

Also detect:

- The same tool called with slightly different arguments many times, which is usually a model
  trying variants of a failing call.
- Alternating between two calls forever.
- A tool whose output the model ignores, calling it again.

All of these mean the same thing: stop, and return a partial result with an explanation. Do not
let the loop run to the step cap every time; that wastes the full budget on every failure.

## Spend control

Per session, enforce at least:

| Limit | Why |
|---|---|
| Max steps | Bounds the loop |
| Max wall clock | Bounds hung tools and slow models |
| Max tokens or cost | Bounds the bill regardless of step count |
| Max tool calls of one type | Stops retry storms |

Alert when a session hits any limit. A session that always hits the cap is a prompt or tool
design bug, and the alert is how you find out.

## Observability

Log per run: goal, plan, every tool call with arguments and duration, every tool result with
size, model usage, stop reason, final answer, and the reason the run ended (done, step limit,
error, cancellation). Without the reason-the-run-ended field you cannot tell a success from a
truncation.