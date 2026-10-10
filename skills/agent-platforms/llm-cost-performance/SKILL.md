---
name: llm-cost-performance
description: Covers engineering LLM cost and latency, including token accounting, prompt caching, model tiering, batching, streaming, context reduction, routing, and measuring spend in production. Use when the user mentions LLM cost, token usage, prompt caching, rate limits, time to first token, latency budgets, choosing between model tiers, or a bill that grew after a launch. No prices are stated here; see the official pricing pages.
metadata:
  opencode/autoinvoke: true
---

# LLM Cost and Performance Engineering

Cost is a function of tokens and latency is a function of time-to-first-token plus
output tokens. Almost every lever below is a lever on one of those two quantities.
Optimise the shape of the request before you optimise the model.

Verify before relying on this: pricing, context limits, batch discounts, and cache
semantics change often, and differ per provider and per region. Never hard-code a price
into a design document or a budget alert. Read the provider's pricing page and the
caching documentation, and measure your own tokens per task.

## When to use this

- A bill or quota alert jumped and nobody can explain which request caused it.
- Latency is unacceptable and the obvious fix looks like a faster model.
- Someone proposes prompt caching and nobody is sure whether the prefix is stable.
- A team is paying for a frontier model on classification work.
- A user asks for a latency budget, a cost per task, or a capacity plan.

## Quick start

Instrument before optimising. Without per-request accounting, every optimisation is a
guess.

```typescript
import { randomUUID } from "node:crypto";

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;
}

export interface RequestRecord {
  requestId: string;
  route: string;
  model: string;
  usage: TokenUsage;
  ttftMs: number | null;
  totalMs: number;
  cached: boolean;
  outcome: "ok" | "error" | "aborted";
}

/** Read counts from the provider's own usage fields. Field names differ per provider;
 * map them once here rather than at every call site. */
export function readUsage(raw: {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
}): TokenUsage {
  return {
    inputTokens: raw.input_tokens ?? 0,
    outputTokens: raw.output_tokens ?? 0,
    cacheReadTokens: raw.cache_read_input_tokens ?? 0,
    cacheWriteTokens: raw.cache_creation_input_tokens ?? 0,
    reasoningTokens: 0,
  };
}

export class RequestLog {
  private records: RequestRecord[] = [];

  record(entry: RequestRecord): void {
    this.records.push(entry);
    emit(entry);
  }

  /** Tokens that would have been sent if nothing had been cached. */
  uncachedInput(record: RequestRecord): number {
    return record.usage.inputTokens + record.usage.cacheWriteTokens;
  }

  cacheHitRate(): number {
    const total = this.records.reduce(
      (sum, r) => sum + this.uncachedInput(r),
      0,
    );
    if (total === 0) return 0;
    const hit = this.records.reduce((sum, r) => sum + r.usage.cacheReadTokens, 0);
    return hit / total;
  }

  costPerTask(taskIdField: (r: RequestRecord) => string): Map<string, number> {
    const byTask = new Map<string, number>();
    for (const record of this.records) {
      const key = taskIdField(record);
      const tokens =
        this.uncachedInput(record) + record.usage.outputTokens + record.usage.reasoningTokens;
      byTask.set(key, (byTask.get(key) ?? 0) + tokens);
    }
    return byTask;
  }
}

export function newRequestId(): string {
  return randomUUID();
}
```

The field names above are illustrative. Map your provider's actual usage object once,
in one place, and never read provider-specific fields elsewhere in the codebase.

## Token accounting

Understand the four quantities providers report, because they price differently:

| Quantity | What it is | Cost behaviour |
| --- | --- | --- |
| Input tokens | Tokens actually processed this request | Full input rate |
| Cached read tokens | Prefix served from the provider's prompt cache | Discounted, often substantially |
| Cache write tokens | Tokens written into the cache this request | Usually a premium over the input rate |
| Output tokens | Tokens generated, including reasoning tokens on reasoning models | Output rate, which is typically higher than input rate |

Two accounting consequences people miss:

- A cache write costs more than a plain input. Caching a prompt nobody reads again is a
  net loss. Measure hit rate before assuming caching pays.
- Reasoning tokens are billed as output on reasoning models. Lowering reasoning effort
  is an output-token lever, not just a latency lever.

Pricing is per provider, per model, and per region, and providers publish it themselves.
Fetch it at runtime for a cost dashboard if you need one; never transcribe it into
code. If you must hard-code anything, store it as configuration with the source URL and
a date, and have a job that checks the URL.

## Prompt caching

Caching works on prefixes. The provider stores the token prefix of a request and reuses
it when a later request starts with the same tokens.

That single sentence implies every rule:

1. **The stable content must come first.** System instructions, tool definitions, and
   large static documents go at the top. Anything that varies per request goes at the
   bottom.
2. **One byte of change in the prefix invalidates everything after it.** A timestamp
   injected into the system prompt, a shuffled tool list, or a request id in the header
   text destroys the cache.
3. **There is a minimum cacheable prefix.** Short prompts will not hit. Check the
   provider's minimum cacheable prefix length before designing around caching.
4. **Cache lifetime is bounded.** Expect a TTL, and expect the first request after
   expiry to pay the write premium again.

```typescript
// Stable prefix. Nothing here may vary per request.
const SYSTEM = [
  "You are a support triage assistant for Acme Cloud.",
  "Answer only from the retrieved documentation below.",
  "If the documentation does not cover the question, say so and escalate.",
].join("\n");

function buildPrompt(question: string, docs: string[]): string {
  // Variable content strictly after the stable prefix.
  const context = docs.map((d, i) => `[${i + 1}] ${d}`).join("\n\n");
  return `${SYSTEM}\n\n<documents>\n${context}\n</documents>\n\n<question>\n${question}\n</question>`;
}
```

Common cache destroyers to grep for in an existing codebase:

- `new Date().toISOString()` interpolated into the system prompt.
- A conversation id, user id, or session id placed above the static content.
- Tool or function definitions serialised from a map whose iteration order changes.
- Dynamic few-shot examples placed above the instructions instead of below.

Fix by moving the varying content to the end and, where a varying header is genuinely
needed, by keeping the varying part small and after everything reusable.

## Model tiering

Route by difficulty, not by habit. A single "smartest model everywhere" default is the
most common avoidable spend.

| Tier | Use for | Signals |
| --- | --- | --- |
| Small, fast | Classification, routing, extraction, summarisation of short inputs, tool-argument repair, "does this need escalation" | Short output, fixed schema, easy to grade |
| Mid | Code edits with tests, summarisation of long documents, multi-step tool use with a bounded plan | Needs real reasoning but not deep planning |
| Frontier | Architecture decisions, novel debugging, high-stakes judgement, tasks with no partial credit | Previous tiers failed, or the task is genuinely open-ended |

Two tiering designs that pay off:

- **Cascade.** Run the cheap tier; if a cheap verifier says the output is insufficient,
  re-run with the frontier tier. You pay frontier prices only on the hard slice.
- **Escalate on failure.** Run the cheap tier; if the tool chain errors, the schema
  fails validation, or a test fails, escalate. Free signal, no extra model call.

Routing code is ordinary code. Keep the decision observable so you can audit how often
each tier fires.

```typescript
type Tier = "fast" | "mid" | "frontier";

export interface RouteRequest {
  tierHint?: Tier;
  promptTokens: number;
  needsToolUse: boolean;
  taskKind: "extract" | "classify" | "codegen" | "plan" | "judge";
}

export function route(req: RouteRequest): Tier {
  if (req.tierHint) return req.tierHint;
  if (req.taskKind === "extract" || req.taskKind === "classify") return "fast";
  if (req.promptTokens > 120_000) return "frontier";
  if (req.taskKind === "plan" || req.taskKind === "judge") return "frontier";
  return req.needsToolUse ? "mid" : "fast";
}
```

The numeric threshold above is a placeholder. Derive yours from your own measurement of
where the cheap tier starts failing.

## Batching and streaming

Two opposite latency strategies, for two different contracts.

**Batching** amortises fixed per-request cost. Most providers offer an asynchronous batch
API where a queue of requests is submitted together and results arrive later, at a
discount relative to synchronous calls. Use it for anything not user-facing: evaluation
runs, classification sweeps, embedding generation, overnight enrichment. Confirm the
current turnaround and discount with the provider; the trade is latency for cost.

**Streaming** improves perceived latency without reducing total time. Time to first
token is what users perceive. Stream whenever a user is waiting, and be explicit in the
interface that output is partial, because a partially generated answer changes what
"stop" means.

What streaming does not fix:

- Time to first token is dominated by prefill. A 100k-token prompt will have a slow TTFT
  no matter how fast generation is.
- Streaming does not reduce tokens. If your problem is cost, it is the wrong lever.
- Streaming complicates tool calls. A model that streams a tool call emits partial JSON;
  parse only after the stream completes, and design the UI not to render half a JSON
  blob.

Decision table:

| Situation | Choice |
| --- | --- |
| Interactive text | Stream |
| Interactive, very long prompt | Stream and reduce the prompt; TTFT will dominate |
| Nightly batch classification | Batch API |
| Evaluation sweeps | Batch API |
| Latency-sensitive single tool call | Synchronous, smallest tier that works |

## Reduce context

The most reliable saving is sending fewer tokens. Ordered by typical payoff:

1. Delete what the model does not need for this request. Most prompts accumulate context
   that only mattered for one earlier step.
2. Retrieve less. Fewer, better chunks beat more chunks. Cap the number of retrieved
   documents and cap each one.
3. Summarise the history, keep the last few turns verbatim.
4. Put long reference material behind a tool the model calls on demand, rather than
   inlining it every turn.
5. Cap output length explicitly with a token or finish-reason constraint when the task
   does not need an essay.

Every one of these trades something. Retrieval reduction can lose the answer. Summarised
history can lose a detail. Make the trade deliberately and log it.

## Measuring in production

Instrument four things per request and keep them joinable by a task id:

| Signal | Why |
| --- | --- |
| Token usage by category | The only way to attribute spend to a feature |
| Time to first token | Perceived latency |
| Total duration | Throughput and capacity |
| Cache read and write tokens | Whether the prefix is actually stable |
| Model and tier | Attribution of quality regressions |
| Outcome | Cost per successful task, not cost per call |

Then define the unit economics on the unit that matters: cost per resolved ticket, cost
per merged pull request, cost per completed agent task. Cost per request is a
meaningless number that gets optimised in the wrong direction.

Guardrails that work:

- A per-tenant budget with a soft alert and a hard stop at a multiple of the median.
- A max output token cap on every call site, asserted in a shared client wrapper.
- A guard on total prompt tokens per request, so a single pathological document cannot
  run up an unbounded bill.
- A CI job that fails when a new call site bypasses the instrumented client.

## Checklist

- Every call site goes through one instrumented client wrapper.
- Per-request token usage, TTFT, and duration are recorded and joinable by task id.
- The stable prompt prefix is above all varying content, byte for byte.
- No timestamp, request id, or session id sits above reusable content.
- Cache hit rate is measured, and cache writes are counted as a cost.
- Model choice is per task kind, not global.
- Batching is used for non-interactive work.
- Streaming is used for user-facing generation and never for tool-argument parsing.
- Output length is capped at the call site.
- The cost unit is cost per successful task.
- Prices come from the provider's pricing page at read time, not from a constant.

## Common mistakes

- **Cache never hits.** Symptom: cache write tokens are high, read tokens near zero.
  Cause: something varies in the prefix. Look for a date, an id, or a reordered list
  near the top of the prompt. Fix: move varying content to the end.
- **Caching made things more expensive.** Cause: the prompt is short, the cache has a
  minimum prefix length, or hit rate is near zero so you pay write premiums for nothing.
  Fix: measure hit rate before keeping the caching path.
- **Cost per request fell but cost per resolved task rose.** Cause: the cheap tier
  silently lowers completion rate, so you need more requests. Fix: measure both.
- **Latency got worse after adding context.** Cause: prefill. Fix: reduce tokens, or
  move long material behind an on-demand tool.
- **Streaming shows a tool call as broken JSON.** Cause: the consumer renders partial
  frames. Fix: buffer tool-call arguments and render them only after completion.
- **Spend spikes in one tenant.** Cause: no per-tenant attribution. Fix: tag every
  request with a subject and budget per subject.
- **A "cheap" model change silently dropped quality.** Cause: no eval gate on the routing
  change. Fix: run the eval suite before and after any tier change.

## Anti-patterns

- Quoting a price per million tokens from memory in a design document.
- Caching "just in case" without measuring hit rate.
- Truncating the tool list or the system prompt to save tokens, and quietly degrading
  reliability to save money.
- Putting the user question above the static instructions.
- Raising `max_tokens` everywhere "to be safe".
- Optimising cost per request instead of cost per successful task.
- Adding a stronger model to fix a latency problem that is really a prefill problem.
- Instrumenting cost only in the client that was easy to instrument.

## References

- Prompt caching, per provider; see the provider's own caching documentation
- Anthropic pricing: https://docs.anthropic.com/en/docs/about-claude/pricing
- Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI API pricing: https://openai.com/api/pricing/
- OpenAI prompt caching: https://platform.openai.com/docs/guides/prompt-caching
- Google Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- OWASP LLM10 Unbounded Consumption: https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/