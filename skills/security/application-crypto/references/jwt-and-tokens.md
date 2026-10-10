# JWT and Token Security

A JWT is a JSON document wrapped in a signature. Everything about its security depends on how the *verifier* handles it, because the token itself is entirely attacker-controlled input.

## Anatomy

```
header.payload.signature
```

Each part is base64url-encoded. Decoding is trivial; nothing about the payload is protected. Only the signature authenticates the contents.

Header:

```json
{ "alg": "RS256", "typ": "JWT", "kid": "signing-2024-06" }
```

Payload (claims):

```json
{
  "sub": "cus_123",
  "iss": "https://auth.example.com",
  "aud": "https://api.example.com",
  "iat": 1717000000,
  "nbf": 1717000000,
  "exp": 1717000600,
  "jti": "01H...",
  "scope": "orders:read"
}
```

Claims that matter: `sub` (subject), `iss` (issuer), `aud` (audience), `exp` (expiry), `nbf` (not before), `iat` (issued at), `jti` (unique token id, for revocation), plus your own claims.

## Issuing

```python
import jwt


def issue_access_token(*, user_id: str, scopes: list[str], key_id: str, private_key_pem: bytes,
                       issuer: str, audience: str, lifetime_seconds: int = 600) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime_seconds,
        "jti": str(uuid.uuid4()),
        "scope": " ".join(scopes),
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256", headers={"kid": key_id})
```

Keep access tokens short-lived. Refresh tokens should be rotating, revocable, and stored server-side as a hash if you can.

## Verifying

```python
import jwt
from jwt.exceptions import InvalidTokenError


def verify(token: str, public_key_pem: bytes, *, issuer: str, audience: str) -> dict:
    return jwt.decode(
        token,
        public_key_pem,
        algorithms=["RS256"],          # pinned; never omitted, never "any"
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "nbf", "iat", "iss", "aud", "sub"]},
    )
```

`algorithms` is not optional decoration. Passing it is what stops an attacker from choosing the verification mode. Omitting it, or building it from the token's own header, reintroduces algorithm confusion.

## The three pitfalls

### 1. `alg: none`

A token with `{"alg": "none"}` and an empty signature means "trust me". Vulnerable verifiers are those that:

- Look up the algorithm from the header.
- Then look up a verification key and skip verification when the algorithm says there is none.

Defend by pinning the allowed algorithms to the ones you actually use. Verify your library's default; some reject `none` outright, others accept it only when the key is empty. Do not rely on that.

### 2. Algorithm confusion (RS256 versus HS256)

Setup: a service has an RSA key pair. The public key is published (as a JWKS document, for example). The verifier is configured to accept both `RS256` and `HS256`, and to look up whichever key the header names.

Attack: the attacker takes the public key, uses it as an HMAC secret, and signs a token with `HS256`. The verifier sees `HS256`, fetches the RSA public key, computes an HMAC with it as the shared secret, and the signature matches. The service believes it verified an RSA signature, but the attacker never had the private key.

Defend:

- Pin the algorithm. One verifier, one algorithm.
- Never use an RSA public key as an HMAC secret. If the library allows it, configure it not to.
- Separate the keys used for the two schemes so a confusion attack is not possible even if a pin is removed.

### 3. Injected key identifiers

Headers can name where the key comes from:

| Header | Attack |
| --- | --- |
| `kid` | Turned into a file path, a database query string, or a lookup into an attacker-controlled key store |
| `jku` / `x5u` | The verifier fetches a JWKS document from an attacker-controlled URL, which contains the attacker's key |
| `x5c` | A certificate chain embedded in the token, which the attacker controls |

Defend:

- Resolve `kid` from a fixed in-memory map of known key ids. A lookup miss is a rejection, never a fallback to "read from disk".
- Never fetch a JWKS URL supplied by the token. Use a configured, pinned URL, and validate the fetched document.
- Ignore embedded certificate chains unless you deliberately support them.

## Token storage on the client

| Location | Risk |
| --- | --- |
| `localStorage` | Readable by any script on the origin. A single XSS exfiltrates every token |
| `sessionStorage` | Same as localStorage, scoped to a tab |
| Cookie, `HttpOnly` + `Secure` + `SameSite` | Not readable by JavaScript. The usual choice for browser sessions |
| In-memory JavaScript variable | Not persisted; lost on reload. Needs a refresh path |

If you must use bearer tokens in a browser, keep them in memory, send them in an `Authorization` header, and accept that XSS is a total compromise. Do not put them in a URL: they land in access logs and `Referer` headers.

## Revocation

A self-contained JWT cannot be revoked before `exp`. Options:

- Short lifetimes, so revocation matters less.
- A `jti` denylist checked on every verification.
- A version or "session id" claim checked against a store on each request.
- Opaque tokens with a server-side session, when immediate revocation is a requirement.

Choose based on the requirement. "Logout must take effect immediately" and "stateless verification" are incompatible; decide which one you need.

## Refresh tokens

- Rotate on every use and detect reuse: presenting a refresh token that was already used means it was stolen, so revoke the whole token family.
- Bind refresh tokens to a device or session record.
- Store them hashed server-side.
- Keep them out of JavaScript-readable storage.

## Claims hygiene

- Do not put secrets in the payload. It is signed, not encrypted.
- Minimize personal data in the payload; it is readable by anyone who obtains the token, including every intermediary and every log that records it.
- Use short, machine-readable claims with a namespaced prefix for your own extensions, so you do not collide with registered claims.
- Validate `exp` and `nbf` with a small clock skew allowance, not zero.

## Nonces and replay

A signed token is still replayable. Bind it where replay matters:

- OIDC authorization code flow: send a `nonce` in the authorization request and verify the `nonce` claim in the ID token.
- Signed URLs and one-time tokens: store the `jti` and reject a second use.

## Do you need a JWT?

If only your own services consume the token, an opaque token with a server-side session is often better: immediate revocation, no algorithm pitfalls, no claim-validation surface. Use a JWT when the audience is distributed and stateless verification is a real requirement. Choose deliberately, and do not default to JWT because it is popular.

## Verification checklist

- [ ] `algorithms` pinned to a non-empty allowlist at every verification site.
- [ ] `iss`, `aud`, `exp`, `nbf`, `sub` all validated; `exp` required.
- [ ] Key ids resolved from a fixed map; no dynamic URL or filesystem lookup from the token.
- [ ] JWKS fetched from a configured URL only, with caching and failure handling.
- [ ] Access tokens short-lived; refresh tokens rotated and reuse-detected.
- [ ] Storage matches the threat model; `HttpOnly` cookies for browser sessions.
- [ ] No secrets or unnecessary personal data in the payload.
- [ ] Revocation path documented and implemented where required.

## References

- RFC 7519 JSON Web Token - https://datatracker.ietf.org/doc/html/rfc7519
- RFC 7515 JSON Web Signature - https://datatracker.ietf.org/doc/html/rfc7515
- RFC 7518 JSON Web Algorithms - https://datatracker.ietf.org/doc/html/rfc7518
- RFC 7517 JSON Web Key - https://datatracker.ietf.org/doc/html/rfc7517
- RFC 7520 JSON Web Token Examples - https://datatracker.ietf.org/doc/html/rfc7520
- RFC 9068 JWT Access Token Profile - https://datatracker.ietf.org/doc/html/rfc9068
- PyJWT documentation - https://pyjwt.readthedocs.io/
- python-jose documentation - https://python-jose.readthedocs.io/
- Auth0 JWT vulnerabilities - https://auth0.com/blog/critical-vulnerabilities-in-json-web-token-libraries/
- OWASP Session Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP JSON Web Token Cheat Sheet for Java - https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_for_Java_Cheat_Sheet.html