# Provider API Field Map

Quick reference for the fields this skill relies on. **Verify against the official reference
page before shipping** — field names, enum values, and model support change.

Provider references:

- OpenAI: https://platform.openai.com/docs/api-reference/chat/create
- Anthropic: https://docs.anthropic.com/en/api/messages
- Gemini: https://ai.google.dev/api/generate-content

## Request shape

| Concept | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Model | `model` | `model` | `model` (SDK) / `models/{model}` in URL (REST) |
| Conversation | `messages[]` | `messages[]` | `contents[]` |
| System instructions | message with `role: "system"` or `"developer"` | top-level `system` (string or content blocks) | top-level `systemInstruction` |
| Tool definitions | `tools[]` with `type: "function"` | `tools[]` | `tools[]` |
| Tool choice | `tool_choice` | `tool_choice` | `toolConfig` |
| Tool result | message with `role: "tool"` and `tool_call_id` | `tool_result` block inside a user message | `functionResponse` part |
| Structured output | `response_format` | `output_config.format` | `generationConfig.responseMimeType` + `responseSchema` |
| Reasoning control | `reasoning_effort` | `thinking` | thinking configuration |
| Caching control | prefix based, plus explicit breakpoints on newer models | `cache_control` on blocks | explicit cached content by name |

## Sampling and output limits

| Concept | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| Temperature | `temperature`, 0 to 2 | `temperature` | `generationConfig.temperature` |
| Nucleus | `top_p` | `top_p` | `generationConfig.topP` |
| Top-k | not exposed on Chat Completions | `top_k` | `generationConfig.topK` |
| Output cap | `max_completion_tokens` (`max_tokens` is deprecated) | `max_tokens`, required | `generationConfig.maxOutputTokens` |
| Stop strings | `stop` (string or array) | `stop_sequences` (array) | `generationConfig.stopSequences` |
| Seed | `seed`, best effort, beta | not exposed | available on some Vertex paths |

Not every model accepts every knob. Reasoning models in particular restrict sampling controls.
Check the provider's reasoning or thinking guide for the specific model before you send these
fields.

## Structured output differences that bite

- OpenAI: `response_format` has three shapes — `text`, `json_object`, `json_schema`. Under
  `strict`, only a subset of JSON Schema is supported. `json_object` mode will not produce JSON
  at all unless a message tells it to.
- Anthropic: structured output via `output_config` is newer and may require a beta header or a
  model that supports it. The `schema` field is a JSON Schema object.
- Gemini: `responseMimeType: "application/json"` plus `responseSchema` in
  `generationConfig`. The SDKs accept Pydantic models or Zod schemas and convert them.

In all three, constrained decoding fixes the syntax of the output. It does not guarantee the
meaning. Validate after decoding.

## Response fields worth logging

| Field | Where | Why |
|---|---|---|
| Stop reason | `choices[0].finish_reason` (OpenAI), `stop_reason` (Anthropic), `finishReason` on a candidate (Gemini) | Tells you truncation vs natural stop vs tool use |
| Token usage | `usage.prompt_tokens` / `usage.completion_tokens` (OpenAI), `usage.input_tokens` / `usage.output_tokens` (Anthropic), `usageMetadata` (Gemini) | Cost and latency analysis |
| Model served | `model` in the response | Confirm routing and aliases |
| Cache metrics | provider specific usage fields | Verify the caching actually worked |
| Backend fingerprint | `system_fingerprint` on OpenAI chat completions | Detect backend changes when you use `seed` |

Always log the stop reason. A truncated answer that is silently accepted downstream is one of the
most common production failures in LLM features.