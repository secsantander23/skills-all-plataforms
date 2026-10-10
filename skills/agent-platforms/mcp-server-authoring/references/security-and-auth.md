# Security for MCP servers

Read this before shipping a server that holds credentials or is reachable over a network.

Authoritative references:

- MCP security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- OWASP Top 10 for LLM Applications: https://genai.owasp.org/llm-top-10/
- OWASP LLM01 Prompt Injection: https://genai.owasp.org/llmrisk/llm01-prompt-injection/

## The threat model in one line

The model is an untrusted, persuasive, and occasionally compromised caller. Every tool
call is an attacker-supplied request that happens to have been phrased in English.

## Confused deputy

The server holds credentials. The model selects which credential to use and how. If the
model can choose the target of a privileged operation, the server has granted the model
an authority it should not have.

Mitigations:

- Never let a tool argument name a tenant, a user, a host, or a resource owner.
- Resolve all scope from the authenticated subject on the server.
- Narrow every credential to the smallest scope that works. Read-only tokens for read
  tools.

## Token passthrough

The classic anti-pattern:

```typescript
// Wrong. The caller's token is forwarded to a downstream API that never intended to
// trust your client, so your server relays whatever authority it was handed.
server.registerTool(
  "call_downstream",
  { inputSchema: z.object({ path: z.string() }) },
  async ({ path }, extra) => {
    const callerToken = extra.requestInfo?.headers?.authorization;
    return fetch(`https://downstream.example${path}`, {
      headers: { authorization: callerToken },
    });
  },
);
```

Right: exchange the token at your server and issue your own downstream credential, with
your own scope decision.

```typescript
async function downstreamCredential(subject: Subject): Promise<string> {
  // Subject comes from the verified token, never from tool arguments.
  const claims = await verifyAccessToken(subject.accessToken);
  return tokenBroker.issue({
    audience: "downstream.example",
    subject: claims.sub,
    scopes: scopeFor(claims, "read"),
  });
}
```

## Server-side request forgery

If a tool accepts a URL, the model can be induced to pass an attacker-controlled host.
Treat every model-supplied URL as hostile input.

- Prefer no URL parameter. Expose `list_docs`, `read_doc(doc_id)` instead of
  `fetch_url(url)`.
- When a URL is unavoidable, resolve it, check the scheme, and check the resolved IP
  against a block list for loopback, link-local, and private ranges. Re-check after every
  redirect.
- Never follow redirects blindly to a different origin.

## Prompt injection arriving through your server

Content you return reaches the model and can carry instructions. This is the indirect
injection path.

- Return data, not instructions. Strip or fence content that is explicitly marked as
  untrusted.
- Do not concatenate retrieved text into a role-tagged instruction block.
- Where a tool reads documents, keep them in a content block and describe the trust
  level in the tool description so the model treats them as reference material.

See the `agent-prompt-injection-defense` skill for the full defence pattern.

## Consent and least privilege

- Split read and write tools. Never bundle a delete with a read in one tool.
- Ask for human approval on the destructive subset. Hosts surface this per tool, so a
  separate tool name is what makes the approval prompt meaningful.
- Classify tools honestly. If a tool modifies state, do not describe it as read-only,
  even if it is usually used that way.
- Keep credentials in the server process environment or a secret manager, never in tool
  arguments, results, or descriptions.

## Output handling

Model and tool output crosses trust boundaries constantly. Before persisting or
rendering it:

- Validate structure against a schema. Do not concatenate into SQL, shell, or templates.
- Escape for the destination. HTML, Markdown, and terminal output all have their own
  injection problems.
- Strip control characters and ANSI escapes before logging.
- Cap sizes. A tool returning unbounded text can exhaust a host's context window or your
  own memory.

## Logging for investigation

Log every tool call with:

- timestamp and request id
- tool name
- authenticated subject, never a model-supplied identity
- argument shape with secrets redacted, not the raw arguments
- outcome and duration
- the error class on failure

Make the redaction explicit rather than incidental. A logger that dumps full arguments
will eventually write an access token to disk because a tool took one as input.

## Checklist for a remote server

- OAuth flow matches the MCP authorization specification, including discovery documents.
- Token audience is checked on every request.
- Each session gets its own state.
- No token passthrough.
- URL parameters are absent or allow-listed with redirect re-checking.
- Destructive tools are separate from read tools and require consent.
- Per-subject and per-token rate limits exist.
- Logs carry the subject and the tool name with redacted arguments.
- Session lifetime and idle reaping are implemented.