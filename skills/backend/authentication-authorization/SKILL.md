---
name: Authentication and Authorization Design
description: Covers authentication and authorization design: session cookies versus JWT, OAuth 2.1 and OpenID Connect flows, PKCE, refresh token rotation and reuse detection, token storage tradeoffs, RBAC versus ABAC, scope enforcement, session fixation defense, and common auth flaws such as CSRF and token leakage. Use when the user asks to implement login, choose between sessions and JWTs, add OAuth or OIDC, wire PKCE, design roles and permissions, or fix an auth vulnerability.
metadata:
  opencode/autoinvoke: true
---

# Authentication and Authorization Design

Authentication answers "who is this?" and authorization answers "may they do this?". Conflating the two produces systems that authenticate correctly and then leak data. Design them separately: authentication establishes an identity with a lifetime, authorization is a policy evaluated on every request against a resource, a subject, and an action.

## When to use this

- Choosing between server-side sessions and self-contained tokens (JWT).
- Implementing login, logout, session issuance, or session revocation.
- Building or integrating an OAuth 2.1 authorization server or client, including PKCE.
- Designing refresh token rotation and detecting token replay.
- Deciding where to store tokens in a browser, native app, or service.
- Choosing RBAC, ABAC, or a hybrid, and enforcing permissions in code.
- Fixing CSRF, session fixation, token leakage, or privilege-escalation bugs.
- Scoping API access and validating scope at the resource server.

## Quick start

A server-side session implementation with CSRF protection, rotation on privilege change, and a permission check:

```ts
import { createHmac, randomBytes, timingSafeEqual, scryptSync } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { parse as parseCookie, serialize as serializeCookie } from "cookie";

const SESSION_COOKIE = "__Host-session";
const CSRF_COOKIE = "__Host-csrf";
const SESSION_TTL_MS = 30 * 60 * 1000; // 30 minutes of inactivity

type Session = {
  id: string;
  userId: string;
  csrfToken: string;
  createdAt: number;
  lastSeenAt: number;
  absoluteExpiry: number;
};

declare const store: {
  create(s: Session): Promise<void>;
  get(id: string): Promise<Session | undefined>;
  destroy(id: string): Promise<void>;
  destroyAllForUser(userId: string): Promise<void>;
};

function newId(): string {
  return randomBytes(32).toString("base64url");
}

// --- Password storage ----------------------------------------------------
// Argon2id is the preferred choice; scrypt is acceptable where Argon2 is not
// available in your runtime. Never store reversible passwords.
function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const key = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
  return `scrypt$N=16384,r=8,p=1$${salt.toString("base64url")}$${key.toString("base64url")}`;
}

function verifyPassword(password: string, stored: string): boolean {
  const [scheme, params, saltB64, hashB64] = stored.split("$");
  if (scheme !== "scrypt") return false;
  const [N, r, p] = params.replace(/^N=/, "").split(",");
  const salt = Buffer.from(saltB64, "base64url");
  const expected = Buffer.from(hashB64, "base64url");
  const actual = scryptSync(password, salt, expected.length, {
    N: Number(N),
    r: Number(r),
    p: Number(p),
  });
  // Constant-time compare: a length or byte mismatch must not leak timing.
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

// --- Cookies -------------------------------------------------------------
function sessionCookie(value: string, maxAge: number): string {
  // __Host- prefix enforces Secure, no Domain, and Path=/ by conforming UAs.
  return serializeCookie(SESSION_COOKIE, value, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: Math.floor(maxAge / 1000),
  });
}

function csrfCookie(value: string): string {
  // Readable by JS on purpose: the client copies it into a request header.
  // It is not a secret from the browser, only from cross-site attackers.
  return serializeCookie(CSRF_COOKIE, value, {
    httpOnly: false,
    secure: true,
    sameSite: "lax",
    path: "/",
  });
}

// --- Middleware ----------------------------------------------------------
async function authenticate(req: IncomingMessage): Promise<Session | null> {
  const cookies = parseCookie(req.headers.cookie ?? "");
  const sid = cookies[SESSION_COOKIE];
  if (!sid) return null;

  const session = await store.get(sid);
  if (!session) return null;

  const now = Date.now();
  if (now > session.absoluteExpiry || now - session.lastSeenAt > SESSION_TTL_MS) {
    await store.destroy(sid);
    return null;
  }

  session.lastSeenAt = now;
  return session;
}

function requireCsrf(req: IncomingMessage, session: Session): boolean {
  const header = req.headers["x-csrf-token"];
  if (typeof header !== "string") return false;
  const a = Buffer.from(header);
  const b = Buffer.from(session.csrfToken);
  return a.length === b.length && timingSafeEqual(a, b);
}

// --- Permission model ----------------------------------------------------
type Permission = `${string}:${string}:${string}`; // subject:action:resource

declare const rolePermissions: Record<string, Permission[]>;

function can(user: { id: string; roles: string[] }, action: string, resource: string): boolean {
  const allowed = user.roles.flatMap((r) => rolePermissions[r] ?? []);
  const want = `user:${action}:${resource}` as Permission;
  // A wildcard role grants admin without a hard-coded "isAdmin" branch.
  const admin: Permission = "user:*:*";
  return allowed.includes(want) || allowed.includes(admin);
}

declare const db: { findUserByEmail(email: string): Promise<{ id: string; roles: string[]; passwordHash: string } | null> };

// --- Routes --------------------------------------------------------------
const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");

  if (url.pathname === "/login" && req.method === "POST") {
    const body = await readJson(req);
    const user = await db.findUserByEmail(String(body.email ?? ""));
    // Same response and comparable timing whether or not the user exists.
    if (!user || !verifyPassword(String(body.password ?? ""), user.passwordHash)) {
      return json(res, 401, { error: "invalid_credentials" });
    }

    const session: Session = {
      id: newId(),
      userId: user.id,
      csrfToken: newId(),
      createdAt: Date.now(),
      lastSeenAt: Date.now(),
      absoluteExpiry: Date.now() + 8 * 60 * 60 * 1000, // hard 8h cap
    };
    await store.create(session);

    res.setHeader("Set-Cookie", [sessionCookie(session.id, SESSION_TTL_MS), csrfCookie(session.csrfToken)]);
    return json(res, 200, { ok: true });
  }

  if (url.pathname === "/logout" && req.method === "POST") {
    const session = await authenticate(req);
    if (!session) return json(res, 204, null);
    // Session fixation defense: a new session id must be issued on login.
    // Logout destroys server state and clears the cookie.
    await store.destroy(session.id);
    res.setHeader("Set-Cookie", sessionCookie("", 0));
    return json(res, 204, null);
  }

  if (url.pathname === "/admin" && req.method === "GET") {
    const session = await authenticate(req);
    if (!session) return json(res, 401, { error: "unauthenticated" });
    if (!can({ id: session.userId, roles: ["user"] }, "read", "admin")) {
      return json(res, 403, { error: "forbidden" });
    }
    // SameSite=Lax already blocks cross-site POSTs; verify the token too so
    // that SameSite=None deployments remain protected.
    if (!requireCsrf(req, session)) return json(res, 403, { error: "csrf" });
    return json(res, 200, { ok: true });
  }

  json(res, 404, { error: "not_found" });
});

server.listen(8080, () => console.log("auth on :8080"));

function json(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(body === null ? "" : JSON.stringify(body));
}

declare function readJson(req: IncomingMessage): Promise<Record<string, unknown>>;
```

The above shows the shape: password hashing with a memory-hard KDF, an opaque server-side session id in an `HttpOnly`/`Secure`/`SameSite` cookie, a double-submit CSRF token, constant-time comparison, uniform failure responses, and per-resource permission checks.

## Sessions versus tokens

This is the first decision and it is largely settled by where the code runs.

| | Server-side sessions | Self-contained tokens (JWT) |
| --- | --- | --- |
| Revocation | immediate (delete the row) | hard (until expiry, or a denylist) |
| Lookup per request | one store read | local signature verification |
| Payload size | cookie holds only an id | token carries claims, grows with them |
| Horizontal scaling | needs a shared store | stateless |
| Trust boundary | server owns state | whoever holds the token is the principal |
| Browser storage | `HttpOnly` cookie | `HttpOnly` cookie works; `localStorage` does not |

Default recommendation:

- **Browser sessions**: server-side session with an opaque id in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Revocation is a requirement you cannot opt out of (sign-out must actually end access).
- **Service-to-service**: short-lived signed tokens (JWT or Paseto) with a small TTL, verified locally.
- **Mobile and third-party**: OAuth with PKCE (see below).

Two failure modes drive this:

1. **JWT in `localStorage`.** Any XSS exfiltrates a token that works until expiry, with no server-side kill switch. Symptom: a single XSS becomes a persistent account compromise, and you cannot force-logout the attacker.
2. **Stateless JWT with no revocation.** "Sign out" only clears the cookie; a stolen token remains valid for its whole lifetime. Fix: keep access-token TTL short (minutes) and use refresh tokens with rotation and server-side revocation.

If you must use JWTs, add: short `exp`, an `iss` and `aud` you actually verify, a `jti` you can denylist, and a key id (`kid`) so rotation is possible. Never accept `alg: none` and never trust the token's `alg` header to select verification:

```ts
import { jwtVerify, createRemoteJWKSet } from "jose";

const JWKS_URL = new URL(process.env.JWKS_URL!);
const jwks = createRemoteJWKSet(JWKS_URL, { cacheMaxAge: 600_000, timeoutRank: 5_000 });

export async function verifyAccessToken(token: string) {
  // The algorithm list is pinned by you, not read from the token.
  return jwtVerify(token, jwks, {
    issuer: "https://issuer.example.com",
    audience: "https://api.example.com",
    algorithms: ["RS256", "ES256"],
    clockTolerance: 5,
  });
}
```

## OAuth 2.1 and OpenID Connect

Use OAuth 2.1 for delegated authorization and OpenID Connect when the client also needs to know *who* the user is. Do not build your own.

| Grant | Use when | Notes |
| --- | --- | --- |
| Authorization code + PKCE | Web apps, SPAs, native and mobile apps | The only grant OAuth 2.1 recommends for these. |
| Client credentials | Your own service acting as itself, no user | Machine-to-machine; issues a token with no user subject. |
| Refresh token | Long-lived access to a consented scope | Rotate on every use. |
| Resource owner password | Effectively never | Deprecated in OAuth 2.1; do not implement. |
| Implicit | Never | Removed in OAuth 2.1; tokens in URL fragments are leaky. |

### PKCE

PKCE (RFC 7636) binds the authorization request to the token request. The client generates a high-entropy `code_verifier`, sends its SHA-256 hash as `code_challenge` with `code_challenge_method=S256`, and proves possession of the verifier when exchanging the code. It removes the need for a client secret in public clients, where it cannot be kept secret anyway.

```ts
import { createHash, randomBytes } from "node:crypto";

function pkcePair() {
  const codeVerifier = randomBytes(32).toString("base64url"); // 43 chars, high entropy
  const codeChallenge = createHash("sha256").update(codeVerifier).digest("base64url");
  return { codeVerifier, codeChallenge };
}
```

Server side, at the authorization server. The first message builds and stores the challenge; the second verifies it at token exchange:

```ts
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

// --- at /authorize: store the challenge keyed by the authorization code ---
function storeChallenge(code: string, challenge: string, method: string): void {
  issuedCodes.set(code, {
    challenge,
    method,
    redirectUri: pendingRedirectUri,
    expiresAt: Date.now() + 60_000,
  });
}

// --- at /token: verify the verifier against the stored challenge ---
function verifyPkce(issued: { challenge: string; method: string }, verifier: string): boolean {
  if (issued.method !== "S256") return false;
  const computed = createHash("sha256").update(verifier).digest("base64url");
  const a = Buffer.from(computed);
  const b = Buffer.from(issued.challenge);
  return a.length === b.length && timingSafeEqual(a, b);
}

function mintCodeChallenge(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}
```

Use `S256` only. `plain` exists for legacy clients and provides no protection against an attacker who can observe the authorization request.

### Redirect URIs

Exact string matching, no wildcards, no prefix matching. An open or partially-matched redirect URI lets an attacker steal the authorization code. Compare the full string and reject any mismatch.

### Refresh token rotation and reuse detection

Rotation means each refresh token is single-use. On every refresh you issue a new refresh token and invalidate the old one. If an already-used refresh token is presented again, assume theft: revoke the entire token family for that session and force re-authentication.

```ts
type RefreshRecord = { familyId: string; userId: string; used: boolean; expiresAt: number; scopes: string[] };
declare const refreshStore: {
  get(id: string): Promise<RefreshRecord | undefined>;
  markUsedAndRotate(id: string, next: RefreshRecord): Promise<void>;
  revokeFamily(familyId: string): Promise<void>;
};

export async function refresh(refreshToken: string) {
  const record = await refreshStore.get(refreshToken);
  if (!record || record.expiresAt < Date.now()) throw new Error("invalid refresh token");

  if (record.used) {
    // Replay of a consumed token: assume the token leaked. Kill the family.
    await refreshStore.revokeFamily(record.familyId);
    throw new Error("refresh token reuse detected; session revoked");
  }

  const nextId = newId();
  await refreshStore.markUsedAndRotate(refreshToken, {
    familyId: record.familyId,
    userId: record.userId,
    used: false,
    expiresAt: Date.now() + 30 * 24 * 60 * 60 * 1000,
    scopes: record.scopes,
  });

  return { accessToken: mintAccessToken(record), refreshToken: nextId };
}
```

Binding refresh tokens to a client-supplied key (DPoP, RFC 9449) further limits a stolen token's usefulness, but rotation plus reuse detection covers the common case.

### Token storage

| Client | Store | Why |
| --- | --- | --- |
| Browser app | `HttpOnly` `Secure` `SameSite` cookie | JS cannot read it, so XSS cannot steal it. Requires CSRF protection. |
| SPA using bearer tokens | memory only, never `localStorage` | `localStorage` survives XSS; memory does not (it costs a refresh on reload, which refresh tokens solve). |
| Native/mobile | OS keychain / keystore | Encrypted at rest, backed up per platform policy. |
| Backend service | secret store + memory | Never in source, never in an env var checked into a repo. |

`SameSite=Lax` blocks cross-site POSTs from carrying cookies and is the single highest-value CSRF defense. `SameSite=None` requires `Secure` and re-opens the CSRF problem, which is why double-submit tokens and `Origin` checks exist.

## Authorization models

| Model | Shape | Strengths | Weaknesses |
| --- | --- | --- | --- |
| RBAC | roles grant permissions | Simple, familiar, easy to audit | Role explosion; cannot express "only their own records". |
| ABAC | policies over attributes | Expressive, handles ownership and context | Harder to reason about and test; needs a good policy engine. |
| ReBAC | relationships (graph) | Natural for sharing and org hierarchies | Requires a relationship store and graph traversal. |
| Hybrid | RBAC baseline plus ABAC exceptions | Covers most real systems | Two places to look for a permission |

Start with RBAC for coarse capabilities, then add an ownership or attribute check for object-level rules. A hybrid like "role permits the action, and the row's tenant matches the caller's tenant" covers most applications without a policy engine.

Check authorization at the resource, not just the route:

```ts
// WRONG: role check only. Any `editor` can read every order in every tenant.
if (can(user, "read", "order")) return repo.findById(id);

// RIGHT: role check plus object-level constraint, inside the data access layer.
const order = await repo.findById(id);
if (!order) return 404;                                   // do not confirm existence
if (!can(user, "read", "order")) return 403;
if (order.tenantId !== user.tenantId) return 404;          // hide cross-tenant existence
```

Enforcement belongs where the row is loaded, so a new endpoint cannot forget the check. Doing it in middleware gives you a check that every handler must remember to opt into.

Scope enforcement at the resource server: a resource server that trusts the client's claim of scope without verifying the token is not enforcing anything. Verify the JWT signature, issuer, audience, expiry, and the required scope per operation, and reject with `403` and an `insufficient_scope` error when the scope is missing.

## Common auth flaws

| Flaw | Mechanism | Fix |
| --- | --- | --- |
| Session fixation | Attacker sets a victim's session id before login; victim authenticates on that id | Issue a new session id on login and on privilege change |
| Credential stuffing | Automated reuse of breached passwords | MFA, rate limit per account and per IP, breached-password checks, CAPTCHA after failures |
| CSRF | Cross-site request reuses the victim's ambient cookie | `SameSite=Lax` cookies, double-submit token, `Origin` verification, no GET side effects |
| Token in URL | Referrers, logs, and browser history leak query-string tokens | Never put tokens in URLs; authorization code flow only |
| Open redirect on login | `?next=` points at an attacker host | Allowlist redirect targets to relative paths or a known host list |
| JWT not verified | Attacker forges an unsigned or `alg:none` token | Verify signature, pin algorithms, verify `iss`, `aud`, `exp` |
| Missing `aud` check | A token minted for another service is accepted | Always verify `aud` against your own identifier |
| Timing leak on token compare | Byte-by-byte compare reveals the value | Constant-time compare (`timingSafeEqual`, `hmac.compare_digest`) |
| Password reset token reuse | Reset link works twice | Single-use token, short expiry, invalidate on use, notify on change |
| Enumeration via login | Different message or timing for unknown user | Uniform message and comparable timing |
| JWT stored in localStorage | Any XSS exfiltrates a durable credential | `HttpOnly` cookie or in-memory storage |
| No rate limit on token endpoint | Password or token brute force | Per-account, per-IP, and per-client-id limits |

## Checklist

- [ ] Passwords stored with Argon2id, scrypt, or bcrypt with per-user salt; never reversible.
- [ ] Login responses are uniform in message and timing for existing and non-existing accounts.
- [ ] Session id rotated on login and on privilege change (fixation defense).
- [ ] Session cookies are `HttpOnly`, `Secure`, `SameSite=Lax` (or `Strict`), and `Path=/`.
- [ ] Absolute session lifetime capped, not just an idle timeout.
- [ ] State-changing requests require a CSRF token in addition to `SameSite`.
- [ ] Authorization checked per resource with the row loaded, including tenant or ownership constraints.
- [ ] Cross-tenant lookups return `404`, not `403`, so IDs cannot be enumerated.
- [ ] JWT verification pins algorithms and checks `iss`, `aud`, and `exp`; `alg: none` rejected.
- [ ] Access tokens short-lived; refresh tokens rotated with reuse detection that revokes the family.
- [ ] Tokens never placed in URLs, logs, or error messages.
- [ ] Login, token, and password-reset endpoints are rate limited per account and per source.
- [ ] Redirect URIs compared exactly; login `next` parameter allowlisted.
- [ ] Multi-factor authentication available for privileged roles and enforced by policy, not just offered.

## Common mistakes

- **Session id not regenerated on login.** Symptom: an attacker who plants a cookie before the victim logs in inherits the authenticated session. Fix: always mint a new session id at authentication and at privilege elevation.
- **`403` where `404` belongs.** Symptom: an attacker enumerates valid IDs by distinguishing `403` from `404`. Fix: return the same status for "does not exist" and "not yours".
- **JWT verification reads `alg` from the token.** Symptom: sending `alg: none` or switching to an asymmetric algorithm the server has the public key for bypasses validation. Fix: pin the allowed algorithms in your verifier configuration.
- **`aud` not verified.** Symptom: a token you minted for an internal service is accepted by the public API because the signature is valid. Fix: require your own audience value.
- **Refresh tokens never rotated.** Symptom: one leaked refresh token grants indefinite access with no detection. Fix: rotate on every use and treat reuse of a consumed token as compromise.
- **Rate limit only on password, not on the token endpoint.** Symptom: attackers brute-force client secrets or refresh tokens, which the login limiter does not see. Fix: rate limit each endpoint separately.
- **Authorization only in route middleware.** Symptom: a new endpoint forgets the check and exposes data. Fix: enforce in the data-access layer or with a declarative policy the handler cannot skip.
- **Uniform login error but non-uniform timing.** Symptom: an enumeration attack still works because the "no such user" path skips the password hash. Fix: always perform a hash comparison, against a dummy hash when the user is absent.
- **CSRF protection disabled for JSON APIs by assumption.** Symptom: if any endpoint accepts form-encoded bodies or the cookie is `SameSite=None`, cross-site POSTs succeed. Fix: `SameSite` plus a token plus `Origin` checks, applied uniformly.

## Anti-patterns

- **Rolling your own authentication protocol.** Symptom: subtle flaws in signature handling, replay protection, or key exchange. Fix: use an established library and, better, an established identity provider.
- **Password grant for your own users.** Symptom: the app becomes an open password oracle that must be brute-force hardened and is directly phishingable. Deprecated in OAuth 2.1. Fix: authorization code with PKCE.
- **Implicit flow** (`response_type=token`). Symptom: access tokens land in URL fragments, leaking through history, referrers, and error logs. Removed in OAuth 2.1. Fix: code flow with PKCE.
- **Long-lived "remember me" token that never expires and cannot be revoked.** Symptom: a single leak is permanent access. Fix: bounded absolute expiry plus server-side revocation, and re-authenticate for sensitive actions.
- **Storing roles in a JWT and trusting them forever.** Symptom: an admin removed from the database keeps admin access until the token expires. Fix: short TTL, or a version/epoch claim checked against the database, or a denylist.
- **Roles named after job functions with baked-in resource scope** (`regional_admin`, `only_reports`). Symptom: every new exception adds a role, and after a year nobody can say what `support_tier_2` can do. Fix: permissions plus attribute checks, and keep role definitions few and auditable.
- **Authorization decided by a client-supplied tenant or user id.** Symptom: changing `X-Tenant-Id` gives access to another tenant's data. Fix: derive the tenant and subject from the verified token or session, never from a request header.
- **Comparing tokens with `===` or a byte loop.** Symptom: timing differences leak the value byte by byte. Fix: constant-time compare.

## References

- RFC 6749, The OAuth 2.0 Authorization Framework — https://www.rfc-editor.org/rfc/rfc6749.html
- RFC 7636, Proof Key for Code Exchange (PKCE) — https://www.rfc-editor.org/rfc/rfc7636.html
- RFC 8252, OAuth 2.0 for Native Apps — https://www.rfc-editor.org/rfc/rfc8252.html
- RFC 9449, OAuth 2.0 Demonstrating Proof of Possession (DPoP) — https://www.rfc-editor.org/rfc/rfc9449.html
- RFC 9126, Pushed Authorization Requests (PAR) — https://www.rfc-editor.org/rfc/rfc9126.html
- RFC 8705, mTLS for OAuth 2.0 — https://www.rfc-editor.org/rfc/rfc8705.html
- RFC 7009, Token Revocation — https://www.rfc-editor.org/rfc/rfc7009.html
- RFC 7662, Token Introspection — https://www.rfc-editor.org/rfc/rfc7662.html
- RFC 8414, Authorization Server Metadata — https://www.rfc-editor.org/rfc/rfc8414.html
- RFC 8693, Token Exchange — https://www.rfc-editor.org/rfc/rfc8693.html
- OAuth 2.1 (IETF draft, current) — https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/
- OAuth 2.0 Security Best Current Practice (RFC 9700) — https://www.rfc-editor.org/rfc/rfc9700.html
- RFC 7519, JSON Web Token (JWT) — https://www.rfc-editor.org/rfc/rfc7519.html
- RFC 8725, JWT Best Current Practices — https://www.rfc-editor.org/rfc/rfc8725.html
- RFC 6750, Bearer Token Usage — https://www.rfc-editor.org/rfc/rfc6750.html
- RFC 9207, OAuth 2.0 Authorization Server Issuer Identification — https://www.rfc-editor.org/rfc/rfc9207.html
- OpenID Connect Core 1.0 — https://openid.net/specs/openid-connect-core-1_0.html
- OWASP Authentication Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP Authorization Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- OWASP JSON Web Token Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html
- OWASP OAuth2 Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/OAuth2_Cheat_Sheet.html
- OWASP Password Storage Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Credential Stuffing Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Credential_Stuffing_Prevention_Cheat_Sheet.html
- OWASP CSRF Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP IDOR Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html
- OWASP Multifactor Authentication Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html
- OWASP Key Management Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html