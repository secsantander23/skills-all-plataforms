# Defensive Code Patterns

Concrete patterns for the controls in the OWASP Top Ten. Language-specific libraries vary; verify the exact API in the library's documentation before copying.

## SQL injection

```python
# psycopg / PostgreSQL: placeholders are %s, parameters are passed separately.
cursor.execute(
    "SELECT id, email FROM users WHERE tenant_id = %s AND email = %s",
    (tenant_id, email),
)

# asyncpg uses $1, $2
await conn.fetch("SELECT id FROM orders WHERE customer_id = $1", customer_id)
```

```javascript
// node-postgres
const { rows } = await pool.query(
  'SELECT id, email FROM users WHERE tenant_id = $1 AND email = $2',
  [tenantId, email],
);
```

```java
// JDBC
PreparedStatement stmt = connection.prepareStatement(
    "SELECT id, email FROM users WHERE tenant_id = ? AND email = ?");
stmt.setString(1, tenantId);
stmt.setString(2, email);
ResultSet rs = stmt.executeQuery();
```

ORM escape hatches to audit:

| Library | Unsafe entry point | Safe alternative |
| --- | --- | --- |
| SQLAlchemy | `text()`, `execute()` with a built string | Bind parameters: `text("... WHERE id = :id")` with `execute(stmt, {"id": id})` |
| Django | `.raw()`, `.extra()` | `.filter()` with field lookups |
| Rails | `.where("...")`, `.find_by_sql` | `.where(id: id)`, or sanitized SQL fragments |
| Hibernate | Native queries built by concatenation | Named or positional parameters in the query |
| Sequelize / Prisma | `$queryRawUnsafe`, `$queryRaw` tagged template | The tagged-template or parameterized variant |

Identifiers (table names, column names, sort direction) cannot be parameterized. Allowlist them:

```python
SORTABLE_COLUMNS = {"created_at", "total_cents", "status"}
column = sort_field if sort_field in SORTABLE_COLUMNS else "created_at"
```

```javascript
const direction = sortDir === 'asc' ? 'ASC' : 'DESC';   // never interpolate raw input
```

## XSS and output encoding

Encode for the context, at the point of output. Prefer a template engine with autoescaping.

```python
import html

safe = html.escape(comment.body, quote=True)
```

```html
<!-- Django and Jinja2 autoescape by default. Never disable it. -->
{{ comment.body }}
```

```html
<!-- Do not mark user data safe. If you must emit raw HTML, sanitize it first. -->
<script type="module">
  const cfg = JSON.parse(document.getElementById('cfg').textContent);
</script>
<script id="cfg" type="application/json">{{ config_json }}</script>
```

JSON embedded in a `<script>` block must have `<`, `>`, and `&` escaped, or a payload containing `</script>` breaks out. Serde-derived serializers often do this escaping; check yours.

Other contexts needing different encoding: URL path segments (`urllib.parse.quote`), attribute values, JavaScript string literals (`json.dumps`), and CSS (never interpolate into CSS).

CSP is the second layer, not the fix:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'
```

## Path traversal and file handling

```python
from pathlib import Path

UPLOAD_ROOT = Path("/srv/uploads").resolve()


def resolve_upload(relative_name: str) -> Path:
    """Resolve a user-supplied name inside UPLOAD_ROOT, or reject it."""
    if not relative_name or relative_name.startswith("/") or "\x00" in relative_name:
        raise ValueError("invalid name")

    candidate = (UPLOAD_ROOT / relative_name).resolve()
    if not candidate.is_relative_to(UPLOAD_ROOT):
        raise ValueError("path escapes the upload root")

    if not candidate.is_file():
        raise FileNotFoundError(relative_name)

    return candidate
```

`is_relative_to` requires Python 3.9 or newer. On older versions, compare `os.path.commonpath` results or check `str(candidate).startswith(str(UPLOAD_ROOT) + os.sep)`.

Never derive the on-disk path from the file name alone. Generate the stored name (a UUID, a content hash) and keep the original name as a database field that is re-encoded on output.

When writing files, also check:

- Size limits enforced while streaming, not by trusting a `Content-Length` header.
- Content type determined by inspecting the bytes, not by the client's claim or the file extension.
- Storage outside the web root, or served with `Content-Disposition: attachment` and a safe content type.

## SSRF

Prefer an identifier over a URL. When a URL is unavoidable, apply every check.

```python
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"https"}
ALLOWED_HOSTS = {"api.partner.example.com"}


def is_public_ip(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(addresses)


def validate_outbound_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError("scheme not allowed")
    if parsed.username or parsed.password:
        raise ValueError("credentials in url not allowed")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("host not allowed")
    if not is_public_ip(parsed.hostname):
        raise ValueError("host resolves to a non-public address")
    return url
```

`ipaddress`'s `is_global` covers loopback, private, link-local, and reserved ranges in one check. DNS rebinding still needs defense: resolve and pin the address for the connection, or enforce the restriction at the network layer where the app cannot be bypassed.

Also enforce: timeouts, `allow_redirects=False` (or re-validate each hop), a response size cap, and no forwarding of ambient credentials.

## Deserialization

| Format | Safe | Unsafe |
| --- | --- | --- |
| JSON | `json.loads` with schema validation | Nothing here, but watch for prototype pollution in JavaScript |
| YAML | `yaml.safe_load` | `yaml.load` with an object-capable loader |
| Pickle | None | `pickle.loads` |
| XML | `defusedxml` | `xml.etree.ElementTree` on untrusted XML in older versions; Java `DocumentBuilderFactory` without entity restrictions |
| Java | JSON, protobuf, or a schema-bound format | `ObjectInputStream`, `XMLDecoder` |
| .NET | `System.Text.Json`, protobuf | `BinaryFormatter`, `NetDataContractSerializer` |
| PHP | `json_decode` | `unserialize` |

```python
# Prototype pollution in JavaScript: deep merge of untrusted JSON.
function safeMerge(target, source) {
  for (const [key, value] of Object.entries(source)) {
    if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      target[key] = safeMerge(Object.create(null), value);
    } else {
      target[key] = value;
    }
  }
  return target;
}
```

## Mass assignment

This is authorization, not injection, but it produces the same breach.

```python
# Wrong: the client can set is_admin, balance, or tenant_id.
user = User(**request.json)

# Right: an explicit allowlist of client-settable fields.
user = User(
    email=payload["email"],
    display_name=payload["display_name"],
)
```

Many frameworks have an opt-in protection. Mass assignment protection exists in Django (`ModelForm` `fields`/`exclude`), Rails (`attr_accessible` historically, strong parameters by default), and Spring MVC data binding (the `WebDataBinder` `setAllowedFields` allowlist). Enable it explicitly rather than relying on defaults.

## Constant-time comparison

```python
import hmac
hmac.compare_digest(a, b)
```

```javascript
const crypto = require('node:crypto');
crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
```

`timingSafeEqual` throws if the buffers differ in length, so hash both values to a fixed length first.

## Security headers

Set at the edge, applied to every response:

| Header | Value to consider |
| --- | --- |
| `Content-Security-Policy` | `default-src 'self'` plus explicit directives; start in report-only mode |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Frame-Options` or CSP `frame-ancestors` | Deny framing unless you need it |
| `Permissions-Policy` | Restrict unused browser capabilities |

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response
```

Assert these headers against the deployed environment in an automated check. A reverse proxy rewrite can silently drop them.

## Error handling

Never return a stack trace, a SQL fragment, a stack frame, or a file path to a client.

```python
@app.exception_handler(Exception)
async def unhandled(request, exc):
    logger.exception("unhandled error", extra={"path": request.url.path})
    return JSONResponse({"error": "internal error"}, status_code=500)
```

Log the detail server-side with a correlation id returned to the client, so support can find it without exposing internals.

## Logging and redaction

```python
class RedactingFilter(logging.Filter):
    SENSITIVE = {"password", "token", "authorization", "secret", "api_key", "card_number"}

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key.lower() in self.SENSITIVE:
                record.__dict__[key] = "[redacted]"
        return True
```

Prefer structured logs with a small set of fields over dumping whole request bodies. Log the principal, the action, the resource, the outcome, the source address, and a correlation id.

## Authentication comparison checklist

- [ ] Passwords stored with bcrypt, scrypt, or Argon2 and a per-user salt.
- [ ] Comparisons use a constant-time primitive.
- [ ] Login and reset responses do not reveal account existence.
- [ ] Rate limits exist per account and per source.
- [ ] Cookies are `Secure`, `HttpOnly`, `SameSite`.
- [ ] Session identifiers rotate on authentication and on privilege change.
- [ ] Tokens are short-lived, scoped, and revocable.

## References

- OWASP Cheat Sheet Series - https://cheatsheetseries.owasp.org/
- OWASP Cheat Sheet, SQL Injection Prevention - https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- OWASP Cheat Sheet, Cross Site Scripting Prevention - https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html
- OWASP Cheat Sheet, File Upload - https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- OWASP Cheat Sheet, Deserialization - https://cheatsheetseries.owasp.org/cheatsheets/Deserialization_Cheat_Sheet.html
- OWASP Cheat Sheet, Input Validation - https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- OWASP Cheat Sheet, Password Storage - https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Cheat Sheet, Session Management - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- Python `ipaddress` documentation - https://docs.python.org/3/library/ipaddress.html
- Python `html` module - https://docs.python.org/3/library/html.html
- CWE-22 Path Traversal - https://cwe.mitre.org/data/definitions/22.html
- CWE-79 Cross-site Scripting - https://cwe.mitre.org/data/definitions/79.html
- CWE-89 SQL Injection - https://cwe.mitre.org/data/definitions/89.html
- CWE-915 Mass Assignment - https://cwe.mitre.org/data/definitions/915.html