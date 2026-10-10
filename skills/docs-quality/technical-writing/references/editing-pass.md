# Editing passes and terminology

Working material for `technical-writing`.

## Sentence surgery

Before and after, so the passes are concrete.

| Before | After | Pass |
| --- | --- | --- |
| "In order to reduce the latency, it is recommended that you configure the timeout parameter." | "Set `timeout_ms` to reduce latency." | Cut filler, active voice, literal value |
| "This may sometimes result in a delay of a few hundred milliseconds." | "Queued requests wait for the queue to drain; the drain interval is 250 ms." | Replace hedging with mechanism |
| "The token is rejected by the API gateway." | "The API gateway rejects the token." | Make the actor explicit |
| "Note that the account must be an administrator." | "Only accounts with the Administrator role can rotate credentials." | Condition as a rule |
| "You can either use the CLI or the web interface to do this." | Split into two procedures with links. | One path per procedure |

## Terminology sheet template

Keep it in `docs/style/terminology.md` and reference it from `CONTRIBUTING.md`.

```markdown
# Terminology

| Concept | Use this | Do not use | Note |
| --- | --- | --- | --- |
| Authentication | authenticate | authorise, log in | Sign-in is a UI action; authentication is the check |
| Workspace | workspace | organisation, tenant, account | A workspace contains projects |
| Deleting a key | revoke | delete, remove | Revocation is immediate and auditable |
| Error | error | failure, exception | Exception is the language-level construct |
| Retry | retry | re-attempt, try again | Include the count and the backoff |
```

One row per contested concept. Ten rows is a usable sheet; a hundred is not.

Enforcing it cheaply:

- Add the most-replaced terms to a spelling/wordlist check in CI.
- Put the sheet next to the PR template so reviewers can link to it rather than paste it.
- Revisit it quarterly; contested terms change as the product changes.

## Structure diagnosis

Read only the headings of a document, in order. They should read as an answer to the reader's
questions.

Weak outline:

```text
Introduction
Overview
Background
Implementation Details
Configuration
Usage
Notes
Conclusion
```

Strong outline:

```text
Reset your API credentials
Before you start
Steps
Verify
If the new credential returns 401
Roll back
How rotation interacts with signing keys
```

The second outline can be evaluated without the body. The first cannot.

## Procedure template

```markdown
# <Task in the imperative>

<One sentence: when to use this page. Include the symptom or error
string if the reader arrives from a failure.>

## Before you start

- <permission or role needed>
- <file, credential, or access required>
- <version constraint, if any>

## Steps

1. <One action> (<where>).
2. <One action> (<where>).
3. <One action, with the literal value to enter>.

## Verify

<One command or one observable UI state, and what a pass looks like.>

## Roll back

<How to undo, or a link to the runbook.>

## If this does not work

| Symptom | Cause | Fix |
| --- | --- | --- |
| `401 Unauthorized` after redeploy | Old secret still set in another target | Check every deployment target |
| `403 Forbidden` | Role is not Owner | Grant the Owner role |
```

## Length budgeting

- A how-to that fits one screen will be finished. Most finish a single numbered list.
- A reference page may be long, but it must be scannable: fixed entry format, one entry per
  heading.
- Split a procedure at 20 steps. You have lost the reader.
- If a page exceeds roughly 1500 words and has no subsections, it is two pages.

## Reader-testing protocol

1. Give the page to a colleague who has not used the product.
2. Do not explain anything. Note every question they ask.
3. Record every place they scroll back up.
4. Every question is a sentence the page failed to answer in place.

## Doc review checklist for PRs

- [ ] New behavior has a docs change in the same PR.
- [ ] Every command in the change is copy-pasteable and has been run.
- [ ] Every new flag, option, or default is documented where a user would look for it.
- [ ] Deprecated things are marked deprecated with a replacement and a removal condition.
- [ ] Terminology matches `docs/style/terminology.md`.
- [ ] Screenshots accompany UI instructions but do not replace them.
- [ ] The change adds no unexplained jargon.

## Verify before relying on this

Style guides evolve and differ between organizations. Treat the linked guides as models to
adapt, not as rules imposed on your project. Where this skill and an in-house style guide
disagree, the in-house guide wins.