# Wire Format and Compression Reference

Detail for `content-negotiation-formats`.

## JSON (RFC 8259)

RFC 8259 is an Informational RFC that explicitly does not define a normative grammar, deferring to ECMA-404 for the formal specification. The practical grammar everyone implements:

```json
{
  "string": "escaped with \" \\ \/ \b \f \n \r \t \u0041",
  "number": -0.5e10,
  "object": { "key": "value" },
  "array": [1, 2, 3],
  "true": true,
  "false": false,
  "null": null
}
```

Properties that matter in production:

- Numbers are doubles in nearly every implementation. A 64-bit integer or a decimal monetary amount is not reliably representable. Use strings for money and for identifiers beyond 2^53.
- Duplicate object member names are permitted by the grammar but behaviour is unpredictable in most parsers. Last-wins is common. Reject duplicates on strict endpoints.
- Deeply nested input is a DoS vector: a payload of a few hundred bytes can nest thousands of levels. Enforce a depth limit in your parser.
- UTF-8 is required for interchange (RFC 8259 Section 8.1). Escaped surrogate pairs must be well formed; lone surrogates are invalid.
- Serialisation differences (key order, whitespace, number formatting, escaping) break byte-level equality. If you sign or hash JSON, you need a canonicalisation scheme, and "whatever my library emits" is not one.

## YAML 1.2

```yaml
# comment
id: 42
name: widget            # trailing comment
nested:
  a: 1
  b: [1, 2, 3]
list:
  - one
  - two
literal: |
  line one
  line two
folded: >
  these lines
  are folded
anchor: &base
  shared: true
derived:
  <<: *base
  extra: 1
```

- The "Norway problem" (`no` parsed as boolean false) is a YAML 1.1 behaviour. YAML 1.2's core schema removes it. Check which schema your parser implements before assuming.
- YAML is a superset of JSON, which means it also accepts constructs you may not expect. If you accept user-supplied YAML, constrain it.
- Aliases and merge keys are a DoS vector: a "billion laughs"-style YAML bomb expands exponentially. Disable alias expansion or set an explicit node/alias limit.
- Multi-line scalars (`|`, `>`) silently change trailing whitespace semantics. Use them deliberately.
- Line length is unbounded in the spec. Impose a limit.

## XML (XML 1.0 5th edition, W3C)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="https://example.com/ns/widget" id="42">
  <name>widget</name>
  <tags><tag>a</tag><tag>b</tag></tags>
</widget>
```

- Well-formedness is a hard requirement; parse with a namespace-aware, non-validating parser and disable external entity resolution (XXE). Set an explicit "no DTD" policy.
- DTDs, entities, and `xsi:schemaLocation` are attack surface, not features, for an API endpoint.
- Attribute order is not significant; element order is, unless a schema says otherwise.
- Namespaces: elements in different namespaces are different elements even with the same local name. Always compare namespace-qualified names.

## CBOR (RFC 8949)

Major types (RFC 8949 Section 3):

| Major type | Number | Additional info values |
| --- | --- | --- |
| Unsigned integer | 0 | 0 to 23 inline, 24 = 1 byte, 25 = 2 bytes, 26 = 4, 27 = 8 |
| Negative integer | 1 | Value is `-1 - n` |
| Byte string | 2 | Length-prefixed |
| Text string | 3 | UTF-8, length-prefixed |
| Array | 4 | Length then items, or indefinite `0x9f ... 0xff` |
| Map | 5 | Length then pairs, or indefinite `0xbf ... 0xff` |
| Tag | 6 | Number-prefixed semantic tag wrapping one item |
| Simple / float | 7 | 25 = half, 26 = single, 27 = double; 20 = false, 21 = true, 22 = null, 23 = undefined |

Head encoding rule: the first byte is `major << 5 | additional_info`. If `additional_info <= 23`, that is the value. If it is 24, 25, 26, or 27, that many following bytes hold the value. If it is 31, the value is indefinite length.

Tags you will meet:

| Tag | Meaning |
| --- | --- |
| 0 | Standard date/time string (RFC 3339 profile) |
| 1 | Epoch-based date/time |
| 2 | Positive bignum, 3 = negative bignum |
| 4 | Decimal fraction, 5 = bigfloat |
| 21, 22, 23 | Expected conversion to `uint64`, `negint64`, `float64` |
| 32 | URL |
| 33 | Base64url |
| 34 | Regular expression |
| 35 | MIME message |
| 36 | Binary data |
| 55799 | Self-described CBOR |

Self-described CBOR (tag 55799) is how you wrap a CBOR document so that a receiver can tell it apart from another binary format. Deterministic encoding, also from RFC 8949, fixes the choices above: definite-length items, shortest-form integer encoding, and code-point-ordered map keys. Use it whenever bytes are hashed or signed.

## MessagePack

The specification is maintained at msgpack.org. Same major-type shape as CBOR with slightly different encodings (positive fixint, fixmap, fixstr, and so on). It is a small, permissive spec with broad library coverage, which is its main appeal. It has no standardised notion of a deterministic encoding or of self-description tags, so hashing a MessagePack payload requires a canonicalisation scheme you define yourself.

## Protocol Buffers

Wire types in the binary encoding:

| Wire type | Number | Encoding |
| --- | --- | --- |
| Varint | 0 | Base-128, little-endian groups |
| 64-bit | 1 | Fixed 8 bytes, little-endian |
| Length-delimited | 2 | Varint length followed by bytes |
| 32-bit | 5 | Fixed 4 bytes, little-endian |

```protobuf
syntax = "proto3";

package shop.v1;

message Order {
  string  id            = 1;
  int64   quantity      = 2;
  Money   total         = 3;
  repeated string tags  = 4;
  map<string, string> metadata = 5;
}

message Money {
  string currency_code = 1;
  int64  units         = 2;
  int32  nanos         = 3;
}
```

```text
# Order { id: "A-1", quantity: 2 } encoded, shown as tagged hex.
field 1 (LEN)   0x0a 03 41 2d 31
field 2 (VARINT) 0x10 02
```

Key insight for schema evolution: the tag number is the contract, the field name is documentation. Two rules follow. Never reuse a field number, because a reader will try to decode the new type with the old schema. Never change a field's type in a way that changes its wire type, because that is not a compatible change. Renaming a field, or adding a new field number, is compatible.

- `proto3` gives scalars default values and no required/optional distinction by default. Explicit `optional` in proto3 gives presence; check the language guide for the exact semantics in your compiler version.
- Enums, `oneof`, `Any`, and well-known types (`Timestamp`, `Duration`, `Struct`, `wrappers`) all exist; read the official language guide before assuming their wire behaviour.
- "Editions" is the newer mechanism for language-level feature gating. Check the current protobuf documentation for what is stable in your toolchain; this area moves.

## Size and speed: what to say and what not to say

Do not quote percentages. Ratios and throughput depend on your data, your compressor settings, your CPU, and your runtime. What you can say honestly:

- Text formats compress well because repetition and a small alphabet give the entropy coder redundant structure to exploit.
- Binary formats that use varints shrink small integers dramatically compared to fixed-width binary, and shrink textual identifiers a lot compared to JSON's quoting.
- Random-access parsing is easier in self-describing formats (JSON, CBOR) because you can skip to a field. In protobuf you must walk the message sequentially unless a submessage is separated out.
- Reflection-driven parsing of a binary format costs more CPU than a hand-written reader for a fixed schema, which is why protobuf is faster in practice than its byte count alone suggests.
- Compression is dominated by the payload's compressibility, not by the codec. Measure with your data.

## Compression: mechanisms and failure modes

- **gzip (RFC 1952)** wraps DEFLATE (RFC 1951) with a header carrying CRC-32 and size. Universally supported. Decoders are a classic source of memory-exhaustion bugs because of how they handle back-references; use a maintained implementation.
- **Brotli (RFC 7932)** uses a static dictionary and a context-modelled arithmetic coder with built-in transforms for text. Generally produces smaller output than gzip at comparable CPU for text-like data.
- **zstd (RFC 8878)** combines entropy stages with a windowed match finder and trains a dictionary. Very fast at low ratios and competitive at high ratios. RFC 8878 also registers the `application/zstd` media type for zstd-compressed CBOR, which is a different use from the HTTP `zstd` content coding.
- **Interoperability trap:** a content coding is only usable if the client advertises it in `Accept-Encoding`. An old client sends `gzip, deflate` and will not get `br` or `zstd`, which is the correct behaviour.
- **Nested compression does not compose.** Compressing a `.gz` again with Brotli yields a large file and wasted CPU. Never chain content codings for already-compressed media types.
- **Compression plus side channels.** Compressed responses are not the same length as identity responses, which leaks information through response size. This is a known side channel in BREACH-style attacks against secrets reflected into a compressed response alongside attacker-controlled input. If you compress responses that mix secret and attacker-controlled data, that is a real risk.
- **Server CPU amplification.** An attacker can request the most expensive coding repeatedly. Rate-limit by negotiated coding if compression is expensive in your deployment.

## Choosing and defending the choice

Write down four answers before choosing:

1. Who consumes this? Browsers and scripts, other services you deploy code for, or both?
2. Do you need to inspect payloads in production with standard tooling (`curl`, a browser, a text editor)?
3. Is byte-level determinism required for signing or deduplication?
4. What is the actual measured cost of the text format, over your real payloads and your real network?

If the answer to (2) is yes, a binary format costs you more in operational friction than it saves in bytes. Almost always the right default is JSON, with `Content-Encoding` doing the compression work.

## References

- RFC 8259, JSON - https://www.rfc-editor.org/rfc/rfc8259
- RFC 7493, The `application/json` Media Type - https://www.rfc-editor.org/rfc/rfc7493
- RFC 8949, CBOR - https://www.rfc-editor.org/rfc/rfc8949
- RFC 7049 (obsoleted by RFC 8949) - https://www.rfc-editor.org/rfc/rfc7049
- MessagePack specification - https://msgpack.org/
- Protocol Buffers encoding reference - https://protobuf.dev/programming-guides/encoding/
- Protocol Buffers documentation - https://protobuf.dev/documentation/
- YAML 1.2.2 specification - https://yaml.org/spec/1.2.2/
- W3C XML 1.0 Fifth Edition - https://www.w3.org/TR/xml/
- RFC 1951, DEFLATE - https://www.rfc-editor.org/rfc/rfc1951
- RFC 1952, gzip - https://www.rfc-editor.org/rfc/rfc1952
- RFC 7932, Brotli - https://www.rfc-editor.org/rfc/rfc7932
- RFC 8878, Zstandard - https://www.rfc-editor.org/rfc/rfc8878