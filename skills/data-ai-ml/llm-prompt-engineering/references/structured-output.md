# Structured Output Deep Dive

## Why not "return JSON"

Prose-prompted JSON fails in characteristic ways:

- Trailing commentary after the object.
- Single quotes, trailing commas, unquoted keys.
- Enum values invented to fit the question rather than the allowed set.
- Fields omitted when the model is unsure.

Constrained decoding removes the syntax class of failure. It does not remove the semantic ones.

## Schema design rules

1. **Flat beats nested.** Deeply nested objects increase refusal and truncation rates.
2. **Use enums, not free strings,** for anything with a known label space. Enums shrink the
   output space and make downstream validation trivial.
3. **Make every property required** when using strict schema adherence, and use a nullable type
   or an explicit `null` enum member if a value can be absent. Optional fields are a source of
   `KeyError` downstream.
4. **Disallow extra properties** so the model cannot add fields you never handle.
5. **Describe fields in the description text.** The model reads them. "One sentence, present
   tense, no ticket id" materially changes output.
6. **Encode constraints in the schema**, not only in prose: `minimum`, `maximum`, `enum`,
   `pattern` where supported.
7. **Cap string length expectations in prose** if the schema cannot express it.

A good schema:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["category", "priority", "summary", "needs_human"],
  "properties": {
    "category": {
      "type": "string",
      "enum": ["billing", "bug", "account", "other"],
      "description": "Single best fit for routing."
    },
    "priority": {
      "type": "integer",
      "minimum": 1,
      "maximum": 4,
      "description": "1 is lowest, 4 is highest."
    },
    "summary": {
      "type": "string",
      "description": "One sentence, present tense, no ticket id, no customer name."
    },
    "needs_human": {
      "type": "boolean",
      "description": "True when the request is ambiguous or high impact."
    }
  }
}
```

## Validate anyway

```python
import json

from jsonschema import Draft202012Validator

validator = Draft202012Validator(TICKET_SCHEMA)


def parse_ticket(raw: str) -> dict:
    data = json.loads(raw)  # raises on malformed JSON
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        raise ValueError("; ".join(e.message for e in errors))
    return data
```

Then decide what to do on failure. Two sane options:

- **Retry once** with the validation error appended as a new user turn. Cheap, sometimes works.
- **Fall back** to a rule-based or human path. Correct for high-stakes flows.

Never loop more than one retry. Repeated failure means the schema and the task do not match.

## Streaming structured output

Partial JSON streams are common: you get fragments that are not valid JSON until concatenated.
Buffer, then parse once at the end. If you need incremental parsing, use a real incremental JSON
parser and expect partial states to be incomplete, not merely invalid.

## Tool calls as structured output

When a model supports tool calls with strict schemas, a single-purpose tool call is often a more
portable way to get a typed result than the provider's structured output mode: it works across
more models, and the call itself is logged and auditable. It also costs a round trip in some
architectures. Pick per provider capability and re-check the current docs.