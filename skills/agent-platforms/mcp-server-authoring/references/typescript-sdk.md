# TypeScript SDK shapes and protocol details

Verify import paths against the installed SDK version. The SDK has changed its export
map across major releases; the object shapes below are the stable part.

Authoritative references:

- https://modelcontextprotocol.io/docs/develop/build-server
- https://github.com/modelcontextprotocol/typescript-sdk

## Package layout

The long-standing TypeScript package is `@modelcontextprotocol/sdk`, with subpath
exports for the server, client, transports, and types. The official quickstart page has
also shipped a shorter package name in the past. Check the SDK repository README for the
current layout before pinning an import path in code you intend to maintain.

Common subpaths in the widely used layout:

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
```

If those specifiers do not resolve, read the package's own README rather than guessing
at a replacement.

## Server construction

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer(
  { name: "issue-tracker", version: "1.0.0" },
  {
    capabilities: {
      tools: { listChanged: true },
      resources: {},
      prompts: {},
    },
    instructions:
      "Prefer search_issues before update_issue. Never call delete_project; it is destructive.",
  },
);
```

The `instructions` field is server-level guidance the host may pass to the model. Treat
it as a hint about capability, not as a security control.

## Registering a tool

Two APIs exist for the same purpose. Prefer the single-object form because it keeps the
description, schema, and handler adjacent.

```typescript
server.registerTool(
  "update_issue",
  {
    title: "Update issue",
    description:
      "Change the status, assignee, or labels of an existing issue. Does not delete issues and does not change the title. Returns the updated issue.",
    inputSchema: z.object({
      id: z.string().regex(/^[A-Z]+-\d+$/).describe("Issue key such as ENG-142"),
      status: z.enum(["open", "in_review", "closed"]).optional(),
      assignee: z.string().optional(),
    }),
    annotations: { readOnlyHint: false, destructiveHint: false },
  },
  async ({ id, status, assignee }, extra) => {
    const subject = await authenticate(extra); // never trust a model-supplied user
    await assertCanEdit(subject, id);
    return toolResult(await tracker.update(id, { status, assignee, by: subject }));
  },
);
```

Behaviour worth knowing:

- `inputSchema` given as a Zod shape or object generates JSON Schema for the wire
  format. Use `.strict()` so unknown keys are rejected rather than dropped.
- Returning `structuredContent` requires declaring `outputSchema`; hosts validate it.
- `extra` carries request context such as the abort signal. Thread it into downstream
  calls so a cancelled request stops work.
- Annotations are advisory hints. Enforce read-only and destructive classification in
  code, not by annotation alone.

## Registering a resource

```typescript
server.registerResource(
  "runbook",
  "https://internal.example/runbook/deploy",
  { description: "Deployment runbook", mimeType: "text/markdown" },
  async (uri) => ({
    contents: [{ uri: uri.href, mimeType: "text/markdown", text: await loadRunbook() }],
  }),
);
```

Subscribe form for servers that push updates:

```typescript
server.registerResource(
  "deploy-log",
  "https://internal.example/deploy-log",
  { description: "Current deployment log", mimeType: "text/plain" },
  async (uri) => ({ contents: [{ uri: uri.href, text: await tailLog() }] }),
  {
    subscribe: true,
    listChanged: true,
  },
);
```

The subscribe handler receives a callback for pushing new contents.

## Registering a prompt

```typescript
import { z } from "zod";

server.registerPrompt(
  {
    name: "review_diff",
    description: "Review a unified diff for correctness, security, and test coverage.",
    argsSchema: {
      diff: z.string().describe("Unified diff text"),
      focus: z.string().optional().describe("Optional area to emphasise"),
    },
  },
  ({ diff, focus }) => ({
    messages: [
      {
        role: "user",
        content: {
          type: "text",
          text: focus
            ? `Review this diff, focusing on ${focus}.\n\n${diff}`
            : `Review this diff.\n\n${diff}`,
        },
      },
    ],
  }),
);
```

## Transport: stdio

```typescript
const transport = new StdioServerTransport();
await server.connect(transport);
```

Nothing else may write to stdout. Configure any logging framework in the app to stderr:

```typescript
import { Console } from "node:console";

const logger = new Console({ stdout: process.stderr, stderr: process.stderr });
logger.error("server started");
```

## Transport: Streamable HTTP

The Streamable HTTP transport uses a single endpoint that the client POSTs to. The
server may respond with an event stream to deliver server-initiated messages.

```typescript
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";

const app = express();
app.use(express.json());

app.post("/mcp", async (req, res) => {
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => crypto.randomUUID(),
    onsessioninitialized: (id) => sessions.set(id, { createdAt: Date.now() }),
  });

  transport.onclose = () => {
    if (transport.sessionId) sessions.delete(transport.sessionId);
  };

  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

app.listen(8080);
```

Deployment rules that follow from this shape:

- Authenticate before `handleRequest`, and derive the subject from the verified token.
- Create a server instance per session when any session holds mutable state. Reusing one
  server object across sessions leaks state between users.
- Enforce a session lifetime and reap idle sessions.
- Streamable HTTP is stateless-friendly: a load balancer can route any request to any
  instance as long as session state is externalised. Stateless per-request handling is
  possible for servers with no cross-request state, and is the easiest way to scale.

Verify the exact constructor options and stateless mode flags against the installed
SDK; this area changes between releases.

## Testing with an in-memory transport

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

export async function connectClient(server: McpServer) {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test-client", version: "0.0.0" });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  return client;
}
```

In-memory tests skip process spawning, so they will not catch a stdout contamination bug.
Keep at least one test that spawns the built binary as a subprocess.

## JSON-RPC envelope

Every message on the wire is a JSON-RPC 2.0 object.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": { "name": "search_issues", "arguments": { "query": "flaky test" } }
}
```

Success response:

```json
{ "jsonrpc": "2.0", "id": 7, "result": { "content": [{ "type": "text", "text": "{}" }] } }
```

Error response, for protocol-level failures only:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": { "code": -32602, "message": "Unknown tool: search_issues" }
}
```

Notifications carry no `id` and expect no response. Progress notifications and
cancellation are both notifications.

## Content types

Tool results are a list of content blocks:

| Type | Purpose |
| --- | --- |
| `text` | Plain text |
| `image` | Base64-encoded image with a declared MIME type |
| `audio` | Base64-encoded audio with a declared MIME type |
| `resource` | An embedded resource with its own URI and MIME type |
| `resource_link` | A pointer to a resource the client can read separately |

Prefer `resource_link` when the payload is large: the client can decide whether to read
it rather than forcing the bytes into the model context.