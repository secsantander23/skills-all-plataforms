# MCP Integration Notes

Everything here is from the Model Context Protocol specification. The specification is dated
and revised, so check the current version page before depending on a field.

- Specification index: https://modelcontextprotocol.io/specification/latest
- Documentation index for tooling: https://modelcontextprotocol.io/llms.txt

## Primitives

| Primitive | Control | What it is |
|---|---|---|
| Tools | Model | Callable actions described by a JSON Schema `inputSchema` |
| Resources | Application | Readable context addressed by URI, optionally subscribable |
| Prompts | User | Reusable templates the user invokes explicitly |

## Lifecycle

1. Client sends `initialize` with `protocolVersion`, `capabilities`, `clientInfo`.
2. Server replies with its `protocolVersion`, `capabilities`, `serverInfo`, and optional
   `instructions`.
3. Client sends `notifications/initialized`.
4. Operation phase. Both sides use only negotiated capabilities.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": { "roots": { "listChanged": true } },
    "clientInfo": { "name": "MyApp", "version": "1.0.0" }
  }
}
```

Version negotiation is a MUST-level rule in the specification: if the server supports the
requested version it returns the same version, otherwise it returns one it does support, and
the client should disconnect if it cannot speak that version. Over HTTP, send the
`MCP-Protocol-Version` header on subsequent requests.

Timeouts are a SHOULD in the specification, with cancellation notifications for in-flight
requests. Implement them even if a server does not honor them.

## Capabilities worth knowing

Client side: `roots` (filesystem boundaries the client exposes), `sampling` (the server may ask
the client to run an LLM call), `elicitation` (the server may ask the user for input).

Server side: `tools`, `resources`, `prompts`, `logging`, `completions`. `listChanged` on
tools, resources, or prompts tells the client to re-list when the set changes;
`notifications/tools/list_changed` is the notification for that.

Roots are a real security boundary. Expose the narrowest set that lets the server work.

## Tool messages

Listing supports pagination via a cursor and returns `nextCursor`.

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

Result:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{ "type": "text", "text": "Current weather in New York:\nTemperature: 72F" }],
    "isError": false
  }
}
```

Content block types include `text`, `image`, `audio`, `resource_link`, and embedded `resource`.
If a tool declares `outputSchema`, the server must return `structuredContent` conforming to it,
and for backwards compatibility should also serialize it into a text block. Note the
specification is explicit that `structuredContent` is unrelated to model-side structured outputs.

## Two error channels

| Channel | Mechanism | Use for |
|---|---|---|
| Protocol error | JSON-RPC `error` object | Unknown tool, invalid arguments, server failure |
| Tool execution error | Result with `isError: true` | Business errors, API failures, bad input data |

The model should see the execution error as text so it can adapt, and your code should see the
protocol error as an exception so it can alert.

## Security requirements from the spec

Servers must validate inputs, enforce access control, rate limit invocations, and sanitize
outputs. Clients should confirm sensitive operations with the user, show tool inputs before
calling, validate results before passing them to the model, implement timeouts, and log usage.

The spec also states clearly that clients must treat tool annotations as untrusted unless the
server is trusted. An annotation is a hint for display and for your own decision-making, never
a control input.

## Building an MCP server

- Pick the transport the deployment needs: stdio for a local child process, HTTP for a remote
  service. Read the current transports page for the exact HTTP definition and its auth model.
- Validate every argument against the declared `inputSchema` before doing anything.
- Keep tool side effects small and idempotent where you can.
- Return errors through the execution-error channel so the client can recover.
- Emit `notifications/tools/list_changed` when the tool set changes at runtime.

## Consuming an MCP server from an agent

```text
connect -> initialize -> notifications/initialized
       -> tools/list
       -> filter or route the tool set by session privilege
       -> expose as agent tools
       -> on each call: validate args, call tools/call, validate result, feed back
```

Cache the tool list per connection and refresh on `list_changed`. If your provider has a tool
limit, partition the set per session rather than truncating arbitrarily.