---
name: Web Security Headers
description: Covers browser security headers and policy including Content Security Policy with nonce, hash, and strict-dynamic strategies, HSTS, X-Content-Type-Options, frame-ancestors and X-Frame-Options, Referrer-Policy, Permissions-Policy, COOP COEP CORP for cross-origin isolation, rolling CSP out without breaking the app, and measuring policy violations. Use when the user asks to add security headers, set up a CSP, stop clickjacking, configure HSTS, isolate a page for SharedArrayBuffer, or debug policy violations.
metadata:
  opencode/autoinvoke: true
---

# Web Security Headers

Security headers are the browser-enforced part of your threat model. They cannot fix a broken
authorization check, but they reliably stop the whole classes of attack they cover: script
injection, framing, MIME confusion, protocol downgrade, and excessive permission grants. Most
are one-time configuration; CSP is a project.

## When to use this

- Setting or reviewing response security headers for a web app.
- Rolling out a Content Security Policy on an existing site without breaking it.
- Stopping clickjacking, MIME sniffing, or referrer leakage.
- Enabling cross-origin isolation for `SharedArrayBuffer` or high-resolution timers.
- Reducing device-permission exposure with Permissions-Policy.
- Diagnosing `Refused to ... because it violates the following Content Security Policy`.

## Quick start

A defensible baseline for a server-rendered application:

```text
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; style-src 'self' 'nonce-{RANDOM}'; script-src 'self' 'nonce-{RANDOM}'; connect-src 'self'; upgrade-insecure-requests
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), camera=(), microphone=(), payment=(), usb=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```

Ship the report-only version first, collect violations, then enforce. Details below.

## Content Security Policy

CSP is specified in CSP Level 3. It is a set of directives, each with one or more sources. A
directive not present falls back to `default-src`.

### The directives you will actually use

| Directive | Governs |
| --- | --- |
| `default-src` | Fallback for the `fetch`-type directives below |
| `script-src` | `<script>`, workers, event handlers |
| `script-src-elem` | `<script>` elements only, overriding `script-src` |
| `script-src-attr` | Inline event handler attributes (`onclick`) |
| `style-src` | `<style>`, `<link rel=stylesheet>`, inline `style` attributes |
| `img-src` | Images, `image-set`, CSS `url()` for images |
| `connect-src` | `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `sendBeacon` |
| `font-src` | Web fonts |
| `media-src` | `<audio>`, `<video>`, `<track>` |
| `frame-src` | `<iframe>`, `<frame>` |
| `worker-src` | Web workers and `SharedWorker` |
| `object-src` | `<object>`, `<embed>`. Set to `'none'` |
| `manifest-src` | Web app manifests |
| `base-uri` | `<base href>`. Restricting this blocks base-tag hijacking |
| `form-action` | Where `<form>` submissions may go |
| `frame-ancestors` | Which origins may frame this page. Replaces `X-Frame-Options` |
| `sandbox` | Applies the HTML sandbox to the page, like an iframe sandbox attribute |
| `upgrade-insecure-requests` | Rewrite HTTP subresource URLs to HTTPS |
| `block-all-mixed-content` | Block mixed content. Superseded in practice by `upgrade-insecure-requests` |
| `report-uri` / `report-to` | Where violation reports go |
| `trusted-types` / `require-trusted-types-for` | Constrain DOM sinks (CSP 3; limited support) |

### Source expressions

| Expression | Meaning |
| --- | --- |
| `'self'` | Same origin (scheme, host, port) |
| `'none'` | Nothing allowed; use for `object-src`, `base-uri`, `frame-ancestors` |
| `'unsafe-inline'` | Inline script or style. Avoid; see the nonce note below |
| `'unsafe-eval'` | `eval()`, `new Function()`, string-based `setTimeout`. Required by some legacy code |
| `'unsafe-hashes'` | Allow inline event handlers by hash |
| `'nonce-{base64}'` | A per-response random value, unpredictable to an attacker |
| `'sha256-{base64}'` | Allow a specific inline script or style by hash |
| `'strict-dynamic'` | Trust scripts loaded by a nonce/hash script, transitively |
| `https:` | Any HTTPS origin |
| `*` | Any origin. Not the same as `'none'` or `'self'`; it also matches `data:` and `blob:` in some contexts |

The single most important rule: **when a nonce or a hash source is present in a directive, the
browser ignores `'unsafe-inline'` in that same directive.** That is what makes the nonce
migration safe to start — you add the nonce and can drop `'unsafe-inline'` at the same time.

### Nonce strategy

Generate a fresh, unpredictable value per response. Never reuse it across requests: the whole
value of a nonce is that an attacker cannot predict it for the response they are injecting into.

```javascript
import { randomBytes } from 'node:crypto';

export function cspNonce(req, res, next) {
  const nonce = randomBytes(16).toString('base64');
  res.locals.cspNonce = nonce;
  res.setHeader(
    'Content-Security-Policy',
    [
      "default-src 'self'",
      `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
      `style-src 'self' 'nonce-${nonce}'`,
      "img-src 'self' data:",
      "object-src 'none'",
      "base-uri 'none'",
      "frame-ancestors 'none'",
      "form-action 'self'",
    ].join('; ')
  );
  next();
}
```

In a template:

```html
<script nonce="{{ cspNonce }}">window.__APP_VERSION__ = "1.4.0";</script>
<script nonce="{{ cspNonce }}" type="module" src="/assets/app.js"></script>
```

Notes:

- Nonces must be base64. `randomBytes(16).toString('base64')` is fine.
- Attach the nonce to **every** `<script>`, including external `src` scripts, or they are blocked.
- A cached HTML page that still contains an old nonce will break. Send
  `Cache-Control: no-store` or ensure the HTML is not shared-cached.
- Put the nonce in the response header, not in a cookie; a cookie is readable from JavaScript and
  defeats the purpose.

### Hash strategy

Good for a small, fixed set of inline scripts that will never change.

```text
script-src 'self' 'sha256-B2yPHKaXnvFWtRChIbabYmUBFZdVfKKXHbWtWidDVF8='
```

Compute the hash of the exact bytes of the element's content, including whitespace:

```bash
printf 'console.log("hi")' | openssl dgst -sha256 -binary | openssl base64
```

`{base64-value}` braces are a valid equivalence prefix in CSP 2 and 3, so
`'sha256-{BASE64}'` and `'sha256-BASE64'` are equivalent. Confirm against your target browsers if
you rely on the brace form.

### strict-dynamic

```text
script-src 'nonce-{RANDOM}' 'strict-dynamic'; object-src 'none'
```

`'strict-dynamic'` lets a script that is itself trusted by nonce or hash load further scripts,
and makes host allowlists and `'unsafe-inline'` be ignored in that directive. It is how you
survive a dependency that injects a script tag at runtime, because the injected script is loaded
by an already-trusted script.

Caveats:

- It requires a nonce or hash to be present; without one it does nothing useful.
- It is for `script-src`, not styles. There is no `'strict-dynamic'` equivalent for `style-src`,
  which is why style nonces exist.
- It moves your trust model from "which origins" to "which first script". Ensure first scripts are
  only your own.

### Sandbox

```text
sandbox allow-scripts allow-forms allow-same-origin allow-popups
```

Values include `allow-scripts`, `allow-forms`, `allow-same-origin`, `allow-popups`,
`allow-modals`, `allow-downloads`, `allow-pointer-lock`, `allow-presentation`,
`allow-top-navigation-by-user-activation`, and others. An empty value (or omitting the token
list) applies the maximum restrictions. `allow-scripts` together with `allow-same-origin` on
same-origin content is close to no sandbox, because the script can remove its own sandbox
attribute; treat that pair as a decision, not a default.

### Delivering CSP

Three mechanisms, in descending order of capability:

1. **HTTP header** (`Content-Security-Policy`). Use this. It applies to every response including
   errors and redirects.
2. **`<meta http-equiv="Content-Security-Policy" content="...">`**. Must be in the document head
   before the content it restricts, is not applied to responses other than HTML, and several
   directives are ignored in this form — notably `frame-ancestors`, `report-uri`, and `sandbox`.
   Use only where a header is impossible.
3. **JavaScript** via `document.securityPolicyViolationEvent` and the Reporting API. Observability
   only, not enforcement.

## Measuring and reporting violations

```text
Content-Security-Policy: default-src 'self'; report-uri /csp-report; report-to csp-endpoint
Reporting-Endpoints: csp-endpoint="https://example.com/csp-reports"
```

`report-uri` is the older mechanism and is deprecated in CSP Level 3 in favor of `report-to` with
a `Reporting-Endpoints` header. Check browser support in your target matrix before choosing; both
are still implemented widely, and `report-uri` has the advantage of working without extra headers.

The report body is JSON with the violated directive, the blocked URI, the original policy, and
the document that was blocked:

```json
{
  "csp-report": {
    "document-uri": "https://app.example.com/orders",
    "violated-directive": "script-src-elem",
    "blocked-uri": "https://cdn.thirdparty.example/analytics.js",
    "effective-directive": "script-src-elem",
    "original-policy": "default-src 'self'; script-src 'self' 'nonce-abc'"
  }
}
```

In the browser, violations fire as events:

```javascript
document.addEventListener('securitypolicyviolation', (event) => {
  navigator.sendBeacon('/client-csp-reports', JSON.stringify({
    blockedURI: event.blockedURI,
    violatedDirective: event.violatedDirective,
    effectiveDirective: event.effectiveDirective,
    line: event.lineNumber,
  }));
});
```

The `securitypolicyviolation` event and `SecurityPolicyViolationEvent` are real; the properties
above match the specification. Note that in some browsers the inline script that installs this
listener is itself subject to the policy, so serve it as an external script with a nonce.

**Report-only mode** is the rollout tool:

```text
Content-Security-Policy-Report-Only: default-src 'self'; report-uri /csp-report
```

Everything is measured and nothing is blocked, including `default-src` itself and
`upgrade-insecure-requests`. Report-only can be sent in addition to the enforced header, which
lets you trial a stricter policy while enforcing a lax one.

## HSTS

```text
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

- Only honored over HTTPS. Delivered over HTTP it is ignored entirely.
- `max-age` is required and is in seconds. Set it low first (`300`), raise it once you are
  confident no subdomain will break.
- `includeSubDomains` covers every subdomain. This is the field that breaks things: confirm
  every subdomain is HTTPS-capable before adding it.
- `preload` opts into browser preload lists and has hard requirements: a valid certificate, a
  redirect from HTTP to HTTPS on the base domain, `includeSubDomains`, and `max-age` of at least
  one year. Removal is not self-service; think before submitting.

HSTS eliminates the SSL-strip class of attacks. It does not stop HTTP traffic being *sent*
unencrypted before the policy takes effect, which is why `upgrade-insecure-requests` in CSP and
`Redirect-All` style server rules still matter.

## X-Content-Type-Options

```text
X-Content-Type-Options: nosniff
```

Stops browsers from sniffing a response's media type away from the declared `Content-Type`.
The classic exploitation path is an "image" that is actually HTML: without `nosniff`, a
same-origin file upload served with a permissive content type can be navigated to and executed
in your origin.

Its highest-value effect is on stylesheets: a stylesheet served as anything other than
`text/css` is refused when `nosniff` is set. Send it on everything.

## Framing protection: clickjacking

Two mechanisms, and the older one is a fallback.

```text
Content-Security-Policy: frame-ancestors 'none'
```

`frame-ancestors` replaces `X-Frame-Options` and is the one to use. It accepts source
expressions, so `frame-ancestors 'self' https://partner.example.com` allows exactly those.

```text
X-Frame-Options: DENY
```

`DENY` blocks all framing; `SAMEORIGIN` allows same-origin. `ALLOW-FROM` is obsolete and ignored
by current browsers. Send both: modern browsers use `frame-ancestors`, and older ones fall back
to `X-Frame-Options`.

Note the distinction from `sandbox`: `frame-ancestors` controls *who may frame you*;
`form-action` controls where your forms may submit; `X-Frame-Options`/`frame-ancestors` do
nothing to stop a same-origin page from framing you if you allow `'self'`.

## Referrer-Policy

```text
Referrer-Policy: strict-origin-when-cross-origin
```

Values: `no-referrer`, `no-referrer-when-downgrade`, `same-origin`, `origin`,
`strict-origin`, `origin-when-cross-origin`, `strict-origin-when-cross-origin`, `unsafe-url`.

`strict-origin-when-cross-origin` sends only the origin on cross-origin HTTPS requests, nothing
on HTTPS-to-HTTP, and the full URL for same-origin requests. It is the default in current
browsers, but set it explicitly: the default differs by browser and a policy that changes when a
browser ships an update is not a policy.

Use `no-referrer` if the URL query strings contain anything sensitive.

## Permissions-Policy

Part of the Feature Policy family; it restricts which powerful device features a document and
its embedded content may use.

```text
Permissions-Policy: geolocation=(), camera=(), microphone=(), payment=(), usb=(), accelerometer=(), gyroscope=(), magnetometer=(), display-capture=()
```

Allowlist syntax is a feature name followed by an allowlist in parentheses:

```text
Permissions-Policy: camera=(self "https://video.example.com"), geolocation=(self), fullscreen=(*)
```

Empty allowlist `()` denies everywhere, which is the right default for features your application
does not use. `self` permits the document's own origin; a quoted origin permits that origin;
`*` permits any origin.

The attribute spelling has churned — it was historically `whitelist` and is now `allowlist` —
so verify the current spelling in the specification or MDN before deploying. This skill uses
`allowlist`.

Also set the corresponding browser permissions prompt defaults (`Permissions-Policy` for the
iframe case) and remember that Permissions-Policy does not apply to an API the page does not use.

## Cross-origin isolation: COOP, COEP, CORP

To use `SharedArrayBuffer` and precise timers, a page must be **cross-origin isolated**. That
requires two response headers together, and usually a third to keep embedded resources working.

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

| Header | Values | Purpose |
| --- | --- | --- |
| `Cross-Origin-Opener-Policy` | `same-origin`, `same-origin-allow-popups`, `noopener-allow-popups` | Isolates the browsing context group. Breaks `window.opener` across origins and requires same-origin popups to carry `COOP` |
| `Cross-Origin-Embedder-Policy` | `require-corp`, `credentialless`, `unsafe-none` | Requires every embedded resource to opt in via CORS or `CORP` |
| `Cross-Origin-Resource-Policy` | `same-origin`, `same-site`, `cross-origin` | Declares who may embed this resource |

Values and consequences:

- `COEP: require-corp` means every cross-origin subresource must be served with
  `Cross-Origin-Resource-Policy: cross-origin` or a suitable CORS mode. If you cannot control
  those origins, this breaks the page.
- `COEP: credentialless` loads no-cors cross-origin resources without credentials, which avoids
  many of those failures, at the cost of no credentials on those requests.
- `COOP: same-origin` severs the `window.opener` relationship and `postMessage` targeting across
  origins. If you rely on popups communicating with the opener, use
  `same-origin-allow-popups` and carry `COOP` on the popup too.

Verify isolation from the page:

```javascript
console.log(crossOriginIsolated); // true when COOP + COEP are effective
```

`crossOriginIsolated` is a real global boolean that reflects the actual state, which is better
than inferring it from headers.

## Deprecations and omissions

- `X-XSS-Protection` is deprecated and ignored by current browsers. Omit it, or set it to `0`
  explicitly to neutralize very old browsers that implement a vulnerable version of it.
- `X-Frame-Options: ALLOW-FROM` is obsolete; use `frame-ancestors`.
- `report-uri` is deprecated in CSP 3; use `report-to`. `report-uri` still works widely and is
  simpler to adopt.
- `Feature-Policy` is the predecessor of `Permissions-Policy`; do not ship both.

## Rollout plan for CSP on an existing site

1. **Report-only with the policy you eventually want.** Start wide:
   `default-src 'self'; report-uri /csp-report`. Collect.
2. **Read the reports for a week.** Classify each violation as yours (inline script, inline
   style, API host, font CDN) or a third party's.
3. **Add nonces to your own scripts and styles.** This is the actual work. Inline handlers
   (`onclick="..."`) must become `addEventListener` — CSP 3 can allow them with
   `'unsafe-hashes'`, but that is a step backwards.
4. **Whitelist the third-party origins you confirmed are necessary**, one at a time, and record
   why each is there.
5. **Enforce the policy you are satisfied with.** Keep sending a stricter report-only policy
   alongside it.
6. **Tighten toward `'strict-dynamic'`** for scripts and `'none'` for `object-src` and
   `base-uri`.

Do not skip from "no CSP" straight to a strict policy on a busy site. The step that fails is
almost always inline styles, and discovering that during an incident is worse than a two-week
rollout.

## Checklist

- [ ] `Strict-Transport-Security` with a `max-age` you have verified, and `includeSubDomains`
      only if every subdomain is HTTPS.
- [ ] `Content-Security-Policy` on HTML responses, with `object-src 'none'` and
      `base-uri 'none'` at minimum.
- [ ] `script-src` uses nonces or hashes, not `'unsafe-inline'`.
- [ ] Violation reporting is on (`report-uri` or `report-to`) and someone reads the reports.
- [ ] `X-Content-Type-Options: nosniff` on all responses.
- [ ] `frame-ancestors` set, plus `X-Frame-Options` for legacy clients.
- [ ] `Referrer-Policy` set explicitly.
- [ ] `Permissions-Policy` denies features the app does not use.
- [ ] `upgrade-insecure-requests` present or an equivalent HSTS-plus-redirect policy.
- [ ] Headers are applied to error responses, not only `200`s.
- [ ] Any `COOP`/`COEP` combination is tested against every embedded third-party resource.

## Common mistakes

**A nonce that is static or reused.** Symptom: an injected inline script executes anyway.
Root cause: the nonce is derived from something predictable. Fix: `randomBytes` per response, and
verify with a CSP report.

**A nonce on the header but not on the `<script src>` tags.** Symptom: everything is blocked with
`script-src-elem`. Fix: nonce every script element, external ones included.

**Adding `'strict-dynamic'` without a nonce.** Symptom: nothing changes, because
`'strict-dynamic'` only matters when a nonce or hash is present.

**Enforcing CSP with inline styles still in the app.** Symptom: unstyled pages. Fix: nonce the
styles, or use `'unsafe-inline'` in `style-src` only while you migrate.

**`frame-ancestors` in a `<meta>` tag.** Symptom: clickjacking protection silently absent.
Fix: send it as a header.

**`report-uri` on the enforced policy only.** You find out about breakage after users do. Fix:
report-only first.

**`Access-Control-Allow-Origin: *` with `Cross-Origin-Embedder-Policy: require-corp`.**
Symptom: cross-origin images blocked. Fix: `CORP: cross-origin` on those resources, or move to
`COEP: credentialless`.

**HSTS `max-age=31536000` added on day one.** Symptom: a non-HTTPS subdomain becomes
unreachable for a year. Fix: start at `max-age=300`, then raise.

## Anti-patterns

- **CSP as a substitute for output encoding and sanitization.** CSP is a second line of defence;
  it does not make unescaped interpolation safe.
- **`'unsafe-inline'` in `script-src` "temporarily".** It is permanently there.
- **`*` in `script-src`.** That is no script protection at all.
- **Reporting endpoints that log and nobody reads.** Reporting without a triage loop is a cost
  with no benefit.
- **Copying a policy wholesale from a blog post** without checking it against your own
  subresources.
- **Applying headers only in the edge proxy**, so direct-to-origin access bypasses them.
- **Blocking all third parties as a goal rather than a decision.** Some are load-bearing.
- **Treating CSP as complete.** It does nothing about XSS through the DOM APIs that CSP 3's
  Trusted Types does not yet cover everywhere.

## References

- Content Security Policy Level 3 (W3C): https://www.w3.org/TR/CSP3/
- MDN, Content-Security-Policy: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy
- MDN, `script-src`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/script-src
- MDN, `'strict-dynamic'`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/script-src#strict-dynamic
- MDN, `Content-Security-Policy-Report-Only`:
  https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy-Report-Only
- MDN, `Strict-Transport-Security`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security
- MDN, `X-Content-Type-Options`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Content-Type-Options
- MDN, `X-Frame-Options`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options
- MDN, `Referrer-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Referrer-Policy
- MDN, `Permissions-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Permissions-Policy
- MDN, `Cross-Origin-Opener-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cross-Origin-Opener-Policy
- MDN, `Cross-Origin-Embedder-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cross-Origin-Embedder-Policy
- MDN, `Cross-Origin-Resource-Policy`: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cross-Origin-Resource-Policy
- MDN, same-origin policy: https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy
- MDN, `SecurityPolicyViolationEvent`: https://developer.mozilla.org/en-US/docs/Web/API/SecurityPolicyViolationEvent
- MDN, `crossOriginIsolated`: https://developer.mozilla.org/en-US/docs/Web/API/Window/crossOriginIsolated
- HSTS preload requirements: https://hstspreload.org/
- web.dev, CSP hardening: https://web.dev/articles/strict-csp
- OWASP HTTP Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Security_Cheat_Sheet.html
- OWASP Content Security Policy Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html