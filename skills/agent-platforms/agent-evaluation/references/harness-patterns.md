# Offline harness patterns

Runnable structure for evaluating an agent that calls tools. Adapt to your stack; the
shape is what matters.

## Case file format

```json
{
  "cases": [
    {
      "id": "refund-outside-window-001",
      "input": "I bought a subscription on 3 March and want my money back today.",
      "fixtures": {
        "customer.json": "{\"tier\":\"free\",\"createdAt\":\"2026-03-03T09:00:00Z\"}"
      },
      "expect": {
        "outcome": "success",
        "mustCallTools": ["lookup_customer", "get_refund_policy"],
        "mustNotCallTools": ["issue_refund"],
        "outputMatches": ["(?i)outside.{0,40}(window|policy)"],
        "outputForbidden": ["(?i)refund(ed)?\\s+\\$[0-9]"],
        "rubric": "Explains the refund window without offering a refund. Does not blame the customer."
      },
      "tags": ["billing", "refusal", "difficulty:medium"]
    }
  ]
}
```

Design notes:

- `mustNotCallTools` is where most real value lives. An agent that does the wrong thing
  is worse than one that answers badly, and this is cheap to assert.
- `outputForbidden` catches leaked secrets and claimed side effects that did not happen.
- The rubric is used only when the assertions cannot decide the outcome.
- Tags drive slicing. Keep them hierarchical with `namespace:value` so filtering is
  predictable.

## Running a case

```typescript
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { EvalCase, RunResult } from "./types.js";

/** Minimal HTTP mock so cases do not hit a real upstream. */
export function startFixtureServer(
  routes: Record<string, { status: number; body: unknown }>,
): Promise<{ url: string; close: () => Promise<void>; calls: string[] }> {
  const calls: string[] = [];
  const server = createServer((req, res) => {
    const key = `${req.method} ${req.url ?? "/"}`;
    calls.push(key);
    const route = routes[key];
    if (!route) {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "no fixture" }));
      return;
    }
    res.writeHead(route.status, { "content-type": "application/json" });
    res.end(JSON.stringify(route.body));
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      resolve({
        url: `http://127.0.0.1:${port}`,
        close: () => new Promise((done) => server.close(() => done())),
        calls,
      });
    });
  });
}

export async function runCase(
  agent: { run: (input: string, ctx: { baseUrl: string }) => Promise<{
    output: string;
    toolCalls: string[];
    tokensIn: number;
    tokensOut: number;
  }> },
  testCase: EvalCase,
  baseUrl: string,
): Promise<RunResult> {
  const started = performance.now();
  try {
    const result = await agent.run(testCase.input, { baseUrl });
    return {
      caseId: testCase.id,
      outcome: judge(testCase, result.output, result.toolCalls),
      toolCalls: result.toolCalls,
      output: result.output,
      tokensIn: result.tokensIn,
      tokensOut: result.tokensOut,
      durationMs: performance.now() - started,
      transcript: JSON.stringify(result, null, 2),
    };
  } catch (error) {
    return {
      caseId: testCase.id,
      outcome: "failure",
      toolCalls: [],
      output: "",
      tokensIn: 0,
      tokensOut: 0,
      durationMs: performance.now() - started,
      error: error instanceof Error ? error.message : String(error),
      transcript: "",
    };
  }
}
```

Machine judgement, applied in order:

```typescript
function judge(testCase: EvalCase, output: string, toolCalls: string[]): RunResult["outcome"] {
  const expected = testCase.expect;
  const called = new Set(toolCalls);

  const wrongTool = (expected.mustCallTools ?? []).some((t) => !called.has(t));
  const forbiddenTool = (expected.mustNotCallTools ?? []).some((t) => called.has(t));
  if (forbiddenTool || wrongTool) return "failure";

  const missingPattern = (expected.outputMatches ?? []).some((r) => !r.test(output));
  const leakedPattern = (expected.outputForbidden ?? []).some((r) => r.test(output));
  if (leakedPattern) return "failure";
  if (missingPattern) return "partial";

  return expected.outcome === "success" ? "success" : "partial";
}
```

Escalate to the model grader only when `outcome` is `partial` and the case has a rubric.
That keeps grader cost bounded to the genuinely ambiguous slice.

## Pairwise comparison, randomised

```typescript
export function randomise<A, B>(a: A, b: B): { first: A | B; second: A | B; flipped: boolean } {
  const flipped = Math.random() < 0.5;
  return flipped
    ? { first: b, second: a, flipped: true }
    : { first: a, second: b, flipped: false };
}

export const PAIRWISE_PROMPT = `You are comparing two answers to the same task.

<task>{{TASK}}</task>

<answer_a>{{A}}</answer_a>
<answer_b>{{B}}</answer_b>

Pick exactly one option:
A - answer_a is clearly better
B - answer_b is clearly better
TIE - they are equally good
BOTH_BAD - neither is acceptable

Return JSON: {"choice": "A" | "B" | "TIE" | "BOTH_BAD", "reason": "one sentence"}`;
```

When aggregating, correct for the flip. If you sent both orderings, count a win in either
position as a win for the same underlying answer, and treat a position-sensitive result
as evidence the judge is biased rather than as a difference in quality.

## Calibration

```typescript
export interface Verdict { caseId: string; grader: number; human: number }

export function agreement(verdicts: Verdict[]): number {
  if (verdicts.length === 0) return 0;
  const withinOne = verdicts.filter((v) => Math.abs(v.grader - v.human) <= 1).length;
  const exact = verdicts.filter((v) => v.grader === v.human).length;
  return {
    exact: exact / verdicts.length,
    withinOne: withinOne / verdicts.length,
  };
}
```

Report exact agreement and within-one agreement. A grader with poor exact agreement but
high within-one agreement is usable for ranking, not for pass and fail. Say which one you
rely on.

## Storing and diffing reports

```text
artifacts/
  <timestamp>-<git-sha>-<run-id>.json
```

Each report holds the git sha, the case file hash, the model id, the judge model id, the
prompt hash, and the per-case results. Without those fields you cannot tell later whether
a movement came from a prompt change, a case change, or a judge change.

Diff two reports with:

```bash
node scripts/diff-evals.js artifacts/baseline.json artifacts/candidate.json
```

The useful output is per-case status changes, not just aggregate deltas:

```text
case                                baseline    candidate   change
refund-outside-window-001           success     success     -
escalate-on-second-contact-014      partial     success     improved
tool-loop-on-empty-result-007        success     failure     REGRESSION
```

Any regression deserves a written explanation before merge.