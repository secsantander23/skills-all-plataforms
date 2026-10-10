# Tool Design Reference

## Anatomy of a tool definition

```
name         search_orders            verb_phrase, snake_case, stable
description  what it does, when to use it, when not to,
             what it returns, and any precondition
inputSchema  JSON Schema, closed, narrow, described
outputSchema optional JSON Schema for structured results
annotations  optional hints; UNTRUSTED unless the server is trusted
```

Write the description as four sentences:

1. What it does, in one sentence.
2. When the model should call it. Include the trigger words users actually type.
3. When the model should **not** call it, and which tool to use instead.
4. What it returns and any precondition or authorization requirement.

## Schema patterns

Closed object, all required:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["start_date", "end_date"],
  "properties": {
    "start_date": {
      "type": "string",
      "description": "Inclusive start date, ISO 8601, YYYY-MM-DD."
    },
    "end_date": {
      "type": "string",
      "description": "Exclusive end date, ISO 8601, YYYY-MM-DD."
    }
  }
}
```

Enum instead of free string:

```json
{
  "type": "string",
  "enum": ["open", "pending", "shipped", "delivered", "cancelled"],
  "description": "Order status filter."
}
```

Optional value, explicit absence:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["reason", "note"],
  "properties": {
    "reason": { "type": "string", "enum": ["duplicate", "fraud", "customer_request"] },
    "note": {
      "type": ["string", "null"],
      "description": "Optional free text. Use null when there is no note."
    }
  }
}
```

Integer money, not float:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["amount_minor", "currency"],
  "properties": {
    "amount_minor": {
      "type": "integer",
      "description": "Amount in the currency's smallest unit. 4999 means 49.99."
    },
    "currency": { "type": "string", "enum": ["USD", "EUR", "GBP", "BRL", "JPY"] }
  }
}
```

## Tool count and progressive disclosure

Models choose worse as the tool list grows and as descriptions overlap. When you have more than
roughly a dozen tools, or when tool selection accuracy drops:

- Merge tools that differ only by a filter argument.
- Split tools by domain and use a router agent so only one domain's tools are ever in context.
- Use search-style discovery where one tool returns the list of relevant tool ids, then load
  their definitions on demand. Anthropic exposes a tool search and deferred tool loading on some
  models; check current support.
- Split by permission tier so a low-privilege session never sees the destructive tools at all.

## Error results the model can act on

```json
{
  "error": "order 88213 was not found for tenant acme",
  "retryable": false,
  "hint": "Call list_orders to obtain a valid order id for this tenant."
}
```

Rules:

- No stack traces. They waste tokens and leak internals.
- Say what was wrong and what to try next.
- Mark transient failures so the model can retry once instead of giving up.
- Keep error text short. Cap it in code before returning.

## Result payload design

```python
def compact_result(rows: list[dict], limit: int = 20) -> str:
    """Return a small preview plus a handle the agent can page through."""
    preview = rows[:limit]
    return json.dumps(
        {
            "returned": len(preview),
            "total_matched": len(rows),
            "truncated": len(rows) > limit,
            "next_offset": limit if len(rows) > limit else None,
            "rows": preview,
        }
    )
```

A tool that returns a 4000-token dump will dominate the context window and degrade every later
step. Return a summary plus a query handle.

## Dangerous tools

Split into two tiers:

| Tier | Examples | Gate |
|---|---|---|
| Read | list, get, search, export | Direct call after authorization |
| Write, reversible | draft, tag, comment | Direct call, logged |
| Write, irreversible | delete, refund, send, deploy | Out-of-band confirmation, never model-initiated alone |

The model can propose an irreversible action. A human-approved, deterministic code path executes
it. Do not rely on a prompt instruction to gate the destructive case.