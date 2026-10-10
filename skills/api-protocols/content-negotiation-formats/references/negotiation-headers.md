# Negotiation Headers and Caching Reference

Detail for `content-negotiation-formats`. Sources: RFC 9110 Section 12 (Content Negotiation), Section 8.3-8.4 (Content-Type and Content-Encoding), RFC 9111 (Caching).

## Grammar (RFC 9110)

```abnf
Accept = #( media-range [ weight ] )
media-range = ( "*/*"
              / ( type "/" "*" )
              / ( type "/" subtype )
              ) parameters

weight = OWS ";" OWS "q=" qvalue
qvalue = ( "0" [ "." 0*3DIGIT ] )      ; 0.000 to 0.999
        / ( "1" [ "." 0*3("0") ] )     ; 1.000 to 1.000

Accept-Encoding = #( codings [ weight ] )
codings = content-coding / ( content-coding ";" "q=" qvalue )
content-coding = token

Accept-Language = #( language-range [ weight ] )
language-range = <language-range, RFC 5646>

Accept-Charset = #( charset [ weight ] )
charset = token
```

Notes that matter:

- Media type and subtype names are case-insensitive; parameter names are case-insensitive.
- A `q` parameter on a media type other than `Accept` or a range that carries weights has a different meaning. For example, a `q` parameter inside a `Content-Encoding` *content coding* can be a coding-specific parameter. Do not treat every `q` as a weight without checking the field.
- The three-digit limit means `q=0.1234` is invalid. Parse the weight with a strict decimal parser and reject or clamp on failure rather than silently accepting zero.
- Some legacy clients send `Accept-Encoding: gzip;q=1.0, identity; q=0.5` with a space before `q`. OWS is allowed there, but strict parsers vary. Normalise whitespace before matching.

## Resolution algorithm

1. Parse the header into a list of (range, weight) pairs. Reject the header entirely if it cannot be parsed; RFC 9110 allows treating an unparsable header as absent, which is friendlier than failing.
2. Remove every candidate whose matched range has `q=0`.
3. For each available representation, find all matching ranges. Pick the most specific matching range per RFC 9110 Section 12.4.2:

   ```text
   text/*;q=0.3, text/plain;q=0.7, text/plain;format=flowed, text/plain;format=fixed;q=0.4, */*;q=0.5
   ```

   Precedence of a media range against another: same type and subtype beats type with wildcard subtype; both beats `*/*`. When both ranges match a given media type, the more specific one determines the precedence; among equally specific ranges the highest `q` wins.

4. Among the remaining candidates, choose the highest weight.
5. Break ties deterministically with an explicit server preference list. Never with hash-map iteration order.

## Worked examples

```text
Accept: application/xml
Accept: application/xml;q=0, application/json
Accept: */*                     -> default representation
Accept: text/*;q=0.9, text/csv;q=0.1
Accept: application/*+json
Accept: text/html;q=0.5, application/xhtml+xml, image/avif, */*;q=0.8
```

Resulting behaviour:

- `application/xml` with nothing else matches: only `application/xml`. If the server has no XML representation, `406`.
- `application/xml;q=0, application/json`: XML is forbidden outright, JSON is acceptable. This is the correct way to forbid a representation.
- `text/*;q=0.9, text/csv;q=0.1`: `text/csv` wins despite the lower `q`, because it is the more specific match.
- `application/*+json`: matches `application/problem+json`, `application/merge-patch+json`, `application/json`. It does *not* match `application/json` (that is `application/json`, no suffix), so a server relying only on this header may serve nothing.
- The last example is the historical browser header. `application/xhtml+xml` and `image/avif` both match exactly with implicit `q=1`, so they win over `*/*;q=0.8`.

## Server response rules

| Situation | Response |
| --- | --- |
| No `Accept`, or `Accept` absent from the request | Serve your default representation |
| `Accept: */*` | Serve your default representation |
| `Accept` present, at least one match, weight above 0 | Serve the best match, set `Content-Type` |
| `Accept` present, no match | `406 Not Acceptable` |
| `Accept` present, only `q=0` matches | `406 Not Acceptable` (log it; often a client bug) |
| `Accept-Encoding: identity` explicitly | Serve uncompressed |
| `Accept-Encoding` absent | Serve identity, unless your server has a documented default |
| No `Accept-Encoding` match but identity is always acceptable | Serve identity |

An `Accept-Encoding` that is absent or `*` does not mean "I only accept gzip". It means any coding is fine, and the server picks.

## Vary, cache keys, and validators

```text
Vary: Accept-Encoding
Vary: Accept, Accept-Encoding
Vary: *
```

- `Vary: *` marks the response as not reusable by a shared cache. Use it when the selection depends on something you cannot express as a request header.
- Every request header your selection logic reads must appear in `Vary`, or you have a correctness bug, not a performance bug.
- Every request header you *do not* read but a downstream component might still vary on, consider including, because caches upstream of you may not know. Erring toward over-declaring is safer.
- Cache key multiplication: `Vary: Accept-Language` on a hundred-language API multiplies entries by a hundred. Normalise internally, declare honestly.
- `Vary: Origin` is required for CORS responses, since the response depends on the `Origin` request header.
- A `Vary` on a header that a proxy rewrites (for example `Accept-Encoding` normalised to `gzip`) breaks the match between what the client sent and what the cache keys on. Normalise consistently at the edge.

### ETag per representation

RFC 9110 Section 8.8.3.3 shows the pattern for an entity tag that varies with a negotiated representation: derive the tag from the representation data itself.

```text
HTTP/1.1 200 OK
Content-Type: application/json
Content-Encoding: br
ETag: "a1b2c3d4"
Vary: Accept-Encoding

HTTP/1.1 200 OK
Content-Type: application/json
Content-Encoding: identity
ETag: "9f8e7d6c"
Vary: Accept-Encoding
```

Two different tags for the same logical resource, because the bytes differ. A single tag across codings makes `If-None-Match` return `304` to a client that cannot decode the stored variant.

### `Content-Language`

`Content-Language` (RFC 9110 Section 8.5) is descriptive, not negotiable on its own. It tags the natural language of the content. `Content-Location` and `Content-Language` do not imply that the body is encoded in that language. Many servers send `Content-Language` without ever consulting `Accept-Language`; that is legal but usually a bug if the request header was present.

## `Accept-Charset` in practice

`Accept-Charset` is defined in RFC 9110 Section 12.5.2 and was deprecated in the same document because character-set handling moved into the `charset` parameter of `Content-Type` (RFC 9110 Section 8.3.2). Practical guidance:

- Serve UTF-8 unconditionally unless you have a specific, tested reason not to.
- Declare `charset=utf-8` in `Content-Type` for text types.
- Do not echo `Accept-Charset` into `Vary` if you ignore it; that destroys your cache hit ratio for nothing.
- For JSON, RFC 8259 requires UTF-8 for interchange, so `application/json` needs no charset parameter at all. Emitting `application/json; charset=utf-8` is common and harmless, but strictly speaking the parameter is not defined for `application/json`.

## Compression negotiation order

1. Parse `Accept-Encoding`, normalise tokens to lowercase, discard `q=0` codings.
2. Select by your own preference order among acceptable codings.
3. If the chosen coding is `gzip`, decide whether to use the legacy `x-gzip` token. RFC 9112 Section 7.2 defines `x-gzip` as an alias; send the standard `gzip` token and treat `x-gzip` on receive as `gzip`.
4. If the response will be compressed, decide independently whether to chunk it. If the compressed length is known ahead of time, set `Content-Length` on the compressed byte count and skip chunking entirely.
5. Set `Vary: Accept-Encoding` on every response where a client could receive different codings, including identity responses. A cache that stored an identity response without `Vary` will serve it to a br client, and vice versa.

## Reference parser behaviour worth matching

| Input | Behaviour |
| --- | --- |
| `Accept:` (empty) | Treated as "no preference expressed" by RFC 9110 Section 12.4.1, but many implementations treat it as nothing acceptable. Pick one deliberately and document it |
| `Accept: ,,,` | Ignore empty list elements per the `#rule` ABNF extension |
| `Accept: application/json; q=1.0000` | Invalid, more than three digits. Reject the weight or the header |
| `Accept: */*;q=1.5` | Invalid, above 1. Clamp or reject |
| Repeated `Accept` lines | Combine as a list per the header's list rule |

## References

- RFC 9110 Section 12, Content Negotiation - https://www.rfc-editor.org/rfc/rfc9110#section-12
- RFC 9110 Section 5.6.1, Lists - https://www.rfc-editor.org/rfc/rfc9110#section-5.6.1
- RFC 9110 Section 12.4.2, Quality Values - https://www.rfc-editor.org/rfc/rfc9110#section-12.4.2
- RFC 9111 Section 4.1, Calculating Cache Keys with Vary - https://www.rfc-editor.org/rfc/rfc9111#section-4.1
- RFC 9110 Section 8.8.3.3, Example: Entity Tags Varying on Content-Negotiated Resources - https://www.rfc-editor.org/rfc/rfc9110#section-8.8.3.3
- RFC 9112 Section 7.2, Transfer Codings for Compression - https://www.rfc-editor.org/rfc/rfc9112#section-7.2
- RFC 5646, Tags for Identifying Languages - https://www.rfc-editor.org/rfc/rfc5646
- MDN, Accept header - https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Accept
- MDN, Vary header - https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Vary
- Fetch Standard, CORS protocol and headers - https://fetch.spec.whatwg.org/