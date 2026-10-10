---
name: AI Agents and Tools
description: Covers tool and function schema design, tool selection and planning, ReAct-style agent loops, short and long term memory, multi-agent patterns such as router supervisor and parallel workers, Model Context Protocol concepts, guardrails and sandboxing, retries, failure handling, and runaway cost control. Use when the user asks about building an agent that calls tools, wiring an MCP server, designing agent memory, structuring multi-agent orchestration, or debugging an agent that loops, over-spends, or takes unsafe actions.
metadata:
  opencode/autoinvoke: true
---

# AI Agents and Tools

An agent is a loop: model decides, code acts, results go back to the model, repeat until done.
That loop is where every hard problem in agent engineering lives, because the model decides what
happens next and the model is fallible. Your job is to make the loop narrow, bounded, and
observable.

```
   +-------------------------------------------+
   |                                           |
   v                                           |
user goal -> [ planner ] -> [ act ] -> [ observe ] -> [ done? ] -no-+
                              ^                                    |
                              |                                    |
                              +------------- result ----------------+
```

Everything below is about constraining that loop: few tools, tight schemas, hard limits,
deterministic code for anything dangerous.

## When to use this

- The task needs multiple steps and tool calls whose number you cannot know in advance.
- You are choosing between a single tool call, a fixed pipeline, and an agent.
- You are exposing a tool surface to a model and need schemas it can call correctly.
- You are integrating external capability over the Model Context Protocol.
- An agent is looping, burning tokens, calling the wrong tool, or taking unsafe actions.

## First decision: agent or not

| Situation | Do this |
|---|---|
| One known call with known arguments | Direct function call |
| Fixed sequence of calls | Deterministic pipeline |
| Up to N calls, order depends on data | Bounded loop with a step cap |
| Unknown number of steps, open-ended goal | Agent, with tight limits and human checkpoints |
| High blast radius, low tolerance for error | Deterministic code plus human approval |

Agents earn their cost when the control flow genuinely depends on model judgment. They lose
when you could have written the branch. Start with the pipeline; add a loop only where a
measured failure needed one.

## Tool definition and schemas

A tool schema is an interface with a probabilistic caller. Write it for that caller.

```
name        one verb_phrase, snake_case, stable forever
description when to use it AND when not to use it
schema      narrow types, enums, required fields, no free-form where a choice exists
```

Good description:

```text
Look up the current billing status of a customer account.

Use when the user asks whether an account is active, past due, or suspended.
Do NOT use to fetch invoices or payment methods; use list_invoices for that.
Returns JSON with status, status_changed_at, and grace_period_ends_at.
```

Bad description: `Gets account info.` The model cannot distinguish it from three neighbors.

```python
ACCOUNT_TOOL = {
    "name": "get_account_status",
    "description": (
        "Look up the current billing status of a customer account. "
        "Use when the user asks whether an account is active, past due, or suspended. "
        "Do NOT use to fetch invoices or payment methods."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["account_id"],
        "properties": {
            "account_id": {
                "type": "string",
                "description": "Account identifier in the form acct_ followed by 12 characters.",
                "pattern": "^acct_[A-Za-z0-9]{12}$",
            }
        },
    },
}
```

Schema rules:

- **Narrow types.** Enums beat free strings. Bounds beat unbounded numbers.
- **All fields required** unless absence is genuinely meaningful, and make absence explicit with
  a nullable enum member rather than an omitted key.
- **Reject unknown fields.** Without `additionalProperties: false` the model will invent
  parameters and your handler must decide what to do with them.
- **Describe units and formats in the description**, not only the type: `amount_minor` in cents
  as an integer, not a float.
- **Keep the tool count small.** Many providers and models degrade as the tool list grows.
  Merge tools, or use progressive disclosure to load subsets on demand.
- **One job per tool.** `manage_billing(action, ...)` is worse than two named tools.

## Provider shape of tool definitions

| Concept | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Tool list | `tools[]` with `type: "function"` and a nested `function` object | `tools[]` with `name`, `description`, `input_schema` | `tools[]` with `function_declarations[]` |
| Schema field | `parameters` (JSON Schema) | `input_schema` (JSON Schema) | parameters schema on each declaration |
| Force a tool | `tool_choice` | `tool_choice` with `auto`, `any`, `tool`, or `none` | `toolConfig` |
| Result | assistant message with `tool_calls`, then a message with `role: "tool"` and `tool_call_id` | `tool_use` content block, then `tool_result` block in a user message | `functionCall` part, then `functionResponse` part |

All three are JSON Schema for the arguments. Keep one internal representation and translate at
the boundary, so you can switch providers without touching your tools. Validate again at
runtime with a real validator: the provider constrains generation, it does not guarantee it.

```python
import json
import os

from jsonschema import Draft202012Validator, ValidationError
from openai import OpenAI

client = OpenAI()
MODEL = os.environ["OPENAI_MODEL"]
validate_arguments = Draft202012Validator(ACCOUNT_TOOL["input_schema"]).validate

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_account_status",
            "description": "Look up billing status for an account.",
            "strict": True,
            "parameters": ACCOUNT_TOOL["input_schema"],
        },
    }
]


def call_tool(name: str, args: dict) -> str:
    if name != "get_account_status":
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        validate_arguments(args, ACCOUNT_TOOL["input_schema"])
    except ValidationError as exc:
        return json.dumps({"error": str(exc), "retryable": False})
    return json.dumps(db_lookup_account(args["account_id"]))
```

The `match` plus a default arm is not decoration. The model will call tools you did not
advertise, and you need a defined response rather than a crash.

## The agent loop

A minimal, production-shaped loop:

```python
import json
from collections.abc import Callable

ToolFn = Callable[[str, dict], str]

MAX_STEPS = 8
MAX_TOOL_SECONDS = 20.0


def run_agent(goal: str, tools: list[dict], tool_impls: dict[str, ToolFn], llm) -> str:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": goal},
    ]

    for step in range(1, MAX_STEPS + 1):
        resp = llm(messages=messages, tools=tools, tool_choice="auto")
        message = resp["choices"][0]["message"]
        messages.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            return message.get("content") or ""

        for call in calls:
            fn = call["function"]
            try:
                args = json.loads(fn["arguments"] or "{}")
            except json.JSONDecodeError:
                result = json.dumps({"error": "arguments were not valid JSON"})
            else:
                impl = tool_impls.get(fn["name"])
                if impl is None:
                    result = json.dumps({"error": f"unknown tool {fn['name']}"})
                else:
                    try:
                        result = impl(fn["name"], args)
                    except TimeoutError:
                        result = json.dumps({"error": "tool timed out", "retryable": True})
                    except Exception as exc:  # noqa: BLE001 - surfaced to the model as text
                        result = json.dumps({"error": str(exc), "retryable": False})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                }
            )

    return "Stopped: reached the step limit without a final answer."
```

Non-negotiables in that loop:

- **`MAX_STEPS`.** An agent with no step cap is a bill with no ceiling.
- **Tool errors return text, not exceptions.** The model needs to see what happened to recover.
  Returning a Python traceback burns tokens and teaches nothing.
- **Unknown tool names are handled.** Return an error the model can act on.
- **Every tool call is logged** with arguments and result size.
- **Timeouts are enforced per call**, so one hung tool does not stall the loop.

## Tool selection and planning

Three styles, in increasing order of cost and decreasing order of predictability:

| Style | Model behavior | Cost | Use when |
|---|---|---|---|
| Single shot | One call, structured output | One round trip | Extract, classify, one lookup |
| Router | Model picks one of N specialists | One round trip plus one task | Broad surface, distinct domains |
| Planner-executor | Model writes a plan, executor runs it | Several round trips | Multi-step, order matters |
| Free loop | Model decides each step | Unbounded | Genuinely unknown control flow |

A router is usually the best value: it buys specialization at the price of one extra model call.

```python
ROUTES = {
    "billing": BILLING_TOOLS,
    "technical": TECHNICAL_TOOLS,
    "account": ACCOUNT_TOOLS,
}

TOOL_TO_ROUTE = {t["function"]["name"]: route for route, group in ROUTES.items() for t in group}


def tools_for(route: str) -> list[dict]:
    return ROUTES[route]
```

Routing by a cheap classifier or by the model's first token is enough. Do not build a
planner-executor for a three-step task.

## Memory

| Kind | What it holds | Where it lives | Rule |
|---|---|---|---|
| Working | The current conversation and tool results | Message list in the request | Bound it. Summarize or truncate. |
| Scratch | Intermediate findings across tool calls | Session state on your server | Delete at session end |
| Long-term | Durable facts about the user or domain | A database you own | Write only on explicit signal |
| Semantic | Retrieved knowledge | The RAG index | Version it, never treat as truth |

Rules that keep memory from becoming the failure:

- **Bound the working set.** Every turn re-sends the whole conversation. Cap it: keep the system
  prompt, the last N turns, and a summary of the rest. Anthropic's Message Batches and the
  context-editing options in some SDKs exist for this; verify current support.
- **Summarize, do not truncate blindly.** Cutting mid-conversation loses the thread.
- **Long-term writes are explicit.** A model-inferred fact about a user is a liability. Store
  only what the user stated or a deterministic rule derived.
- **Memory you cannot delete is memory you cannot correct.** Provide an inspect-and-delete path.
- **Never put secrets into memory.** Model context is retrievable.

```python
def compact(messages: list[dict], keep_recent: int = 6) -> list[dict]:
    """Keep the system prompt and the last few turns; summarize the middle."""
    system = [m for m in messages if m["role"] in ("system", "developer")]
    rest = [m for m in messages if m["role"] not in ("system", "developer")]
    if len(rest) <= keep_recent:
        return system + rest

    head = rest[:-keep_recent]
    tail = rest[-keep_recent:]
    summary = {
        "role": "user",
        "content": "[summary of earlier turns] " + _summarize_turns(head),
    }
    return system + [summary] + tail


def _summarize_turns(turns: list[dict]) -> str:
    # Replace with a real summarization call or a template-based digest.
    return " | ".join(
        str(m.get("content", ""))[:160] for m in turns if m.get("content")
    )
```

## Multi-agent patterns

| Pattern | Shape | Use when | Failure mode |
|---|---|---|---|
| Router | One classifier, N specialists | Distinct domains | Misroutes and the user gets bounced |
| Supervisor | One lead agent, workers it calls | Workers need shared context | Lead becomes a bottleneck and a cost sink |
| Parallel | N workers, one aggregator | Independent subtasks | Duplicate work, conflicting writes |
| Sequential (pipeline) | A then B then C | Each stage refines the last | Error compounding with no checkpoint |
| Debate / critique | Two agents critique each other | Judgment tasks where one pass is weak | Cost multiplies, no guarantee of correctness |

Rules:

- Every agent needs its own tool set. Sharing one set means every agent can do everything.
- Pass structured summaries between agents, not raw transcripts. Token cost is the constraint.
- Aggregators must resolve conflicts deterministically or surface them.
- Nested agents are a cost trap. Two levels is usually the practical maximum.
- Multi-agent is not automatically better. Benchmark the single-agent version first.

## Model Context Protocol

MCP standardizes how an application connects to external capability servers, so you do not write
a bespoke integration for every data source. It is JSON-RPC 2.0 with three server primitives:

| Primitive | Who controls it | Purpose |
|---|---|---|
| Tools | Model | Model-invoked actions with a JSON Schema `inputSchema` |
| Resources | Application | Readable context addressed by URI |
| Prompts | User | Reusable prompt templates the user selects |

Lifecycle: the client sends `initialize` with its `protocolVersion`, `capabilities`, and
`clientInfo`; the server replies with its own capabilities and `serverInfo`; the client sends
`notifications/initialized`. Both sides then use only the capabilities that were negotiated.
Version negotiation is explicit: if the server cannot support the requested version it replies
with a different one, and the client should disconnect if it cannot speak it.

Two transports are defined: **stdio** for local servers the client spawns as a child process,
and an HTTP transport for remote servers. Read the current transports page for the exact HTTP
transport definition, since it has been revised.

Tool discovery and invocation:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": { "location": "New York" }
  }
}
```

Results come back as a `content` array of typed blocks (text, image, audio, resource links,
embedded resources) plus an `isError` flag. A tool can also declare an `outputSchema`, and a
server that declares one must return structured content conforming to it. Tool listing supports
pagination, and servers that declare `listChanged` can emit `notifications/tools/list_changed`
so clients refresh.

Security requirements worth taking literally:

- Tool annotations are **untrusted input** unless the server is trusted. Do not let an
  annotation such as a hint disable your confirmation prompts.
- Validate every tool input, enforce access control, rate limit invocations, and sanitize tool
  outputs on the server side.
- On the client side: show the user the arguments before calling, require confirmation for
  sensitive operations, validate results before feeding them back to the model, set timeouts,
  and log usage for audit.
- Treat anything an MCP server returns as untrusted content in the model context. A tool result
  can carry a prompt injection.

MCP is a protocol, not a trust boundary. The server is code you did not write; constrain what
it can reach.

## Guardrails and sandboxing

| Guardrail | Placement | What it stops |
|---|---|---|
| Argument validation | Before the tool runs | Malformed or out-of-range parameters |
| Authorization check | In the tool, per call | Acting on data the user cannot see |
| Allowlist of tools per session | Tool registry | Unnecessary capabilities exposed |
| Confirmation gate | Before irreversible calls | Accidental or injected side effects |
| Output validation | After the tool runs | Poisoned context, malformed payloads |
| Network egress restriction | Sandbox | Exfiltration via a tool |
| Filesystem scoping | Sandbox | Reading or writing outside the workspace |
| Step, time, and token caps | Agent loop | Runaway cost and loops |
| Per-user rate limits | Gateway | Abuse and cost spikes |

Design note: put irreversible operations behind a **separate, human-confirmed code path**. The
model can propose `delete_project`; it cannot call it. That single split removes a large class
of failure.

## Failure handling and retries

| Failure | Detection | Response |
|---|---|---|
| Malformed tool arguments | Schema validation | Return the validation error once, then stop |
| Tool timeout | Per-call deadline | Return `retryable: true` and cap retries |
| Transient downstream error | 5xx, connection reset | Exponential backoff with jitter, 2 to 3 tries |
| Auth failure | 401 or 403 | Do not retry. Surface and stop |
| Not found | 404 or empty result | Return an explicit "no results" the model can use |
| Model loop | Same call repeated | Detect repeats and break |
| Step cap | Loop counter | Stop and return a partial result with a reason |

Detect degenerate loops explicitly: if the same tool with the same arguments appears twice in a
row, stop and ask.

```python
def has_looped(calls: list[tuple[str, str]], window: int = 2) -> bool:
    """True when the last `window` (tool, args) pairs are identical."""
    if len(calls) < window:
        return False
    tail = calls[-window:]
    return len(set(tail)) == 1
```

## Cost control

- Cap steps, tokens per step, wall-clock, and total spend per session.
- Use the smallest model that clears your eval, then a larger one for the hard cases.
- Keep tool definitions in the cacheable prefix of the request.
- Return compact tool results. A tool that dumps 4000 tokens of JSON into context is a cost bug.
  Return a summary plus a handle the agent can query further.
- Batch independent tool calls where the provider supports parallel tool calls.
- Cache deterministic tool results within a session.
- Route off-peak or non-interactive work to the provider's batch API when latency allows.

## Common mistakes

- **Tool description says what it does but not when not to use it.** Symptom: the model picks a
  neighboring tool. Fix: add an explicit "do not use for..." line.
- **No `additionalProperties: false`.** Symptom: invented arguments reach your handler as a 500.
  Fix: close the schema and validate again at runtime.
- **Tool exceptions escape the loop.** Symptom: the agent dies mid-task and the user sees a
  stack trace. Fix: convert to a structured error result for the model.
- **Unbounded loop.** Symptom: the bill spikes overnight. Fix: hard step cap plus a spend cap.
- **Dozens of tools.** Symptom: wrong tool chosen, or a request that exceeds the tool limit.
  Fix: fewer tools, or progressive disclosure.
- **Every agent shares every tool.** Symptom: privilege escalation through an unintended agent.
  Fix: per-agent tool sets.
- **Trusting MCP tool annotations.** Symptom: a third-party server's annotation suppresses your
  confirmation prompt. Fix: annotations are untrusted input.
- **Tool results fed to the model unvalidated.** Symptom: injection through a tool response.
  Fix: validate and delimit.
- **Retrying non-retryable errors.** Symptom: an auth failure loops until the cap. Fix: classify
  errors and never retry 4xx auth or permission failures.

## Anti-patterns

- **"Let the agent figure it out"** as a substitute for a workflow you already know.
- **Infinite planning loops** where the agent re-plans without acting.
- **Shared mutable state between agents** with no coordination, producing lost updates.
- **Sandboxing after the fact.** Sandboxing must wrap the tool call, not observe it.
- **Trusting the model's claim that a tool call was safe** because it said so.
- **No human checkpoint** on any irreversible action.
- **Building a multi-agent system to route three intents.** A switch statement does that.
- **Evaluating an agent only on the happy path.** Adversarial and no-op cases are where agents
  break.

## Checklist

- Every tool has a one-purpose name, a description with a "do not use for" clause, and a closed
  schema.
- Irreversible operations are behind a separate confirmed code path.
- The loop has a step cap, a time cap, and a spend cap.
- Tool errors return structured text to the model and never escape as exceptions.
- Repeated identical tool calls are detected and break the loop.
- Working memory is bounded and compacted.
- Long-term memory is explicitly written, inspectable, and deletable.
- MCP servers are treated as untrusted: annotations ignored for policy, outputs validated.
- Per-agent tool sets exist and are tested.
- There is an eval set covering success, failure, refusal, and adversarial inputs.

## References

- Model Context Protocol specification: https://modelcontextprotocol.io/specification/latest
- MCP architecture: https://modelcontextprotocol.io/docs/learn/architecture
- MCP tools: https://modelcontextprotocol.io/specification/latest/server/tools
- MCP transports: https://modelcontextprotocol.io/specification/latest/basic/transports
- OpenAI function calling: https://platform.openai.com/docs/guides/function-calling
- OpenAI Chat Completions API reference: https://platform.openai.com/docs/api-reference/chat/create
- Anthropic tool use: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview
- Anthropic Messages API: https://docs.anthropic.com/en/api/messages
- Gemini function calling: https://ai.google.dev/gemini-api/docs/function-calling
- Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OWASP Top 10 for LLM Applications: https://owasp.org/www-project-top-10-for-large-language-model-applications/