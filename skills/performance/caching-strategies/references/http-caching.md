# HTTP Caching

## Request directives

| Directive | Meaning |
| --- | --- |
| `Cache-Control: max-age=0` | Client considers the response immediately stale; forces revalidation |
| `Cache-Control: no-cache` | Store it, but always revalidate before reuse |
| `Cache-Control: no-store` | Do not store the request or the response anywhere |
| `Cache-Control: max-age=N` | Client will not revalidate for N seconds |
| `Cache-Control: max-stale` / `max-stale=N` | Client accepts a response that is stale by up to N seconds |
| `Cache-Control: min-fresh=N` | Client requires the response to stay fresh for at least N more seconds |
| `Cache-Control: no-transform` | Do not convert the payload (for example, by compressing) |
| `Pragma: no-cache` | Legacy spelling; treat as `Cache-Control: no-cache` if no Cache-Control is present |

`Age` is the header caches add to report how long the response has been cached.

## Revalidation with validators

Validators let a cache revalidate cheaply: the server answers `304 Not Modified` with no
body if the client's copy is still current.

```bash
# 1. Initial request, capture the validator.
curl -sS -D headers.txt -o body.html https://example.com/page
grep -Ei '^(etag|last-modified|cache-control|age):' headers.txt

# 2. Revalidate using the captured validators.
curl -sS -o /dev/null -D - \
  -H "If-None-Match: $(awk 'tolower($1)=="etag:"{sub(/^etag: */,""); gsub(/\r/,""); print}' headers.txt)" \
  https://example.com/page

# Conditional GET with both validators.
curl -sS -o /dev/null -D - \
  -H 'If-None-Match: "a1b2c3d4"' \
  -H 'If-Modified-Since: Sat, 10 Oct 2026 09:00:00 GMT' \
  https://example.com/page
```

`If-None-Match` takes precedence over `If-Modified-Since` when both are present.
`ETag` comparison is strong or weak (`W/"..."`); weak validators compare semantically
equivalent representations. If a resource has no `Last-Modified`, the resource is
considered modified whenever it changes (RFC 9110 section 13.1.3).

A conditional request that returns `304` still costs a round trip. For hot content,
`max-age` and `stale-while-revalidate` remove the round trip entirely.

## Reverse proxy caching in nginx

```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=content_cache:32m
                 max_size=2g inactive=60m use_temp_path=off;

server {
    listen 80;
    server_name example.com;

    location /static/ {
        proxy_cache content_cache;
        proxy_cache_key "$scheme$request_method$host$request_uri";
        proxy_cache_valid 200 10m;
        proxy_cache_valid 404 1m;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_background_update on;
        proxy_cache_lock on;
        proxy_cache_lock_timeout 5s;
        proxy_cache_revalidate on;
        add_header X-Cache-Status $upstream_cache_status always;
        proxy_pass http://backend;
    }
}
```

Notes:

- `proxy_cache_use_stale` is the HTTP-layer equivalent of `stale-if-error`.
- `proxy_cache_background_update on` pairs with `proxy_cache_use_stale updating` and lets
  one request refresh while the rest are served stale.
- `proxy_cache_lock on` is the HTTP-layer stampede defense: only one request populates a
  given key while others wait, up to `proxy_cache_lock_timeout`.
- `$upstream_cache_status` is the field to log if you want to verify hit rate.
- Cache keys must include everything that changes the answer. `$request_uri` alone will
  serve the wrong body to a different `Accept-Encoding` or `Accept-Language`.
- `proxy_cache_valid 404 1m` is negative caching at the proxy.

Pure-POSIX commands above; on Windows the same directives apply under nginx but the cache
path must be a valid Windows path.

## CDN considerations

- **Cache by method and query.** Most CDNs only cache `GET` (and `HEAD`). A cache that
  ignores `Authorization` or `Cookie` risks cross-user leakage; make sure your CDN is
  configured to bypass those or to include them in the key.
- **Cache keys and variants.** `Vary: Accept-Encoding` multiplies your object count by the
  number of encodings. Prefer normalizing encoding at the origin.
- **Purge propagation.** A purge is usually a request to every edge node. Design so a
  purge is not on the critical path of a write.
- **Tiered caching.** Regional shields reduce origin load but add another hop. Confirm the
  number of hops your configuration introduces before assuming an origin hit.
- **Stale serving on error.** `stale-if-error` at the edge is what keeps a 5xx from the
  origin from becoming a user-visible outage.

## Debugging a cache that is not behaving

```bash
# What does the browser have? (DevTools -> Network -> request -> Response Headers)
# What does the proxy think? Log $upstream_cache_status.

# Bypass the cache for one request without changing configuration.
curl -sS -H 'Cache-Control: no-cache' -o /dev/null -D - https://example.com/page

# Force a fresh request from the origin.
curl -sS -H 'Pragma: no-cache' -H 'Cache-Control: no-cache' -o /dev/null -D - https://example.com/page

# See a full exchange including request and response headers.
curl -v -o /dev/null https://example.com/page 2>&1 | grep -Ei 'cache|etag|age|vary'
```

If a response has no `Cache-Control`, the heuristic freshness rules of RFC 9111
section 4.2.2 may apply, which means freshness is derived from `Last-Modified`. Explicit
directives remove the guesswork.

## Common mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Everyone gets `304` but content is wrong | Validator derived from a coarse timestamp | Use content-derived `ETag`s |
| Response cached but `Authorization` header present | Shared cache ignoring auth | Mark `private` or configure the cache to bypass |
| Cache never hits | Key includes a per-user or per-request component | Redesign the key |
| `Age` header missing | Response is not being cached at all | Check `Cache-Control` and the cache's own status header |
| Writes not visible for minutes | Only TTL, no purge or revalidation | Add `stale-while-revalidate` and a purge path |
