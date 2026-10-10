---
name: mcp-server-authoring
description: Covers authoring Model Context Protocol servers, including tools, resources, and prompts, tool schemas the model can select on, stdio versus Streamable HTTP transports, input validation, error handling, authorization for remote servers, testing with the MCP Inspector, and publishing. Use when the user mentions MCP, building or debugging an MCP server, adding a tool to an agent, or wiring a client to a server.
metadata:
  opencode/autoinvoke: true
---

# MCP Server Authoring

The Model Context Protocol is a JSON-RPC interface between an AI application and the
systems it needs. A server exposes three capability types to a client; the client is
embedded in a host that decides when to call them. Your job as an author is to make
each capability cheap for a model to choose and impossible for it to misuse.

Verify before relying on this: the protocol and the SDKs move quickly. The
authoritative sources are https://modelcontextprotocol.io and the specification pages
linked from it. SDK import paths in particular differ between major versions, so
confirm them against the version you actually install.

## When to use this

- The user wants to build an MCP server, or to add a tool to an existing one.
- A model calls the wrong tool, or refuses to call the right one.
- A tool fails in ways the model cannot recover from.
- A remote MCP server needs authentication, authorization, or per-user scoping.
- The user asks about resources versus prompts versus tools and which to expose.

## Quick start

A complete stdio server in TypeScript. The shape is stable across SDK versions; the
import specifiers are the part to verify.

```typescript
// src/index.ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({
  name: "text-stats",
  version: "1.0.0",
});

server.registerTool(
  "count_words",
  {
    title: "Count words",
    description:
      "Count words, unique words, and lines in a UTF-8 text file. Read-only. Returns counts only, never the file body.",
    inputSchema: z.object({
      path: z.string().describe("Absolute path to a UTF-8 text file"),
    }),
    outputSchema: z.object({
      words: z.number(),
      uniqueWords: z.number(),
      lines: z.number(),
    }),
  },
  async ({ path }) => {
    const text = await readTextFile(path);
    const words = text.split(/\s+/u).filter(Boolean);
    const structured = {
      words: words.length,
      uniqueWords: new Set(words.map((w) => w.toLowerCase())).size,
      lines: text.length === 0 ? 0 : text.split("\n").length,
    };
    return {
      structuredContent: structured,
      content: [{ type: "text", text: JSON.stringify(structured) }],
    };
  },
);

async function readTextFile(path: string): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(path, "utf8");
}

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // stdout is the JSON-RPC channel. Log to stderr or the transcript breaks.
  console.error("text-stats MCP server running on stdio");
}

main().catch((error) => {
  console.error("fatal:", error);
  process.exit(1);
});
```

Run it, then inspect it:

```bash
npm install @modelcontextprotocol/sdk zod
npx tsx src/index.ts
# in another terminal
npx @modelcontextprotocol/inspector node dist/index.js
```

## Concepts

| Term | Meaning |
| --- | --- |
| Host | The AI application. Owns the user session, decides when to call tools, and is responsible for consent. |
| Client | The 1:1 protocol connection a host opens to one server. |
| Server | Your program. Holds the credentials and the network access; the model never does. |
| Primitive | A capability a server declares: tools, resources, or prompts. |

The security boundary is the server, not the model. Anything the model should not do,
the server must not offer. Anything the model should not see, the server must not
return.

### Capability selection

| Primitive | Model-invoked | Purpose | Typical shape |
| --- | --- | --- | --- |
| Tools | Yes, after consent | Model-invoked actions with side effects | `search_docs(query, top_k)` |
| Resources | No, host or app reads them | File-like data the application can read and attach | `docs://guide/getting-started` |
| Prompts | User-invoked | Reusable workflow templates the user selects | `review_diff(diff)` |

Rule of thumb: if the model should decide when it happens, it is a tool. If the
application decides, it is a resource. If the user decides, it is a prompt. Do not
expose one thing as all three; you create three descriptions for the model to
disambiguate for no benefit.

## Tool design

Models select tools by name and description, and fill arguments from the input schema.
Optimise the description and schema for that.

Write descriptions that answer three questions:

1. What does this do, in one sentence starting with a verb?
2. When should the model reach for it instead of a sibling?
3. What does it return, and what does it not do?

```json
{
  "name": "search_issues",
  "title": "Search tracker issues",
  "description": "Search tracker issues by free text, optionally filtered by status and label. Returns up to 50 issues with id, title, status, labels, and assignee. Does not create, edit, or close issues; use create_issue or update_issue for that.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Free text, matched against title and body" },
      "status": {
        "type": "string",
        "enum": ["open", "closed"],
        "description": "Restrict to one status; omit for both"
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "default": 20,
        "description": "Maximum issues to return"
      }
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

Schema rules that reduce failed calls:

- Set `additionalProperties: false` unless the tool genuinely accepts free-form keys.
  The model then gets an error instead of silently dropping a misspelled argument.
- Use `enum` whenever the value set is closed. An enum in the schema beats three
  sentences of prose in the description.
- Mark required fields in `required`, and give optional fields defaults.
- Describe units and formats explicitly: `timeout_ms`, `iso_date`, `2` means what.
- Bound numeric ranges. An unbounded `limit` invites a model to request everything.
- Give `path`-like fields a description that says relative to what, and resolve them
  server-side against a configured root.

### Names

Use `verb_noun` in snake case: `create_issue`, `list_files`, `run_query`. Avoid
abbreviations the model has to guess, and avoid two tools whose names differ only by
plural, since that reliably produces a wrong-tool call.

### Return shape

- Return the fields the model needs to decide the next step, and nothing else.
- When a result is large, return a summary plus an id, and expose a separate tool to
  page into the detail. Do not dump thousands of rows into context.
- If you declare `outputSchema`, populate `structuredContent` as well as a text block.
  Hosts and models both use it, and structured data removes a parsing step.
- Never return secrets, tokens, or internal identifiers the model has no use for. Model
  output is logged, cached, and shown to users.

## Input validation and safe output

Validate everything at the boundary. Assume the input is hostile even when the caller
is your own host.

```typescript
import { z } from "zod";

const SearchArgs = z.object({
  query: z.string().min(1).max(500),
  path: z.string().max(4096).optional(),
  limit: z.number().int().min(1).max(50).default(20),
}).strict();

server.registerTool(
  "read_notes",
  { description: "Read one note by id.", inputSchema: SearchArgs },
  async (raw) => {
    const args = SearchArgs.parse(raw);
    const resolved = resolveInsideRoot(args.path ?? "");
    if (!resolved) {
      return toolError("path escapes the configured notes root");
    }
    return toolResult(await readNote(resolved));
  },
);
```

Then enforce the rest of the contract in code, not in prose:

- Resolve user-supplied paths against a configured root and reject anything that
  escapes it after normalisation. Reject symlinks that point outside the root.
- Bind every query parameter in the parameterised form. Never build SQL by
  concatenation, even when the value came from an enum.
- Cap result sizes and timeouts inside the tool, not by asking the model to be careful.
- Redact secrets from anything that enters an error message.

## Error handling the model can act on

There are two distinct failure channels and they mean different things.

Protocol errors are for broken conversations: unknown method, malformed request, bad
params shape. Return a JSON-RPC error object with a numeric code and a short `message`.

Tool execution failures are part of the conversation. A tool that fails because a file
does not exist has produced useful information; surface it as a normal result marked as
an error, so the model reads it and adapts.

```typescript
function toolError(reason: string, nextStep?: string) {
  return {
    isError: true,
    content: [
      {
        type: "text" as const,
        text: nextStep ? `${reason}. Next step: ${nextStep}` : reason,
      },
    ],
  };
}

function toolResult(value: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(value) }],
    structuredContent: value,
  };
}
```

Write the message for the reader who has to fix it:

| Bad | Good |
| --- | --- |
| `Error: 500` | `Upstream API returned 500 on the third retry. The request was valid. Retry later or use cached data.` |
| `Invalid input` | `Field 'status' must be one of open, closed. Received 'resolved'.` |
| `File not found` | `No file at notes/2024-q1.md. The notes directory contains 3 files; list_notes returns them.` |
| `Database error` | `Query exceeded the 5s statement timeout. Narrow the date range or add a LIMIT.` |

Include the offending value, the accepted values, and a next action. Never include a
stack trace, a connection string, or a token.

## Transports

| Transport | Shape | Use when |
| --- | --- | --- |
| stdio | The client spawns the process and speaks newline-delimited JSON-RPC over its stdin and stdout | Local, single-user, development, and CI |
| Streamable HTTP | One endpoint the client POSTs to; the server may upgrade the response to an event stream for server-initiated messages | Remote, multi-user, hosted, horizontally scaled |

Choose stdio for a local tool. Choose Streamable HTTP when the server outlives the
session, is shared between users, or needs to scale. Adding HTTP means you own sessions,
authentication, rate limiting, and tenancy, none of which stdio gives you for free.

### The stdout rule

On stdio, stdout is the protocol channel. Any stray byte there corrupts the stream and
the client reports a parse failure, usually with a confusing message about invalid JSON.

| Runtime | Breaks the stream | Safe |
| --- | --- | --- |
| Node | `console.log`, `process.stdout.write` | `console.error`, `process.stderr.write` |
| Python | `print`, `sys.stdout.write` | `logging` configured to stderr, `sys.stderr.write` |
| Java | `System.out.println` | a logger configured to stderr, `System.err` |

Route every dependency's logging to stderr too. A transitive library that prints to
stdout is enough to break the server, so test with the full dependency set installed.

## Resources and prompts

Resources are addressed by URI. Pick a scheme that describes the data and keep URIs
stable, because clients cache and bookmark them.

```text
file:///notes/2024-q1.md          mimeType: text/markdown
db://customers/42                 mimeType: application/json
https://internal.example/runbook  mimeType: text/markdown
```

Return only a resource body, never instructions. Resources reach the model, and content
that lands in model context is input, not authority.

Prompts are user-selected templates with declared arguments:

```json
{
  "name": "review_diff",
  "description": "Review a unified diff for correctness, security, and test coverage.",
  "arguments": [
    { "name": "diff", "description": "Unified diff text", "required": true },
    { "name": "focus", "description": "Optional area to emphasise", "required": false }
  ]
}
```

Keep prompts free of authority claims. A prompt is a suggestion the user started.

## Authorization for remote servers

A remote MCP server is an internet-facing API holding real credentials. Treat it that way.

- Use OAuth as the protocol specifies, and implement the discovery documents the
  authorization specification defines: the authorization server metadata and the
  protected resource metadata. Do not invent your own token endpoints or a bespoke
  header scheme.
- Never accept a token from the client and forward it to a downstream API. That token
  passthrough pattern turns your server into an amplifier for whatever the client is
  authorised for. Exchange tokens at your server and issue your own downstream
  credentials.
- Derive the effective scope from the token plus the authenticated subject, never from
  a tool argument. A model-supplied `user_id` is attacker-controlled.
- Rate limit per token and per subject, and log the subject with every tool call.
- Reject any outbound URL the model can influence unless it is on an allow list, or you
  have built a server-side request forgery defence.

## Testing

Test at three levels.

Protocol level, with the inspector, for smoke coverage:

```bash
npx @modelcontextprotocol/inspector node dist/index.js
```

In the inspector, list tools, call each one with valid arguments, then call each one
with deliberately wrong arguments and confirm the error message tells the model what to
fix.

Unit level, with an in-memory transport pair, so the suite stays fast and runs in CI
without spawning a process. The SDK exposes an in-memory transport for exactly this.

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, it } from "vitest";

describe("text-stats server", () => {
  it("rejects a path outside the notes root", async () => {
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const client = new Client({ name: "test", version: "0.0.0" });
    await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);

    const tools = await client.listTools();
    expect(tools.tools.map((t) => t.name)).toContain("count_words");

    const result = await client.callTool({
      name: "count_words",
      arguments: { path: "../../etc/passwd" },
    });
    expect(result.isError).toBe(true);
    expect(JSON.stringify(result.content)).toContain("root");
  });
});
```

Selection level, which is the one that actually matters and the one teams skip. Build a
small set of realistic user requests, record which tool the model chose for each, and
assert it chose the right one. A server whose tools are never called has a description
problem, not a code problem. See the `agent-evaluation` skill for the harness pattern.

## Publishing

- Package the entry point with a `bin` field and make it executable. A host launches the
  server by running `command args`, so the file must be runnable directly.
- Ship a `files` array so the published tarball contains the build output and nothing
  else.
- Document the exact launch command with an absolute path, and show both the POSIX and
  Windows JSON forms when clients differ. Clients that spawn a server need an absolute
  path because their working directory is not yours.
- Give the server a stable, human-meaningful name. Users see it in a permission prompt
  next to every action it can take.
- For a remote server, document the URL, the authorization flow, and a way to rotate
  credentials.

## Checklist

- Every tool description states what it does, when to use it, and what it returns.
- Every input schema uses enums for closed sets, bounds numbers, and sets
  `additionalProperties: false`.
- Tool names are `verb_noun` and are distinguishable from each other at a glance.
- Large results are summarised with an id and paged through a second tool.
- Paths resolve inside a configured root; symlink escapes are rejected.
- Tool failures return `isError` with an actionable message and no stack trace.
- Nothing writes to stdout in a stdio server, including dependencies.
- Remote servers use the protocol's OAuth flow, not a bespoke token scheme.
- No client-supplied token is forwarded downstream; effective scope comes from the token.
- Tests cover valid calls, invalid calls, and tool selection on realistic requests.

## Common mistakes

- **Client fails to start the server, JSON parse error.** Cause: a `console.log` or
  `print` reached stdout. Symptom appears on the first request. Fix: redirect all
  logging to stderr and re-test with production dependencies installed.
- **Model never calls the tool.** Cause: the description names a category instead of the
  capability, or a sibling tool overlaps it. Fix: rewrite around the words users type
  and add explicit boundaries.
- **Model calls the tool with the wrong arguments every time.** Cause: parameter names
  are abbreviated or the schema permits any shape. Fix: verbose names, enums, and
  `additionalProperties: false`.
- **Model loops calling a failing tool.** Cause: the error text does not say what to do
  differently. Fix: name the accepted values and the next action.
- **Works locally, hangs in CI.** Cause: the server resolves a relative path against the
  CI working directory, or waits on stdin forever. Fix: absolute paths, explicit
  shutdown, and a test that closes the client.
- **Remote server returns 401 after a token refresh.** Cause: your metadata documents and
  the authorization server disagree about the issuer or audience. Fix: follow the
  discovery documents the specification defines rather than hard-coding endpoints.

## Anti-patterns

- Exposing one operation as a tool, a resource, and a prompt. Pick one.
- A generic `run_command` or `execute_sql` tool that takes a raw string. Scoped tools
  beat a general one, both for model reliability and for authorization.
- Returning whole files or whole tables because truncation logic was never written.
- Trusting a model-supplied identity, tenant, or path.
- Forwarding the caller's bearer token to a downstream API.
- Treating "the model was instructed not to do that" as a control. Use a server-side
  check.
- Shipping a stdio server with no test that spawns the real process. The stdout bug only
  shows up in a real process.

## References

- MCP documentation index: https://modelcontextprotocol.io/llms.txt
- Build an MCP server: https://modelcontextprotocol.io/docs/develop/build-server
- Architecture and concepts: https://modelcontextprotocol.io/docs/learn/architecture
- MCP security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- TypeScript SDK: https://github.com/modelcontextprotocol/typescript-sdk
- MCP Inspector: https://github.com/modelcontextprotocol/inspector