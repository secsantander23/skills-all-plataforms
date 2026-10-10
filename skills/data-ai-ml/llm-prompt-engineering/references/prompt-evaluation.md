# Evaluating Prompts

## The loop

```
1. Collect real inputs        -> eval set (inputs + what good looks like)
2. Write the metric           -> how you score a run
3. Snapshot the config        -> prompt version, model, params, seed
4. Run the whole set          -> not just the case you are fixing
5. Diff                       -> what improved, what regressed
6. Ship behind a flag         -> keep the old prompt as a fallback
```

Skip step 1 and you are not engineering, you are guessing.

## Building the eval set

- 30 to 200 cases is enough to be useful. Coverage beats volume.
- Include at least 30 percent cases the current prompt fails. That is where the value is.
- Include the awkward ones: empty input, very long input, wrong language, near-duplicate
  questions, hostile input, and inputs that should be refused.
- Store the expected behavior, not the expected string. Exact-match tests on prose break the
  first time a model updates.
- Never let the same cases drive both tuning and the final report. Hold out a split.

## Metrics

| Task shape | Metric | Notes |
|---|---|---|
| Classification | Exact match, macro F1 | Report per-class, not just accuracy |
| Extraction | Field-level F1, schema validity rate | Report parse failures separately |
| Summarization | LLM-as-judge rubric with a fixed scale | Pin the judge model and prompt |
| Refusal behavior | Refusal rate on should-refuse set, compliance rate on should-answer set | Both matter |
| Format compliance | Schema validity rate | Must be near 1.0 or you have a schema bug |
| End-to-end | Task success judged in the product | The only metric that matters long term |

For LLM-as-judge, write the rubric as explicit criteria, pin the judge model and temperature,
and score a small human-labeled subset so you know your judge is worth anything.

## Multiple trials

Sampling variance means one run is noise. Run each case several times and report both mean and
the worst observed, so you see flakiness rather than an average that hides it.

## Regression protection

```python
# tests/test_prompt_regression.py
import json
from pathlib import Path

import pytest

EVAL_CASES = json.loads(Path("evals/support_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", EVAL_CASES, ids=lambda c: c["name"])
def test_triage_prompt(case):
    result = triage(case["input"])  # your production prompt function
    assert result["category"] == case["expected_category"]
```

Run this in CI on every prompt or model change. Pin the model to an explicit id in the test
environment so a model alias move cannot silently flip your suite red or green.

## Logging for later analysis

Store per request:

- prompt template id and rendered prompt hash
- model id, sampling parameters, seed, tool schema version
- token usage and latency
- raw response and stop reason
- validation outcome
- user feedback signal, if you have one

That dataset is what lets you answer "did that prompt change help?" months later instead of
arguing from memory.

## Cost of evaluation

Evaluate on a sample, not on all traffic. Keep the set deterministic and cheap to run, and run
a larger offline sweep before a model or provider migration.