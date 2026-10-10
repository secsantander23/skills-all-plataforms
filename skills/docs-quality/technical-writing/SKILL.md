---
name: Technical Writing
description: Covers audience analysis, task-first structure, writing procedures and reference material, sentence-level clarity, terminology consistency, code examples that actually run, revision passes, a docs-as-code review workflow, and writing checklists. Use when the user asks how to write documentation, README pages, guides, or API explanations, wants a document reviewed for clarity, or is struggling with docs that nobody can follow.
metadata:
  opencode/autoinvoke: true
---

# Technical Writing

Technical writing is decision-making under a constraint: the reader has a task, limited
attention, and no interest in your implementation. Every sentence either helps them finish the
task or is cut. Structure carries the meaning; prose carries the detail.

## When to use this

- Writing or restructuring any user-facing documentation.
- Reviewing a document and diagnosing why people cannot follow it.
- Choosing between a tutorial, a how-to guide, a reference, or an explanation.
- Reducing a long document to something people finish.
- Standardizing terminology so two documents stop contradicting each other.

## Quick start

The task-first template that works for most how-to guides:

```markdown
# Reset your API credentials

Use this when your integration returns `401 Unauthorized` and rotating
the credentials is the documented remedy.

## Before you start

- Access to the admin console with the Owner role
- Your integration ID (find it under Settings → Integrations)

## Steps

1. Open **Settings → Integrations**.
2. Select your integration, then **Rotate credentials**.
3. Copy the new secret. It is shown once.
4. Update the `ACME_API_KEY` environment variable in every
   deployment that uses this integration.
5. Redeploy.

## Verify

Send any authenticated request. A `200` response confirms the new
credential is active.

## If this does not work

- `401` after redeploy means the old secret is still set somewhere.
  Check every deployment target, including local `.env` files.
- A `403` means the role is wrong; rotation requires Owner.
```

Lead with what the reader will accomplish, then the prerequisites, then the steps. No history,
no architecture tour.

## Audience analysis

Before writing a line, answer:

- **Who reads this?** The person debugging a 3am incident has a different need from a person
  evaluating whether to adopt your product. Say which one the document serves. A document that
  serves both is two documents.
- **What do they already know?** Assume the platform and the language, not your codebase.
  Never assume they know your internal module names.
- **What will they be doing while reading?** Reading in a terminal with a deadline, or reading
  carefully with a coffee? The answer decides whether the steps are numbered and terse or
  prose and explanatory.
- **What is the failure they came for?** Write the error message or the symptom verbatim in
  the first paragraph so they confirm they are in the right place.

Match the register to the reader. Beginners need "what is this and why"; experts need "here is
the exact call and its constraints". Mixing the two in one paragraph serves neither.

## Choosing the document type

| Type | Answers | Structure | Success measure |
| --- | --- | --- | --- |
| Tutorial | "Teach me by doing" | Ordered steps, guaranteed result, no alternatives | Reader finishes with something working |
| How-to guide | "Help me do X" | Task-first, prerequisites, steps, verification | Reader completes the task |
| Reference | "What exactly does this accept and return?" | Exhaustive, scannable, consistent | Reader finds the exact thing |
| Explanation | "Why is it like this?" | Concept, context, tradeoffs | Reader can predict behavior |
| Tutorial vs how-to | Tutorial has a guaranteed path; how-to has options | — | — |

Rule of thumb: if the reader already knows what they want, they want a how-to, not a tutorial.
Tutorials that branch into "or you could instead" become unusable.

## Structure

Put the answer before the reasoning. Readers scan headings; make each heading a question they
might have.

```markdown
# Configure request retries          <- what this does
## How many retries should I configure?   <- a question they might have
## Why does a retry fail silently?       <- only if they hit that
## Advanced: jitter and exponential backoff
```

Rules that measurably help:

- Front-load. Conclusion, then evidence, then detail.
- One idea per section. If a section needs "also", split it.
- Headings form an outline that reads correctly on its own, with no body text. If it does not,
  the document has an ordering problem.
- Reference material is organized by the reader's lookup path (alphabetical, by task), not by
  the code's structure.
- Length is not the quality metric. Completion is.

## Writing procedures

A procedure is a list of actions that leads to a verifiable result.

```markdown
## Steps

1. Open **Settings → Integrations**.
2. Select your integration.
3. Select **Rotate credentials**.
```

Each step should have:

- One action. "Configure the settings" is two or more actions.
- A location. Where in the interface, which file, which path.
- The literal value to enter, in code formatting, not described.
- The state the reader is in afterward, when it is not obvious.

Add these sections around the steps:

```markdown
## Before you start
## Steps
## Verify          <- how do I know it worked
## Roll back       <- how do I undo this
## If this does not work
```

**Verify** is the section most often missing and the most often needed. It converts "I clicked
the button" into "it is working".

Procedures must be linear. If step 4 says "if you skipped step 2, go back", the document has
two paths; split it into two procedures or add a conditional note at the top.

## Writing reference material

Reference is the part readers scan, not read.

- One entry per thing, in a fixed format. Every parameter has a type, a default, a required
  flag, and a description in the same order, every time.
- Alphabetical or task-ordered, not chronological.
- Say what the default is. "Defaults to the workspace setting" is a reference entry that can
  be acted on.
- Include units and allowed values in the name: `timeout_ms`, `retries`, not `options`.

```markdown
### `timeout_ms`

Type: `integer`
Default: `30000`
Minimum: `1`

Maximum time to wait for a single upstream request, in milliseconds.
Applies per attempt, so a request with `retries: 3` can take up to
`4 × timeout_ms`.
```

## Sentence-level clarity

Editing passes, in order:

1. **Cut.** Delete sentences that do not change an action or a decision.
2. **Front-load.** Move the conclusion to the front of the paragraph. "The request fails when
   the token has expired. Expired tokens are rejected by `validate()`" becomes "Expired tokens
   are rejected by `validate()`, so the request fails."
3. **Replace abstractions with actions.** "Perform the necessary configuration" → "Set
   `ACME_API_KEY` in your deployment environment."
4. **Make subjects explicit.** Name the actor. "This is handled" → "The gateway retries the
   request."
5. **Shorten long sentences.** Under 25 words is a reasonable working limit. Split at
   conjunctions when a sentence carries two independent claims.
6. **Use active voice.** "The token is rejected by the server" → "The server rejects the
   token."
7. **Cut filler.** "In order to", "it is important to note that", "simply", "just", "actually".
8. **Define on first use.** Do not use a term before its definition, and do not define a term
   the reader already knows.

Hedging language is a symptom, not a style. "This may sometimes cause a delay" means you
do not know when. Either find out or say what is known.

## Terminology consistency

Pick one term per concept and write it down in a glossary or style note.

| Avoid | Use | Why |
| --- | --- | --- |
| "log in" / "log on" / "sign in" | one of them | Readers search for the string |
| "delete" / "remove" / "destroy" | one per operation | Implies different outcomes |
| "user" / "account" / "customer" | one | They are not always the same thing |
| "credential" / "key" / "token" | the precise type | Security docs depend on it |
| "error" / "failure" / "exception" | the precise one | Different layers |

Enforce with linting where you can. A terminology list in `CONTRIBUTING.md` plus a check for the
most common offenders is cheaper than a full style guide nobody reads.

## Code examples that work

An example that does not run is worse than no example: it teaches the wrong thing
authoritatively.

- Make examples complete enough to run — no `# ...existing code...` inside a block meant to be
  pasted.
- Use realistic, readable values: `acme-webhook`, not `foo`.
- Show the input and the output. A request with no response teaches nothing.
- Keep examples short enough to fit on one screen.
- Mark anything the reader must substitute with a clear placeholder.
- Test examples. Anything that can be executed from a doc should be executed by a test or a
  fenced block extractor, so it stops being aspirational.

````markdown
Send a signed webhook:

```python
import hmac, hashlib

def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

body = b'{"event":"invoice.paid"}'
signature = sign(body, "whsec_example_not_a_real_secret")
print(signature)
```

The signature goes in the `Acme-Signature` header, prefixed with the
timestamp. Verify with `hmac.compare_digest`, not `==`.
````

## Docs-as-code workflow

Keep documentation in the repository, in Markdown, next to the code it describes.

```text
docs/
  README.md            <- index
  guides/
    rotate-credentials.md
  reference/
    api.md
  style/
    terminology.md
    voice-and-tone.md
```

Workflow that scales:

1. Documentation lives in version control; changes go through the same review as code.
2. CI builds the docs on every PR so a broken link or example fails the build.
3. The author of a behavior change updates the docs in the same PR, enforced by a PR template
   checkbox.
4. Every page has an owner, expressed with CODEOWNERS on the `docs/` directory.
5. A dated review cadence per page: reference pages reviewed by the owning team, guides
   reviewed by someone who has never followed them.

Review for docs is different from review for code. Check: is the task identifiable, does each
step have one action, do examples run, is the terminology consistent, does it say what
happens on failure.

## Revision

Revision passes, each with a different focus:

1. **Structure pass.** Does the outline read correctly on its own? Is the answer before the
   reasoning?
2. **Completeness pass.** Is every step an action? Does every procedure have prerequisites,
   verification, and a failure path?
3. **Accuracy pass.** Verify every command, flag, path, and default against the code or the
   tool's current `--help`.
4. **Clarity pass.** Cut filler, front-load, make subjects explicit.
5. **Consistency pass.** Terminology, heading style, code fence languages, capitalisation.
6. **Fresh-eyes pass.** Give it to someone who has not read it and watch where they stop. Do
   not explain it while they read.

Do these in separate passes. One pass doing all six does none of them well.

## Checklist

- [ ] Title names the task, not the feature ("Reset your API credentials", not "Credentials").
- [ ] Opening paragraph tells the reader whether this is the right page.
- [ ] Audience is single and stated in the style note.
- [ ] Steps are numbered, one action each, with a location.
- [ ] Prerequisites listed before step 1.
- [ ] Verification step included.
- [ ] Failure modes covered with the literal error message.
- [ ] Commands, flags, and defaults verified against the tool, not from memory.
- [ ] Code examples are complete and runnable.
- [ ] Terminology matches the project glossary.
- [ ] Links are absolute or relative paths that exist.
- [ ] Page has an owner.

## Common mistakes

**Writing the tutorial you needed, not the one they need.** Symptom: the page explains a
concept beautifully and never says what to click. Fix: run the task yourself with the reader's
goal and note every step you looked up.

**Documenting the architecture instead of the task.** Symptom: three paragraphs of layering
before the first instruction. Fix: move the explanation to the end or a separate page.

**Examples with `...` that do not compile.** Symptom: the reader pastes and gets a syntax
error, then stops trusting the page. Fix: make examples complete or extract and test them.

**Values that drift from the code.** Symptom: the documented default is three releases old.
Fix: generate the reference from the code, or add a test that asserts the documented default.

**Writing for search engines instead of readers.** Symptom: keyword-stuffed headings, content
that reads as though written to rank. Fix: describe the task the way a reader would.

**Every page written at the same altitude.** Symptom: three paragraphs of context before a
one-step task. Fix: pick the altitude for the page type and stay there.

**Updating the code and not the docs.** Symptom: a deprecated flag still documented as
current. Fix: put a docs checklist in the PR template and gate the merge on it.

## Anti-patterns

- **Screenshots as the only instruction.** They go stale silently and are inaccessible. Name
  the control in text as well.
- **Marketing voice in a manual.** "Simply unleash the power of..." costs credibility and hides
  the action.
- **"Note:" as a substitute for structure.** If there is a caveat, it belongs in a named
  section.
- **Hiding prerequisites at the point of failure.** Say them before step 1.
- **One enormous reference page.** Split by object; index it.
- **Rewriting the implementation's internal names into prose.** Readers do not have your class
  names.
- **A style guide so long that nobody reads it.** Ten rules that are enforced beat a hundred
  that are not.
- **Documenting errors nobody have seen.** If you do not know the failure mode, do not invent
  one; describe what to collect and where to ask.

## References

- Google developer documentation style guide:
  https://developers.google.com/style/high-documentation
- Google style guide, principles of writing: https://developers.google.com/style
- Microsoft Writing Style Guide: https://learn.microsoft.com/en-us/style-guide/welcome/
- Write the Docs guide: https://www.writethedocs.org/guide/
- W3C internationalization article on consistent terminology:
  https://www.w3.org/International/questions/qa-howto-terms
- Google developer documentation style guide, code samples:
  https://developers.google.com/style/code-samples
- Diátaxis framework for documentation types: https://diataxis.fr/
- Divio documentation framework: https://docs.divio.com/documentation-framework/
- GitHub Docs contributing guide (a real docs-as-code repository in practice):
  https://github.com/github/docs/blob/main/CONTRIBUTING.md