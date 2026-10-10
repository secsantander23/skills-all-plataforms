---
name: Content Negotiation and Wire Formats
description: Covers HTTP content negotiation headers (Accept, Accept-Encoding, Accept-Language, Accept-Charset), quality values, media types and parameters, structured syntax suffixes, multipart bodies, and the tradeoffs between JSON, YAML, XML, CBOR, MessagePack, and Protocol Buffers plus gzip, brotli, and zstd compression. Use when the user asks how to negotiate a response format, why 406 happens, how Vary affects caching, which serialization to pick, or when designing a public or internal API payload format.
metadata:
  opencode/autoinvoke: true
---

# Content Negotiation and Wire Formats

HTTP lets a client state what it can accept and lets a server choose. The negotiation half of this skill is about doing that predictably; the format half is about what you put inside the chosen media type once the bytes exist.

## When to use this

- A client receives `406 Not Acceptable` and nobody can say why.
- You are deciding whether to serve `application/json`, `application/problem+json`, YAML, CSV, or protobuf for a resource.
- You added `Vary` or forgot to, and caching broke in a way that is hard to reproduce.
- You are choosing between gzip, brotli, and zstd, or deciding whether compression is worth it for your payload shape.
- You are implementing `multipart/form-data` uploads or `multipart/mixed` responses.
- You need to reason about `q` values, wildcards, and which representation wins when preferences tie.

## Quick start

A server that negotiates properly sends the client's preferences and echoes what it chose.

```text
GET /reports/2026/q1 HTTP/1.1
Host: api.example.com
Accept: application/json;q=0.9, application/problem+json, */*;q=0.1
Accept-Encoding: br, gzip;q=0.9, zstd;q=0.8
Accept-Language: pt-BR, en;q=0.8

HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
Content-Encoding: br
Vary: Accept, Accept-Encoding, Accept-Language
Content-Length: 1840
```

```bash
# See exactly which representation the server picks for a given preference.
curl -sD - -o /dev/null -H 'Accept: application/xml' https://api.example.com/reports/2026/q1
curl -sD - -o /dev/null -H 'Accept-Encoding: br' --compressed https://api.example.com/reports/2026/q1

# Inspect what the server says it supports via OPTIONS.
curl -sX OPTIONS -D - -o /dev/null https://api.example.com/reports/2026/q1
```

## The four negotiation headers

All four share the same shape: a comma-separated list of ranges, each optionally carrying a `q` weight. RFC 9110 Section 12.4 defines the common rules.

| Request header | Section | Server answer | Controls |
| --- | --- | --- | --- |
| `Accept` | RFC 9110 Section 12.5.1 | `Content-Type`, or `406` | Representation media type |
| `Accept-Encoding` | RFC 9110 Section 12.5.3 | `Content-Encoding`, or `406` | Content coding applied to the body |
| `Accept-Language` | RFC 9110 Section 12.5.4 | `Content-Language` | Natural language |
| `Accept-Charset` | RFC 9110 Section 12.5.2 | `charset` parameter | Character set. Deprecated in practice: `charset` belongs in `Content-Type` |

```abnf
Accept       = #( media-range [ weight ] )
media-range  = ( "*/*" / ( type "/" "*" ) / ( type "/" subtype ) ) parameters
weight       = OWS ";" OWS "q=" qvalue
qvalue       = ( "0" [ "." 0*3DIGIT ] ) / ( "1" [ "." 0*3("0") ] )
```

Quality values run from `0` (unacceptable) to `1` (best) with at most three decimal digits. `q=0` is a hard rejection, not a low preference. This distinction matters constantly: `Accept-Encoding: gzip;q=0` tells the server that sending gzip is worse than sending nothing, because the client cannot decode it.

Specificity beats quality when resolving the match. `text/*;q=0.5, text/html;q=0.8` means plain `text/html` is preferred over any other `text/*`. RFC 9110 Section 12.4.2 gives the exact precedence: the most specific matching range determines the precedence for the media type, and the highest `q` among the most specific matches wins.

Ties are resolved by server order of preference. Deterministic tie-breaking matters: if you rank two equal-quality representations by an unstable sort or by hash-map iteration order, two requests with identical headers can receive different representations, which breaks caching and reproducibility. Sort deterministically on an explicit ranking table.

Wildcards compose as expected: `*/*` accepts anything, `application/*` accepts any application subtype, and `application/*+json` accepts structured suffixes whose subtype ends in `+json`.

## Media types and parameters

```abnf
media-type    = type "/" subtype parameters
type          = token
subtype       = token
parameters    = *( OWS ";" OWS [ parameter ] )
parameter     = parameter-name "=" parameter-value
parameter-name = token
```

```text
Content-Type: application/json; charset=utf-8
Content-Type: multipart/form-data; boundary=------------------------d74496d66958873e
Content-Type: application/vnd.example.v2+json; profile="https://example.com/profiles/v2"
```

Parameter names are case-insensitive. Parameter *values* may be case-sensitive depending on the parameter, so do not assume. `charset` is defined as case-insensitive by RFC 9110 Section 8.3.2; many parsers nonetheless compare it exactly, so send lowercase.

### Structured syntax suffixes

RFC 6839 defines the `+suffix` convention: a media type whose subtype ends in `+json` shares the JSON syntax. This lets a profile add meaning without a new top-level type.

```text
application/problem+json      RFC 9457, problem details
application/ld+json           JSON-LD, W3C
application/merge-patch+json  RFC 7396, JSON Merge Patch
application/json-patch+json   RFC 6902, JSON Patch
application/cbor              RFC 8949, CBOR
application/grpc+proto        gRPC
```

Benefits: clients can dispatch on the suffix (`+json`) while still dispatching on the exact subtype. Costs: any resolver or content-type switch that only matches exact types will silently miss `application/problem+json`. Two rules keep this honest: match on the suffix before matching on the exact type, and register the profile publicly if you invent a new one.

### 406 is a contract decision

`406 Not Acceptable` (RFC 9110 Section 15.5.7) means no representation satisfies `Accept`. Sending it for an unroutable edge case breaks clients that never sent a strict `Accept` in the first place. A robust rule:

- No `Accept` header at all, or `Accept: */*`: serve your default.
- `Accept` present but unmatched, and the resource exists: `406` is defensible.
- `Accept: */*;q=0`: technically nothing is acceptable, but this is almost always a client bug. Log it and serve your default rather than breaking the request.

Always include a default in your own client, or an intermediary that strips or rewrites `Accept` will break you.

## Vary and the cache key

`Vary` (RFC 9111 Section 4.1) tells caches which request headers influenced the response. Getting it wrong produces the two classic bugs: serving the wrong variant, or multiplying cache entries by every dimension you failed to list.

```text
Vary: Accept, Accept-Encoding
```

Rules that prevent both failures:

- Vary on exactly the request headers your handler actually reads. Varying on a header you ignore destroys hit ratio; failing to vary on one you read serves wrong content.
- `Vary: *` means the response is not cacheable by a shared cache. Use it deliberately, not as a shortcut.
- `Accept-Encoding` is the one most often forgotten. Without it, a gzip response can be stored and replayed to a client that cannot decode it.
- Because `Vary` multiplies the cache key, high-cardinality values such as `Accept-Language` explode entry counts. Where possible, normalise the header before matching (map `en-GB` and `en` to one internal variant) and emit a `Vary` that reflects the original header anyway.
- Add `Cache-Control` and validators explicitly. `ETag` must vary with the representation: an ETag for the gzip form must differ from the identity form, and RFC 9110 Section 8.8.3.3 shows how to derive a distinct strong ETag when one representation is produced from another.

## `Content-Encoding` versus `Transfer-Encoding`

These are different layers and mixing them up causes real bugs.

- `Content-Encoding` (RFC 9110 Section 8.4) is a property of the *representation*. `Content-Encoding: gzip` means this resource exists in compressed form and the stored form is the compressed one. It is cacheable and should be paired with `Vary: Accept-Encoding`.
- `Transfer-Encoding` (RFC 9112 Section 6.1) is a property of the *message in transit*. `chunked` exists only to delimit a body of unknown length. A proxy is expected to remove it before caching.

Compressing with `Content-Encoding: gzip` and then also chunking produces two independent layers. Choose the one you actually want: compress first (so the compressed representation is cacheable), then chunk if the length is unknown.

Compress-then-encrypt is the correct order, and TLS sits above both, so there is no confidentiality benefit to compressing.

## Choosing a wire format

| Format | Compactness | Parse cost | Schema evolution | Readability | Notes |
| --- | --- | --- | --- | --- | --- |
| JSON (RFC 8259) | Medium | Low | Additive by convention, unenforced | High | The default for public HTTP APIs |
| YAML 1.2 | Low | Higher | Poor error messages in edge cases | High for humans, awkward for diffs | Comments and anchors; the "Norway problem" (`no` as boolean) is a YAML 1.1 artefact that 1.2 removed |
| XML (XML 1.0 5th ed.) | Low | Highest | Namespaces and versioning | Medium | Still the lingua franca for documents, publishing, and government integrations |
| CBOR (RFC 8949) | High | Low | Tag-based, self-describing with tag 55799 | Low | Designed alongside JSON; deterministic encoding exists for hashing |
| MessagePack | High | Low | Less specified than protobuf | Low | Simple, small spec |
| Protocol Buffers | Highest | Low once schema-loaded | Excellent: field numbers, unknown-field skipping | None | Requires a schema and code generation |

```json
{ "id": 42, "name": "widget", "createdAt": "2026-01-31T09:00:00Z", "tags": ["a", "b"] }
```

```yaml
id: 42
name: widget
createdAt: 2026-01-31T09:00:00Z
tags:
  - a
  - b
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<widget id="42">
  <name>widget</name>
  <createdAt>2026-01-31T09:00:00Z</createdAt>
  <tags><tag>a</tag><tag>b</tag></tags>
</widget>
```

The one-line JSON and this CBOR encode the same value. Byte layout follows RFC 8949 Section 3 major types: `0xbf` is an indefinite-length map of five pairs, `0x69` is a text string of length 9, `0x18` is a one-byte unsigned integer, `0xc0` is tag 0 (standard date/time string), `0x74` is a text string of length 20, and `0x82` is an array of two elements.

```text
bf                                            # indefinite-length map, 5 pairs
   69 63 72 65 61 74 65 64 41 74            # "createdAt" (text, len 9)
   c0                                        # tag 0: standard date/time string
   74 32 30 32 36 2d 30 31 2d 33 31 54 30
      39 3a 30 30 3a 30 30 5a               #   "2026-01-31T09:00:00Z" (text, len 20)
   62 69 64                                  # "id" (text, len 2)
   18 2a                                     #   42 (positive integer, 1 byte)
   64 6e 61 6d 65                            # "name" (text, len 4)
   c0                                        # tag 0: standard date/time string
   66 77 69 64 67 65 74                     #   "widget" (text, len 6)
   64 74 61 67 73                            # "tags" (text, len 4)
   82                                        #   array, 2 items
      61 61                                   #     "a" (text, len 1)
      61 62                                   #     "b" (text, len 1)
```

Two properties this demonstrates. The map keys appear in code-point order (`createdAt`, `id`, `name`, `tags`), which is what deterministic encoding requires when the bytes will be hashed or signed. And `createdAt` is encoded with tag 0 rather than as a bare string, which is the CBOR-native way to mark a value as a timestamp. Generate these bytes with a real encoder and verify against your own decoder rather than transcribing hex by hand.

Decision rules:

- **Public API, mixed clients, debugging needed:** JSON. Nothing else is worth the friction.
- **Internal RPC between services you control and can deploy code generators for:** protobuf or one of the binary self-describing formats. The win is bandwidth and CPU, which you should measure in your own workload rather than assume.
- **Configuration files, human-edited inputs:** YAML, with a strict parser and no implicit typing surprises.
- **Documents, transformations, XPath/XSLT, regulated interop:** XML.
- **Signed or hashed payloads:** use a format with a canonical/deterministic encoding (CBOR has one; JSON needs an explicit canonicalisation scheme). Naive serialisation order differences will break signatures.

### Schema evolution rules that actually hold

- **JSON:** add fields freely; never change the meaning of an existing field; never repurpose a removed field name; accept unknown fields and ignore them.
- **Protobuf:** field numbers are the contract. A field number is never reused. Unknown fields are preserved by most implementations and skipped by others, which is what makes rolling deploys work. Renaming a field is wire-compatible; changing its type is not, except among the numeric family within the same wire type.
- **XML:** add namespaced elements; never change an element's type; namespace changes break naive consumers.

## Compression

| Coding | Format spec | Notes |
| --- | --- | --- |
| `gzip` | RFC 1952 (container), DEFLATE in RFC 1951 | Universal support; still the safe fallback |
| `br` (Brotli) | RFC 7932 | Higher compression ratios at comparable speeds on text; requires client support |
| `zstd` | RFC 8878 | Very fast at low ratios and fast at high ratios; requires client support |

Rules that hold regardless of algorithm:

- Always offer `identity` implicitly. RFC 9110 Section 8.4.1 makes `identity` the default coding.
- Compress only above a size threshold. Small responses often grow after compression because of framing overhead.
- Compress structured data better than already-compressed data. JSON compresses well; JPEG, PNG, MP4, and WOFF2 do not. Compressing them wastes CPU and can even enlarge them.
- Do not compress tiny or already-compressed payloads, and do not compress a response that will be re-compressed by a proxy.
- Measure. Compression ratio and CPU cost depend on your payload, your CPU, and your network. Do not quote a percentage you did not measure.

```bash
# Compare codings on a real endpoint and print the chosen coding and size.
for enc in gzip br zstd; do
  curl -s -o /dev/null -D - -H "Accept-Encoding: $enc" https://api.example.com/big.json \
    | awk -v e="$enc" 'BEGIN{IGNORECASE=1} /^content-encoding|^content-length/{print e": "$0}'
done
```

## Multipart

`multipart/*` types combine several body parts, each with its own headers, separated by a boundary string chosen by the sender and declared in the top-level `Content-Type` (RFC 9110 Section 8.3.3, RFC 2046).

```text
POST /upload HTTP/1.1
Host: api.example.com
Content-Type: multipart/form-data; boundary=X-BOUNDARY
Content-Length: 341

--X-BOUNDARY
Content-Disposition: form-data; name="title"

Quarterly report
--X-BOUNDARY
Content-Disposition: form-data; name="attachment"; filename="q1.pdf"
Content-Type: application/pdf

%PDF-1.7
...
--X-BOUNDARY--
```

- `multipart/form-data` is defined by RFC 7578, which obsoleted RFC 2388. Fields carry `Content-Disposition: form-data` with a `name` parameter, plus `filename` for files (RFC 2183 for the disposition header in general).
- Every part must have its own `Content-Type` or it defaults to `text/plain` per RFC 2046. State it explicitly.
- The boundary must not appear in the payload. Generate it from a random source rather than a fixed string.
- RFC 7578 says the `filename` value is encoding-dependent and must be sanitised on the receiving side. Never use a client-supplied filename as a server-side path without normalising and constraining it.
- Use `multipart/mixed` when combining heterogeneous parts of a response, and `multipart/related` (RFC 2387) when parts reference each other, such as an HTML part with inline images.

## Checklist

- Does every resource either send `Vary` for the headers it reads, or set `Vary: *` deliberately?
- Does your client send `Accept: */*` or nothing as a fallback, rather than a narrow list?
- Is tie-breaking between equal-quality representations deterministic?
- Does your `ETag` differ per representation, including per content coding?
- Are you compressing only above a size threshold and only compressible media types?
- Is `Content-Encoding` (representation) kept distinct from `Transfer-Encoding` (message framing)?
- If you use `+json`, does your content-type matching handle suffixes before exact types?
- Are multipart boundaries random, and are uploaded filenames normalised before touching the filesystem?
- If you chose a binary format, do you have a documented compatibility story for schema evolution?

## Common mistakes

**Treating `q=0` as "lowest preference".** Symptom: the server sends `br` to a client that cannot decode Brotli, and the client throws on the body. Root cause: ranking by weight without excluding zero. Fix: drop every candidate with `q=0` before ranking.

**Ignoring specificity.** `Accept: text/*;q=0.9, text/csv;q=0.1` — a naive weight sort picks the first, but `text/csv` is the only acceptable exact match. Fix: apply RFC 9110 Section 12.4.2 precedence rules.

**Forgetting `Vary: Accept-Encoding`.** Symptom: a compressed response gets cached and served to a client that asked for `identity`. Fix: send `Vary: Accept-Encoding` on every negotiable response.

**One ETag for all representations.** Symptom: after deploying a gzip change, clients keep receiving stale content because `If-None-Match` matches the identity ETag. Fix: derive distinct ETags per representation, as in RFC 9110 Section 8.8.3.3.

**Negotiating `charset` at request time.** Symptom: inconsistent behaviour between strict and lenient parsers. Fix: `Accept-Charset` is deprecated; declare `charset` in `Content-Type` only.

**Compressing already-compressed payloads.** Symptom: CPU burn with no size benefit. Fix: allowlist compressible media types.

**Letting the `Accept` list drive a stable sort that breaks ties by map iteration order.** Symptom: two identical requests get different formats; impossible to reproduce. Fix: explicit ranking table with a documented tie-break.

## Anti-patterns

- Returning HTML for `Accept: application/json` with a `200` and hoping the client parses it. Return `406` or serve JSON.
- Negotiating on `User-Agent` strings. Use a stable, explicit parameter.
- Sending `Content-Type` without `charset` and relying on defaults for anything but US-ASCII.
- Adding `+json` suffixes to a type whose body is not actually JSON.
- Introducing a binary format for an endpoint whose consumers are humans debugging with `curl`.
- Generating multipart boundaries from a timestamp or a counter.
- Emitting a `406` body that is not itself a useful representation of the problem.

## References

- RFC 9110 Section 12, Content Negotiation - https://www.rfc-editor.org/rfc/rfc9110#section-12
- RFC 9110 Section 8.3, Content-Type - https://www.rfc-editor.org/rfc/rfc9110#section-8.3
- RFC 9111 Section 4.1, Vary - https://www.rfc-editor.org/rfc/rfc9111#section-4.1
- RFC 6839, Additional Media Type Structured Syntax Suffixes - https://www.rfc-editor.org/rfc/rfc6839
- RFC 7578, multipart/form-data - https://www.rfc-editor.org/rfc/rfc7578
- RFC 2046, MIME Part Two: Media Types - https://www.rfc-editor.org/rfc/rfc2046
- RFC 2183, Content-Disposition - https://www.rfc-editor.org/rfc/rfc2183
- RFC 8259, JSON Data Interchange Format - https://www.rfc-editor.org/rfc/rfc8259
- RFC 9457, Problem Details for HTTP APIs - https://www.rfc-editor.org/rfc/rfc9457
- RFC 8949, CBOR - https://www.rfc-editor.org/rfc/rfc8949
- RFC 7396, JSON Merge Patch - https://www.rfc-editor.org/rfc/rfc7396
- RFC 6902, JSON Patch - https://www.rfc-editor.org/rfc/rfc6902
- RFC 7932, Brotli - https://www.rfc-editor.org/rfc/rfc7932
- RFC 8878, Zstandard and the application/zstd media type - https://www.rfc-editor.org/rfc/rfc8878
- RFC 1952, gzip file format - https://www.rfc-editor.org/rfc/rfc1952
- Protocol Buffers documentation - https://protobuf.dev/documentation/
- YAML 1.2 specification - https://yaml.org/spec/1.2.2/
- IANA, Media Types registry - https://www.iana.org/assignments/media-types/media-types.xhtml
- IANA, HTTP Content Coding registry - https://www.iana.org/assignments/http-parameters/http-parameters.xhtml
- MDN, Content negotiation - https://developer.mozilla.org/en-US/docs/Web/HTTP/Content_negotiation