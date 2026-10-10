---
name: agent-evaluation
description: Covers evaluating LLM features and AI agents, including building representative test sets, task-based versus model-graded evals, offline eval harnesses, regression testing prompts and agents, A/B testing in production, metrics such as success rate and tool-call correctness, judging with rubrics and pairwise comparison, and avoiding eval contamination. Use when the user mentions evaluation, evals, test sets, graders, regressions, or measuring whether a prompt or model change helped.
metadata:
  opencode/autoinvoke: true
---

# Agent and LLM Evaluation

An eval is a fixed set of inputs, a way to score each run, and a comparison to a
baseline. Without all three you have anecdotes. The hard part is almost never the
harness; it is building a test set that resembles production and a grader that does not
drift.

## When to use this

- A prompt, model, or tool description is about to change.
- A team cannot say whether last week's change helped.
- An agent works in demos and fails in real use, or the reverse.
- Someone wants a single "quality score" for an LLM feature.
- A regression reached production and nobody detected it before release.

## Quick start

A harness that scores an agent task, records the transcript, and diffs against a
baseline.

```typescript
import { readFile } from "node:fs/promises";

export interface EvalCase {
  id: string;
  input: string;
  /** Optional fixture the agent is allowed to use. */
  fixtures?: Record<string, string>;
  /** Machine-checkable assertions. */
  expect: {
    mustCallTools?: string[];
    mustNotCallTools?: string[];
    outputMatches?: RegExp[];
    outputForbidden?: RegExp[];
    /** Task considered complete. */
    outcome: "success" | "partial" | "failure";
  };
  /** Free-text grading rubric used only when machine assertions cannot decide. */
  rubric?: string;
  tags: string[];
}

export interface RunResult {
  caseId: string;
  outcome: "success" | "partial" | "failure";
  toolCalls: string[];
  output: string;
  tokensIn: number;
  tokensOut: number;
  durationMs: number;
  error?: string;
  transcript: string;
}

export interface Report {
  results: RunResult[];
  successRate: number;
  toolCallAccuracy: number;
  meanTokens: number;
  byTag: Record<string, { total: number; success: number }>;
}

export function score(results: RunResult[]): Report {
  const byTag: Report["byTag"] = {};
  let toolCorrect = 0;
  let toolTotal = 0;

  for (const r of results) {
    for (const tag of tagsFor(r.caseId)) {
      byTag[tag] ??= { total: 0, success: 0 };
      byTag[tag].total += 1;
      if (r.outcome === "success") byTag[tag].success += 1;
    }

    const wanted = expectedToolCalls(r.caseId);
    if (wanted.length > 0) {
      toolTotal += 1;
      const called = new Set(r.toolCalls);
      const hit = wanted.filter((t) => called.has(t)).length;
      if (hit === wanted.length) toolCorrect += 1;
    }
  }

  const successes = results.filter((r) => r.outcome === "success").length;
  return {
    results,
    successRate: results.length === 0 ? 0 : successes / results.length,
    toolCallAccuracy: toolTotal === 0 ? 1 : toolCorrect / toolTotal,
    meanTokens:
      results.reduce((s, r) => s + r.tokensIn + r.tokensOut, 0) /
      Math.max(results.length, 1),
    byTag,
  };
}

export async function loadCases(path: string): Promise<EvalCase[]> {
  return JSON.parse(await readFile(path, "utf8")) as EvalCase[];
}
```

`tagsFor` and `expectedToolCalls` read the loaded case set. Keep the case file as the
single source of truth so a score can always be traced to specific inputs.

## Two kinds of eval

| Kind | What it measures | Cost | Use it for |
| --- | --- | --- | --- |
| Task-based | Did the agent do the thing | Low | Regression gates in CI |
| Model-graded | Is the thing good | Medium to high | Judgement that has no assertion |

Prefer task-based whenever an assertion exists. Reserve model-graded for genuinely
subjective output, and always pair it with a machine-checkable gate.

**Determinism note.** LLM outputs vary between runs. Never gate CI on an exact string
match against a reference answer. Assert structure, tool calls, constraints, or use a
grader with a tolerance. Pin temperature where the provider allows it, and run a baseline
in the same session so the comparison is like for like.

## Building the test set

This is the highest-leverage work and the part most teams rush.

Sources, in order of value:

1. **Real production traces, redacted.** Best possible distribution. Sample across the
   long tail, not just the frequent cases.
2. **Support tickets and issue reports.** Written by people, not by you.
3. **Failure reports.** Every bug the agent caused becomes a case. This is the cheapest
   and most targeted source you have.
4. **Hand-written adversarial cases.** Breaks a specific assumption.
5. **Synthetic expansions.** Cheap, but they inherit the blind spots of the prompt that
   generated them. Label them.

Size. A set of 30 to 100 well-chosen cases catches most regressions and stays runnable in
a pull request. A set of 5000 synthetic cases catches fewer real problems because it is
skewed. Quality per case beats volume.

Every case needs:

- A realistic input, ideally one that actually occurred.
- An expectation that is checkable by a program, not by a feeling.
- At least one tag so you can slice. Slice by task type, by difficulty, by language, by
  whether tools were needed.
- Cases for the paths you do not want. Empty input, malformed input, a request the agent
  should refuse, an input that tries to instruct it to do something else.

**Hold out.** Split cases into a development set and a test set you only run at release
time. If you iterate prompts against the same set you report, you have fitted to the
set.

## Grading

### Machine assertions

Prefer these. They are fast, free, deterministic, and their failures are easy to debug.

| Assertion | Checks |
| --- | --- |
| `mustCallTools` | The agent used the right tools |
| `mustNotCallTools` | The agent did not take an action it should not |
| `outputMatches` | Format, required sections, identifiers |
| `outputForbidden` | No leaked secrets, no forbidden strings |
| Side-effect checks | The database row exists, the file was written, the API was called with the right arguments |
| Schema validation | The output parses into the expected schema |

Tool-call correctness is the highest-signal metric for agents. A wrong answer usually
means a wrong sequence of actions, and the sequence is far easier to grade than prose.

### Model-graded

When there is no assertion, grade with a rubric. Keep the rubric short and make the
grades discrete.

```markdown
Grade the answer on three criteria, 0 to 2 each.

Correctness: 2 all claims supported, 1 minor error, 0 a wrong claim.
Completeness: 2 answers the question fully, 1 partial, 0 missing the answer.
Grounding: 2 every claim traces to the provided context, 1 minor gap,
0 fabricates or cites something absent.

Return JSON: {"correctness": int, "completeness": int, "grounding": int, "reason": str}
```

Rules for model-graded evals:

- Separate the grader prompt from the system prompt under test.
- Give the grader the reference answer when one exists.
- Pin the grader model and record its id with every result. Graders drift when you
  silently upgrade them.
- Calibrate the grader: grade 20 to 50 cases by hand, compute agreement, and report it.
  A grader that disagrees with humans half the time tells you about the humans or the
  grader, not the system.
- Randomise position when using pairwise comparison, since judges favour the first
  answer.

### Pairwise comparison

For subjective output, comparing two answers is more reliable than scoring either one
alone. Present the two answers in a randomised order and ask which is better, with
"both are equally good" and "both are equally bad" as options to prevent forced choices.

Use it for release decisions ("is the new prompt better than the old one?"). Do not use
it for continuous monitoring; it costs more per signal and gives no absolute anchor.

## Metrics

| Metric | Definition | Watch for |
| --- | --- | --- |
| Success rate | Fraction of cases reaching the defined outcome | The only gate that matters |
| Tool-call correctness | Fraction of cases with the expected tool sequence | Diagnoses planning failures |
| Hallucination rate | Claims not supported by provided context | Silent quality loss |
| Cost per successful task | Total tokens over successful tasks | The efficiency metric |
| Refusal correctness | Cases where the agent should refuse and did, and vice versa | Over-refusal is a real failure |
| Partial-credit score | Fraction of assertions passed per case | Sensitivity for gradual change |

Report per tag, not only the aggregate. An aggregate that holds steady while one tag
collapses is a shipping decision waiting to go wrong.

## Running the harness in CI

```yaml
# .github/workflows/eval.yml
name: eval
on:
  pull_request:

jobs:
  offline-eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - name: Run offline eval against the baseline
        env:
          EVAL_MODEL: ${{ vars.EVAL_MODEL }}
          EVAL_BASELINE_REF: origin/main
        run: |
          npm run eval -- \
            --cases evals/cases.json \
            --out artifacts/eval.json \
            --compare-ref "$EVAL_BASELINE_REF"
      - uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: artifacts/
```

Gate on a threshold, not on strict improvement, because variance is real:

```text
fail if success_rate < baseline.success_rate - 0.02
fail if tool_call_accuracy < baseline.tool_call_accuracy
fail if hallucination_rate > baseline.hallucination_rate + 0.01
```

Store the artifacts. When someone asks "did we make this better" eight weeks later, the
answer is in the stored reports, not in a memory.

## A/B testing in production

Offline evals test what you thought of. Production tests what happened.

| Situation | Method |
| --- | --- |
| Model or prompt change | Randomised A/B with a success metric |
| Traffic and latency change | A/B on latency, error rate, and cost alongside quality |
| Cost change | A/B with cost per successful task as the primary metric |
| High-risk change | Shadow mode first, then a small percentage rollout |

Requirements before running an A/B:

- A success signal that exists in production, not only in the eval set. If your eval
  uses a judge and production has no judge, the experiment has no endpoint.
- Sample size computed from the minimum effect you care about. Do not stop on the first
  day because the number moved.
- Assignment sticky per user or per task, so one user does not see both variants and
  compare them in their head.
- Guardrail metrics watched alongside the primary metric, so a quality gain that comes
  with doubled latency or tripled cost does not ship.

Shadow mode deserves special mention for agents: run the new version without letting it
act, log what it would have done, and diff against what actually happened. It catches
most regressions with zero user risk. It does not catch cases where the log is
misleading about side effects.

## Contamination

The eval set leaks into the system in four ways:

- **Training leakage.** If cases came from public data, the model may have seen them.
  Use private, recently generated, or internal cases for anything sensitive.
- **Prompt overfitting.** Iterating prompts until they pass a fixed set means the set is
  now a training set. Hold out.
- **Grader leakage.** If the judge prompt mentions the expected answer, the judge is
  grading for the reference rather than for quality.
- **Selection bias.** Only sampling the easy paths. Tag by difficulty and report by tag.

Guard against it: hold out a test set, rotate in fresh real cases monthly, and never
show a grader the reference unless the rubric says to.

## Human review loops

Automation does not replace review; it decides what gets reviewed.

- Route low-scoring and disagreeing cases to a human queue with the full transcript.
- Sample a small fixed percentage of high-scoring cases for review, so silent failures
  do not hide behind a good aggregate.
- Record every human verdict with the case id, the rubric used, and the reviewer.
  Over time this becomes the calibration set for your grader.
- Review the rubric itself. Rubrics drift; people disagree with them; the disagreement
  is the signal.

## Checklist

- A committed case set with realistic inputs, tags, and machine-checkable expectations.
- Development and held-out test sets are separate.
- The grader model and its id are pinned and recorded with every result.
- Grader agreement with humans is measured and reported.
- Metrics are sliced by tag, not only aggregated.
- CI gates on thresholds relative to a baseline and stores artifacts.
- Production experiments have a real success signal, sticky assignment, and guardrails.
- Contamination controls exist: holdout, fresh real cases, no reference leakage to the
  judge.
- A human review queue receives low scores and a fixed sample of high scores.
- Failure reports from production flow back into the case set as new cases.

## Common mistakes

- **Exact-match grading.** Symptom: the eval fails on a correct answer. Cause: outputs
  are non-deterministic. Fix: grade structure, tool calls, and constraints, or use a
  rubric with tolerance.
- **Eval improved, users did not.** Cause: the case set is unrepresentative or
  overfitted. Fix: add real production cases, hold out a test set.
- **The aggregate is fine but one flow collapsed.** Cause: only the aggregate is
  reported. Fix: report per tag and alert on per-tag movement.
- **Grader and system share a prompt.** Cause: copy-paste. Fix: separate them and pin the
  grader.
- **Judge flatters the first answer.** Cause: position bias in pairwise comparison.
  Fix: randomise and allow a tie option.
- **Everyone stopped "refining" the eval and started refining the numbers.** Fix: hold
  out, rotate in fresh cases, and audit who changed what and when.
- **A/B test with no success signal in production.** Fix: ship a judge-based signal to
  production, or accept that the experiment can only measure cost and latency.
- **Suite takes 40 minutes, so it only runs nightly.** Fix: shard the set and run a
  30-case smoke set per pull request.

## Anti-patterns

- Reporting a single number with no denominator and no breakdown.
- Generating the eval set with the same model version you are evaluating.
- Optimising the rubric until the score matches your opinion of the product.
- Using an LLM judge with no human calibration.
- Treating a green eval run as sufficient evidence for a change that touches
  permissions, money, or destructive actions.
- Deleting failing cases instead of fixing the system.

## References

- Anthropic evaluation guidance: https://docs.anthropic.com/en/docs/test-and-evaluate
- OpenAI evals guidance: https://platform.openai.com/docs/guides/evals
- MCP specification, for evaluating tool servers: https://modelcontextprotocol.io/specification
- OWASP LLM Top 10, for security checks to include in an eval set: https://genai.owasp.org/llm-top-10/