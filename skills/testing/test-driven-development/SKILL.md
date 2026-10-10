---
name: Test-Driven Development
description: Covers the red-green-refactor loop, choosing the design through a failing test, test seams and dependency injection for testability, characterisation tests for legacy code, the discipline of a real red bar, keeping the test suite fast, and pairing TDD with coverage and mutation testing. Use when the user asks about TDD, when writing tests before code, when adding tests to untested legacy code, when code is hard to test, or when a team wants to adopt test-first practices.
metadata:
  opencode/autoinvoke: true
---

# Test-Driven Development

TDD is a design technique disguised as a testing technique. The unit of work is not "write a test" but "answer one question about the design by writing a test". The core mental model is a tight loop between a question and an answer, with the code as the accumulated evidence.

## When to use this

- New business logic with a clear rule and at least one interesting edge case.
- Designing a public API or a module boundary: writing the test first is how you find the awkward interface.
- Fixing a bug: reproduce it with a failing test, then fix.
- Extending code without tests, where you want the extension to be safe.
- Adding tests to legacy code with no coverage, where behaviour must be pinned before it is changed.

## The loop

```
RED     Write the smallest failing test that expresses the next piece of behaviour.
        Watch it fail. Confirm it fails for the expected reason.
GREEN   Write the least code that makes it pass. Ignore design, ignore elegance.
REFACTOR Remove duplication, rename for clarity, improve structure. Tests stay green.
REPEAT  Change the question, not the goal.
```

The loop takes minutes. If a cycle takes days, you are not doing TDD.

### Red

Write a test that names a rule and fails:

```python
def test_empty_cart_has_zero_total():
    assert compute_total([]) == 0
```

Then **run it** and read the failure. A test that fails for the right reason (NameError on the missing function, or an assertion showing the wrong value) proves two things: the test executes, and it can detect the bug you care about. A test that fails at import time proves nothing about the rule.

### Green

Make it pass with the simplest thing that works:

```python
def compute_total(lines):
    return 0
```

Then relax the test to state the next rule, watch it fail again, and implement it. Resist building the general version before a test demands it. Code written for a test that already exists is code whose complexity you have justified.

### Refactor

With a green test as a safety net, you can rename, extract, and restructure. Then run the tests again. If a test had to change to accommodate the refactor, it was coupled to structure, not behaviour.

## Worked example: a full cycle

Start from the rule, not the code.

```python
# RED - the rule: an order with no lines is not submittable.
def test_empty_order_cannot_be_submitted():
    order = Order(lines=[])

    with pytest.raises(EmptyOrder):
        order.submit()
```

Run it: `EmptyOrder` does not exist, so it fails at import. The intent is clear, so write the minimum.

```python
class EmptyOrder(Exception):
    pass


class Order:
    def __init__(self, lines):
        self.lines = lines

    def submit(self):
        raise NotImplementedError
```

Still red. Make it green with the smallest thing:

```python
    def submit(self):
        if not self.lines:
            raise EmptyOrder
```

Green. Refactor while the test stays green, then state the next rule and repeat:

```python
def test_order_total_is_the_sum_of_line_totals():
    order = Order(lines=[Line(unit_price_cents=1000, quantity=2), Line(unit_price_cents=250, quantity=1)])

    order.submit()

    assert order.total_cents == 2250


def test_order_is_stamped_with_submission_time():
    # The clock arrives as data, because a real clock is not testable.
    order = Order(lines=[Line(unit_price_cents=1000, quantity=1)])
    now = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)

    order.submit(now)

    assert order.submitted_at == now
```

Two things happened here that matter more than the individual tests: the total rule fell out of the second cycle almost for free, and the third cycle forced a signature change (`submit(now)`) that a time-free design needed from the start. Both were discovered by writing the test first.

## The red bar

Discipline: **you do not write production code until you have seen the test fail.** This is the whole mechanism. Without a genuine red, there is no evidence that the test has power.

What breaks without the red bar:

- You write the code, then write a test that mirrors it. The test asserts the code you happened to write, not the rule you meant to implement. Bugs that are misunderstandings survive.
- The test passes on the first run, which tells you nothing about whether it would catch the bug.
- You "know" the code is right and skip the test, then forget to add it.

Legitimate exceptions exist, and you should name them rather than pretend:

- Pure refactoring with an existing green safety net.
- Generated code and mechanical bulk edits, where the generator or the diff is the review artifact.
- Exploratory spikes. Write a spike, throw it away, then TDD the real thing with what you learned.

## Designing an API test-first

Writing the test before the function forces the API to answer "how do I call this?" honestly. If the test is awkward to write, the interface is awkward. That is the signal.

```python
# The test drives the interface. Compare with a harder-to-call alternative
# such as discount(amount, is_percent=True, customer_type="vip", ...)
def test_vip_gets_a_larger_discount_than_a_regular_customer():
    order = Order(total_cents=10_000)
    vip = discount_for(order, tier="vip")
    regular = discount_for(order, tier="regular")

    assert vip == 1500
    assert regular == 500
```

Design smells a test-first workflow surfaces early:

- Too many parameters, especially booleans. Split the function or use an options object.
- Positional arguments of the same type in different orders. Make them keyword-only.
- The caller must set fields in a particular order, or the object must be constructed before any method works. That is a constructor problem, not a test problem.
- The test needs a mock to make progress. The dependency has no seam.

## Test seams

A *seam* is a place where you can alter behaviour without editing in that place (Michael Feathers). Every seam is a design tool. It is the thing that makes a module testable in isolation.

Sources of seams:

- **Dependency injection.** Pass the collaborator in. Constructor injection in most languages; a framework's container can do it for you, but keep a plain constructor available for tests.
- **Parameterize what varies.** Take `now`, the RNG seed, the locale, or the network client as a parameter rather than reaching for a global.
- **Wrap the boundary.** Give the module one thin adapter around the hard-to-control dependency (a clock, a file system, an HTTP client), so tests can substitute the adapter.
- **Link seams and object seams.** In C, link seams replace a function at link time; object seams replace a method on an object. In managed languages, DI covers most of this.

```python
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


class RenewalService:
    def __init__(self, clock: Clock):
        self.clock = clock

    def is_due(self, renewed_on: datetime, every_days: int) -> bool:
        return (self.clock.now() - renewed_on).days >= every_days
```

The cost of a seam is an extra layer of indirection. Pay it where the boundary is genuinely hard to control (time, randomness, external systems), and not for a parameter list that a plain argument would cover.

## Characterisation tests for legacy code

When you inherit code with no tests, do not try to TDD a redesign. First, pin down what it currently does, even where the current behavior is wrong. These are *characterisation tests* (Michael Feathers' terminology): they assert what the code does today, not what it should do.

Method:

1. Read the code and list the inputs and outputs you can observe.
2. Write a test for the current behavior, named after the observed behavior, not the desired one.
3. Run it against the original code. If it passes, you have a characterization.
4. Only then change anything, with the characterization as your net.

```python
# Characterization: the current code returns cents, contrary to its name.
def test_format_amount_currently_returns_cents_not_a_string():
    assert format_amount(1500) == 1500


def test_empty_cart_subtotal_currently_raises():
    with pytest.raises(IndexError):
        subtotal([])
```

Name characterization tests so it is obvious they describe existing behavior. Mark them so you can find and revisit them. As you refactor, promote them to proper tests, and delete the ones that describe a bug you have actually fixed.

Do not write characterization tests for code you are about to delete.

See `references/legacy-and-seams.md` for a step-by-step procedure for introducing seams into code you cannot test, and for the mechanical refactorings that create them.

## Adding tests to an untested module

An adoption sequence that avoids a big-bang test push:

1. **Pick the module with the highest defect density or cost.** Read the bug tracker, not the coverage report.
2. **Write one characterization test** for its most important current behavior and run it against the unmodified code.
3. **Extend to the branch points**, one behavior per test, following the "one reason to fail" rule.
4. **Find a seam.** Identify the first thing that makes the next test slow or impossible, and change the design to introduce a seam.
5. **Repeat.** Each step is a small, reviewable change that leaves the suite green.

Coverage grows as a side effect of step 3 and 4. Do not start with step 3 alone: tests that require the real database, the real clock, and the real network are what made the module untestable in the first place.

## What not to TDD

TDD has costs. Apply it where the design feedback is worth it, and use judgment elsewhere.

| Situation | Why TDD is or is not a fit |
| --- | --- |
| Business rules with edge cases | Ideal. The test states the rule precisely |
| Core algorithms and data structures | Ideal. Hard to get right without examples |
| API and module boundaries | Ideal. The interface emerges from the caller's needs |
| Bug fixes | Ideal. The failing test is the reproduction |
| Configuration plumbing | Low value. Assert the configuration loads and the wiring is present |
| Framework glue | Low value. You would be testing the framework |
| UI layout and visual styling | Mostly not. Use component tests and E2E instead |
| Throwaway spikes and scripts | Not. Explore, discard, then rebuild test-first |

For "infrastructure" code with no interface, ask what observable behavior would prove it works. Often it is an integration test, and that is fine.

## Keeping tests fast

TDD only survives if the suite is fast enough to run constantly.

- A red-green cycle must complete in seconds. If a single test takes more than a few seconds, something at the boundary is not under your control.
- Push the boundary down. A unit test that opens a database connection is an integration test wearing a disguise.
- Use in-memory fakes for storage in unit tests, and reserve the real database for the integration suite.
- Parallelize at the runner level rather than making tests talk to each other.
- Watch the slowest tests explicitly. Most runners can print them; check your runner's docs.

```
5 slowest tests:
  4.2s test_export_csv  (writes to a real temp file, runs a subprocess)
  1.9s test_hash_password (uses an intentionally slow KDF cost factor)
  ...
```

The second one is fine: it is asserting a property of the real cost factor. The first is usually a seam that has not been built.

## Coverage and mutation testing

Coverage is a *finding* tool, not a goal. It tells you which lines never ran; it tells you nothing about whether the assertions were meaningful. A suite at 100 percent line coverage can have zero defect detection power.

Coverage is most useful when you look at it as:

- **Uncovered branches** that indicate code paths nobody reasoned about.
- **Uncovered code that is hard to cover**, which reveals a missing seam.

Mutation testing measures something closer to what you want: it changes your code in small ways (swap a comparison, remove a line, negate a condition) and checks that at least one test fails. A surviving mutant is a gap in your assertions.

| Tool | Language | Notes |
| --- | --- | --- |
| Stryker | JavaScript / TypeScript | Mutates the compiled unit under test |
| mutmut | Python | Simple and fast; check current usage with its docs |
| cosmic-ray | Python | Framework-agnostic, heavier |
| PIT | Java / JVM | Maven and Gradle plugins |
| go-mutesting | Go | Operates on source, so it needs careful build setup |

Treat mutation score as a diagnostic, not a gate you must hit 100 percent on. Prioritize surviving mutants in security-relevant, money-handling, and boundary code.

## Pairing and practice

TDD is a social practice as much as a technical one.

- Pair on hard design; solo on straightforward work.
- Timebox the loop. If you are still red after a few minutes, the test may be too big. Split it.
- Keep the refactor step non-negotiable. Skipping it leaves duplication that makes the next change harder, which pushes people back out of TDD.
- Review for the test's clarity: would a reader understand the rule from the name and the arrange block alone?

## Checklist

A TDD cycle is done when:

- [ ] You ran the test and watched it fail, for the expected reason.
- [ ] The production code you wrote is the minimum that makes it pass.
- [ ] No test was written after the implementation it verifies.
- [ ] The refactor step happened, and the tests stayed green without changing meaning.
- [ ] The test has one reason to fail and reads without consulting the source.
- [ ] The suite runs in seconds, so the next cycle is cheap.
- [ ] Legacy behavior that mattered has a characterisation test before you touch it.

## Common mistakes

- **Writing the test after the code.** Symptom: the test mirrors the implementation, passes immediately, and does not catch the misunderstanding you actually had. Fix: run the test first, watch it fail.
- **Red for the wrong reason.** Symptom: an import error, a typo, or a missing fixture, not the assertion you meant. Fix: read the failure output before writing implementation code.
- **Big-bang test.** Symptom: a hundred-line arrange block and a test that fails for many reasons. Fix: split into tests that each state one rule.
- **No refactor step.** Symptom: duplication grows, the next cycle takes longer, and people abandon TDD. Fix: make refactor part of the loop.
- **TDD on glue.** Symptom: you are writing tests for a framework's behavior and learning nothing. Fix: test the wiring, not the framework.
- **Red-green without a design question.** Symptom: the code is technically minimal but its interface is terrible. Fix: write the test as you wish the API looked, and let the interface follow.
- **Testing the implementation's structure.** Symptom: you have to change tests when you rename a helper. Fix: assert behavior, not internal calls.

## Anti-patterns

- **Test-after with no cycle.** Writing tests once at the end to satisfy a code-review checklist.
- **Refactoring under a red test.** If you restructure while red, you no longer know which change fixed what.
- **Asserting only the happy path first.** Add the edge cases, but add them as separate red-green cycles.
- **Over-mocking to force a red.** A test that fails because a mock returns the wrong thing is testing the mock.
- **Characterization tests on code you are about to delete.** Waste, and it anchors you to the wrong design.
- **TDD purity theater.** Claiming TDD without the red bar. It costs more than it buys and it teaches the team the wrong lesson.

## References

- Beck, "Test-Driven Development by Example" and the original TDD articles (archive) - https://web.archive.org/web/20140609163327/http://www.exampledomain.com/tdd/
- Martin Fowler, Test Driven Development - https://martinfowler.com/bliki/TestDrivenDevelopment.html
- Martin Fowler, Refactoring - https://refactoring.com/catalog/
- Michael Feathers, Working Effectively with Legacy Code (seams, characterization tests) - https://www.workingeffectivelywithlegacycode.com/
- Martin Roberts, The Cynefin framework (when a technique fits) - https://cynefin.com/
- Stryker mutation testing - https://stryker-mutator.io/docs/
- PIT mutation testing - https://pitest.org/
- Python coverage.py - https://coverage.readthedocs.io/
- Jest coverage - https://jestjs.io/docs/getting-started#coverage
- pytest fixtures for controlling time and state - https://docs.pytest.org/en/stable/how-to/fixtures.html