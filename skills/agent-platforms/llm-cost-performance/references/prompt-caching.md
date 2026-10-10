# Prompt caching in practice

Caching is a prefix cache. Everything below follows from that one fact.

Verify the exact semantics, minimum prefix length, TTL, and pricing multipliers with your
provider. They differ, and they change:

- Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI prompt caching: https://platform.openai.com/docs/guides/prompt-caching

## What a prefix means

A request is tokenised into a sequence. The provider can serve the longest prefix of that
sequence that matches something it already stored. Consequences:

| Observation | Mechanism |
| --- | --- |
| Changing the last few characters keeps the cache | Prefix ends where your varying content starts |
| Changing one character near the top invalidates everything | The whole prefix differs |
| Reordering a list invalidates the cache | Prefix is order-sensitive |
| A longer prompt that starts the same still hits | Extra tokens are processed after the hit |

## Layout that caches well

```text
[ system instructions        ]  stable   <- cache boundary starts here
[ tool definitions           ]  stable
[ large static reference doc ]  stable
-------------------------------  cache prefix ends
[ retrieved context          ]  varies   <- small and cheap
[ user question              ]  varies
```

## Layout that never caches

```text
[ timestamp: 2026-01-01T00:00:00Z ]  varies
[ session id abc123                 ]  varies
[ conversation history              ]  varies
[ system instructions               ]  stable, but unreachable behind the prefix
```

## Auditing an existing prompt

Grep the codebase for these near the top of any prompt template:

```bash
rg -n "new Date|Date\.now|toISOString|randomUUID|requestId|sessionId|userId" --glob '**/prompts/**'
rg -n "JSON\.stringify\(.*tools|tools\.map" --glob '**/prompts/**'
rg -n "Object\.(keys|entries)\(" --glob '**/prompts/**'
```

The third pattern matters: serialising an object whose key order is insertion-ordered
produces a different string when the object is built by a different code path. Sort
before you serialise.

```typescript
// Unstable if the source object was built in a different order.
const toolDefs = JSON.stringify(TOOLS);

// Stable.
const toolDefs = JSON.stringify(
  [...TOOLS].sort((a, b) => a.name.localeCompare(b.name)),
);
```

## Measuring

Log three numbers per request: cache read tokens, cache write tokens, and total input
tokens.

```typescript
interface CacheStats {
  reads: number;
  writes: number;
  plain: number;
  hitRate: number;
}

export function cacheStats(
  samples: ReadonlyArray<{ inputTokens: number; cacheRead: number; cacheWrite: number }>,
): CacheStats {
  let reads = 0;
  let writes = 0;
  let plain = 0;
  for (const s of samples) {
    reads += s.cacheRead;
    writes += s.cacheWrite;
    // "plain" is what a provider reports for input not covered by either field.
    plain += Math.max(0, s.inputTokens - s.cacheRead);
  }
  const total = reads + writes + plain;
  return {
    reads,
    writes,
    plain,
    hitRate: total === 0 ? 0 : reads / total,
  };
}
```

Interpretation:

- `hitRate` near zero: the prefix is not stable, or the prompt is below the minimum
  cacheable length. Do not keep the caching path.
- `writes` high and `reads` low: you are paying the write premium to fill a cache that
  is not being reused, usually because the traffic is not repeating.
- `reads` high and `writes` low: working.

## Minimum cacheable prefix

Providers publish a minimum number of tokens before a prefix can be cached, and it
changes. Do not design a system that depends on caching a 500-token prompt. Verify the
current minimum with the provider before relying on it.

## TTL

Cached prefixes expire. For a workload with a burst every few hours, most requests after
the first will miss. If a workload is cacheable in principle but sees low hit rate, check
the request interval against the TTL before restructuring the prompt.

## Explicit cache control

Some providers let you mark a prefix explicitly rather than relying on automatic
matching. This is a per-provider API and it changes. Check the provider's documentation
for the current form; do not assume one provider's control block works on another.

## What not to cache

- Anything that must not persist for the TTL window, including personal data. Treat a
  cached prefix as stored data with a retention policy.
- Content that changes per request above a stable header you control. Confirm the
  boundary empirically by logging the first divergent token index when a miss occurs, if
  the provider exposes that.
- Content whose reuse you would have to explain to a data-protection reviewer.