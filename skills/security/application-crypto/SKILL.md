---
name: Application Cryptography
description: Covers correct use of cryptography in application code: password hashing with bcrypt scrypt and Argon2, symmetric encryption with AES-GCM and nonce handling, asymmetric keys and key exchange, TLS and PKI, JWT signing algorithms and the alg none and algorithm confusion pitfalls, secret key rotation, constant-time comparison, and secure randomness. Use when the user asks about hashing passwords, encrypting data at rest, signing or verifying tokens, choosing a key size or algorithm, or debugging a cryptography failure.
metadata:
  opencode/autoinvoke: true
---

# Application Cryptography

Application cryptography is mostly a decision-making problem, not a code-writing problem. The core mental model is: pick a vetted construction from a narrow menu, generate randomness correctly, keep every key where an attacker cannot read it, and rotate before you have to. Everything else is detail that belongs in a library.

**Rule zero: never implement a cryptographic primitive yourself.** Do not write a block cipher, a hash function, a signature scheme, or a key exchange. Use a vetted library with a maintained implementation, and let it handle modes, nonces, and constant-time operations.

## When to use this

- Storing or verifying user passwords.
- Encrypting data at rest with an application-held key.
- Signing and verifying tokens, webhooks, or signed URLs.
- Choosing an algorithm, key size, or nonce strategy.
- A cryptography incident: a key leaked, a nonce reused, a hash upgraded.
- Reviewing code that calls a crypto library incorrectly, which is more common than writing one.

## The menu

Use this table to decide, then implement with the library.

| Need | Use | Do not use |
| --- | --- | --- |
| Password storage | Argon2id, scrypt, bcrypt, or PBKDF2 with tuned parameters | MD5, SHA-1, SHA-256, any fast hash, or a bare salt |
| Symmetric encryption | AES-GCM (or ChaCha20-Poly1305), 256-bit key | AES-ECB, AES-CBC without authentication, a stream cipher with a reused nonce |
| Randomness | OS CSPRNG (`os.urandom`, `secrets`, `crypto.randomBytes`) | `random`, `Math.random`, a time-based seed |
| Signing | Ed25519, ECDSA with P-256, RSASSA-PSS | Plain RSA PKCS#1 v1.5 signatures, "HMAC with the private key" |
| Key encryption | RSA-OAEP, or better, avoid it by using a KEM | RSA PKCS#1 v1.5 encryption |
| Message authentication | HMAC-SHA-256, or the AEAD tag that comes with AES-GCM | A plain checksum, a truncated hash |
| Deriving subkeys | HKDF | Reusing the master key for multiple purposes |

## Randomness

Uniform randomness is the foundation underneath everything else. A predictable nonce, key, or salt destroys the construction that uses it.

```python
import secrets

api_key = secrets.token_urlsafe(32)          # 256 bits of entropy
salt = secrets.token_bytes(16)               # for a password hash salt
nonce = secrets.token_bytes(12)              # for AES-GCM
session_id = secrets.token_hex(16)
```

```javascript
const crypto = require('node:crypto');
const apiKey = crypto.randomBytes(32).toString('base64url');
const nonce = crypto.randomBytes(12);
```

Never use `random.random()`, `Math.random()`, `uuid.uuid4()` built from a predictable source, or a timestamp. UUID version 4 is acceptable for identifiers because most modern libraries generate it from a CSPRNG; confirm yours does.

## Password hashing

Fast hashes are broken passwords. A fast hash lets an attacker try billions of candidates per second against a stolen database. A password hash must be deliberately slow.

```python
import bcrypt

def hash_password(password: str) -> bytes:
    # gensalt(rounds=...) tunes the work factor. Pick a value from current
    # guidance (OWASP Password Storage Cheat Sheet), then measure verification
    # on your own hardware and re-check it as your hardware improves.
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt)


def verify_password(password: str, stored: bytes) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored)
    except ValueError:
        return False
```

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(stored: str) -> bool:
    # Call after a successful login to upgrade hashes when parameters change.
    return _hasher.check_needs_rehash(stored)
```

Comparison with the standard library, when a KDF dependency is not available:

```python
import hashlib
import os

# Choose iterations from current guidance rather than copying a number from a
# tutorial, then verify the cost against your own hardware.
PBKDF2_ITERATIONS = 600_000


def derive(password: str, salt: bytes, iterations: int, length: int = 32) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=length)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = derive(password, salt, iterations=PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"
```

Store the algorithm, the parameters, the salt, and the output in one encoded string, so you can raise the work factor later and rehash on the next successful login. Follow the OWASP Password Storage Cheat Sheet for current parameter guidance rather than copying numbers from a blog post.

### Rules for password hashing

- One salt per password, random, at least 16 bytes. A salt is stored with the hash; that is the point of it.
- Never a global salt. One salt for every user defeats the purpose.
- Encode the parameters with the hash.
- Normalize consistently: decide on Unicode normalization (NFKC is the common choice) and apply it before hashing, so visually identical passwords produce identical hashes.
- bcrypt truncates input at 72 bytes. Pre-hash with SHA-256 and a per-deployment key if you need to support long passwords, and understand the implications before doing so.
- A pepper (a secret HMAC key held outside the database) adds defense when the database is stolen, but means a lost pepper makes every password unverifiable. Decide deliberately.
- Never log a password, and never return different responses or timings for a missing user versus a wrong password.

See `references/password-storage.md`.

## Symmetric encryption

Authenticated encryption provides confidentiality *and* integrity in one operation. Use it, and do not build an encrypt-then-MAC scheme by hand.

```python
import os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE = 32   # 256-bit AES key


class Encryptor:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24, or 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, associated_data: bytes | None = None) -> bytes:
        nonce = os.urandom(12)   # a fresh, unique nonce for every single message
        ciphertext = self._aead.encrypt(nonce, plaintext, associated_data)
        return nonce + ciphertext   # store the nonce with the ciphertext

    def decrypt(self, blob: bytes, associated_data: bytes | None = None) -> bytes:
        nonce, ciphertext = blob[:12], blob[12:]
        try:
            return self._aead.decrypt(nonce, ciphertext, associated_data)
        except InvalidTag:
            raise ValueError("ciphertext failed authentication")
```

### Nonce handling

The nonce (initialization vector) must be unique for every encryption under a given key. This is the single most dangerous rule in symmetric encryption.

- **Reusing a nonce with AES-GCM leaks the authentication key.** This is not theoretical: nonce reuse under the same key is enough to recover the GHASH key, which allows forging tags and, with enough data, decrypting other ciphertexts.
- Never derive the nonce from a counter stored next to the ciphertext without protection, and never use a fixed nonce.
- Random nonces of 96 bits are the accepted choice for GCM, because the birthday bound leaves a very wide margin. Compute your own margin from the expected number of messages under the key rather than assuming.
- If you use a counter, keep it in a single atomic location (a database sequence, a transactional increment) and never reset it when you restart.

### Modes to avoid

| Mode | Problem |
| --- | --- |
| ECB | Identical plaintext blocks produce identical ciphertext blocks. Structure leaks. Never use for structured data |
| CBC without a MAC | Malleable. An attacker can flip bits in the ciphertext to change the decrypted plaintext predictably |
| CBC with a hand-rolled MAC in the wrong order | Encrypt-then-MAC with separate keys is acceptable; the details are easy to get wrong, so prefer a single AEAD |

### Keys, not passwords, encrypt data

Derive encryption keys with HKDF, never by "stretching" a password with a fast hash. HKDF separates a master secret into independent subkeys for different purposes, so one compromised subkey does not compromise the others.

```python
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

def derive_subkey(master: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,          # distinct per purpose, e.g. b"orders-at-rest-v1"
    ).derive(master)
```

The `info` label must be different per purpose, and must change when you rotate. See `references/symmetric-and-asymmetric.md`.

## Asymmetric cryptography

Asymmetric operations are slow and bulky. They are for *keys*, not data.

| Purpose | Good choices | Notes |
| --- | --- | --- |
| Signature | Ed25519, ECDSA P-256, RSASSA-PSS with SHA-256 | Prefer Ed25519 or ECDSA where supported |
| Key transport | RSA-OAEP, or an AEAD/KEM hybrid | RSA key sizes must be large; hybrid is preferred |
| Key agreement | ECDH (X25519), or TLS 1.3's handshake | TLS does this for you; do not reimplement |

Rule of thumb: to encrypt data, generate a random data key, encrypt the data with AES-GCM, and wrap the data key for the recipient with RSA-OAEP or a KEM. That is envelope encryption, and it is how modern systems do it.

```python
import os
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

data_key = os.urandom(32)
nonce = os.urandom(12)
ciphertext = AESGCM(data_key).encrypt(nonce, b"secret payload", None)

wrapped = key.public_key().encrypt(
    data_key,
    padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ),
)

pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.BestAvailableEncryption(b"passphrase-from-a-secrets-manager"),
)
```

Generate keys with the library's generator, never from a poor random source. Validate keys on load: check the type, the curve or modulus size, and that the key is in the expected state, before using it.

Key sizes and recommended curves change. Check a current authority (NIST, OWASP, or your platform's key management documentation) rather than trusting a number in this document.

## TLS and PKI

```python
import ssl
import requests

# Verification is on by default in requests. Do not turn it off.
response = requests.get(url, timeout=10)

# For a private CA, point at the bundle rather than disabling verification.
ctx = ssl.create_default_context(cafile="/etc/ssl/certs/internal-ca.pem")
response = requests.get(url, timeout=10, verify="/etc/ssl/certs/internal-ca.pem")
```

Verification means three things, all of which matter:

- The certificate chains to a trusted root.
- It is within its validity window.
- **The hostname matches.** A certificate valid for the wrong host is a failure, not a detail.

Symptoms and causes:

| Symptom | Cause |
| --- | --- |
| "certificate verify failed: unable to get local issuer certificate" | The CA bundle is missing or wrong. Install the correct CA |
| "certificate has expired" | The server certificate is stale, or the machine clock is wrong |
| Works in the browser, fails in the service | The service store lacks the intermediate; do not disable verification to fix it |
| Intermittent failures behind a proxy | An intercepting proxy with a CA your service does not trust. Add the corporate root rather than turning verification off |

Certificate pinning prevents interception even by a trusted CA. Its cost is real: pinning to a leaf breaks on every renewal unless you pin to an intermediate or runbook a key rollover. Most services rely on correct hostname verification and private CA distribution instead.

For service-to-service mutual TLS, use short-lived certificates issued by an internal CA (which is one of the strongest reasons to run a PKI), and automate issuance and renewal.

See `references/symmetric-and-asymmetric.md` for the PKI section in full.

## JWT

A JWT is a signed JSON document. Every property of its security comes from how you verify it.

```python
import jwt
from jwt.exceptions import InvalidTokenError

def issue_token(claims: dict, private_key_pem: bytes, kid: str) -> str:
    return jwt.encode(
        {**claims, "sub": claims["user_id"]},
        private_key_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def verify_token(token: str, public_key_pem: bytes, audience: str, issuer: str) -> dict:
    # Pin the algorithm. Never let the token choose it.
    return jwt.decode(
        token,
        public_key_pem,
        algorithms=["RS256"],          # required: never omit, never accept "any"
        audience=audience,
        issuer=issuer,
        options={
            "require": ["exp", "iat", "iss", "aud", "sub"],
            "verify_signature": True,
            "verify_exp": True,
        },
    )
```

### The three JWT pitfalls

**1. `alg: none`.** A token with a header of `{"alg": "none"}` and no signature. Some libraries accept it when the key is empty. Defend by pinning the algorithm list and by using a library that rejects `none` by default. Verify your library's behavior; do not assume.

**2. Algorithm confusion.** A server configured to accept both `HS256` and `RS256` can be attacked: the attacker takes the *public* key (which is public), signs a token with `HS256` using it as the shared secret, and the server verifies the HMAC with the public key as the secret, believing it verified an RSA signature. Defend by pinning one algorithm, or by refusing to use a public key as an HMAC secret.

**3. Injected key identifiers.** `kid`, `jku`, and `x5u` instruct the verifier where to find the key. If your implementation turns `kid` into a file path, a database lookup string, or a URL fetch, an attacker controls which key verifies the token. Defend with a strict in-memory map of known key ids, no dynamic URL fetching, and no dynamic filesystem paths.

### JWT hygiene

- Validate `exp`, `nbf`, `iat`, `iss`, `aud`, and `sub`. An unverified `exp` is decoration.
- Keep access tokens short-lived (minutes). Use a rotating, revocable refresh token for sessions.
- Do not put secrets or unnecessary personal data in the payload. It is base64, not encrypted; anyone holding the token can read it.
- Decide whether you need JWT at all. If the token is only consumed by one service you control, an opaque token with a server-side session is simpler and gives you immediate revocation.
- `aud` and `iss` checks are what stop a token minted for one service from being replayed against another.

See `references/jwt-and-tokens.md`.

## Constant-time comparison

Comparing secrets with `==` leaks information through timing.

```python
import hmac
if hmac.compare_digest(submitted, stored):
    ...
```

```javascript
const crypto = require('node:crypto');
function constantTimeEqual(a, b) {
  const bufA = Buffer.from(a, 'utf8');
  const bufB = Buffer.from(b, 'utf8');
  if (bufA.length !== bufB.length) {
    // Still burn a comparison so the failure path is not obviously faster.
    crypto.timingSafeEqual(bufA, bufA);
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}
```

This applies to HMAC signature verification, API key comparison, reset tokens, and any value where an attacker gets to guess.

## Key rotation

Design for rotation before you need it. Every ciphertext and token must be attributable to a key.

- Version the data: store the key identifier alongside the ciphertext, for example `key_id || nonce || ciphertext`.
- Publish new keys before you retire old ones, and keep a key ring so both verify.
- Retire a key only after every artefact it produced has expired, re-encrypted, or been re-signed.
- Record which key was used for each artefact, so rotation is targeted rather than global.
- For JWT signing keys, use the `kid` header and keep old public keys available for at least the maximum token lifetime plus clock skew.
- Re-wrapping is cheaper than re-encrypting: envelope encryption lets you rewrap the data key under a new master key without touching the bulk data.

```python
class VersionedEncryptor:
    def __init__(self, keyring: dict[str, bytes]):
        # keyring: {"v1": b"...", "v2": b"..."} - newest key last
        self._keys = keyring

    def encrypt(self, plaintext: bytes, key_id: str) -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[key_id]).encrypt(nonce, plaintext, None)
        return key_id.encode().rjust(2, b"\x00") + nonce + ct

    def decrypt(self, blob: bytes) -> bytes:
        key_id = blob[:2].rstrip(b"\x00").decode()
        nonce, ct = blob[2:14], blob[14:]
        return AESGCM(self._keys[key_id]).decrypt(nonce, ct, None)
```

## Encoding is not encryption

- Base64, hex, and URL encoding are reversible by anyone. They are transports, not protection.
- A key stored base64-encoded in a config file is a plaintext key.
- Never "encrypt" by obfuscating, reversing, XOR-ing with a fixed key, or compressing.
- Custom ciphertext formats break libraries and lose authentication. Use a library's own serialization and store the version explicitly.

## Checklist

Before shipping anything cryptographic:

- [ ] No primitive, mode, or signature scheme implemented by hand.
- [ ] Randomness from the OS CSPRNG, nowhere else.
- [ ] Passwords hashed with Argon2id, scrypt, bcrypt, or tuned PBKDF2, with per-password salts and stored parameters.
- [ ] Symmetric encryption is an AEAD with a fresh, unique nonce per message, and the nonce is stored with the ciphertext.
- [ ] Encryption keys derived with HKDF, one purpose per `info` label.
- [ ] Signature verification pins the algorithm and validates `exp`, `nbf`, `iss`, `aud`, `sub`.
- [ ] Key identifiers resolve from a fixed map, never a path or a URL.
- [ ] Secret comparisons use a constant-time primitive.
- [ ] Key material is never in source, images, logs, or environment dumps; it lives in a secret store.
- [ ] Every artefact records which key produced it, and a rotation has been rehearsed.
- [ ] TLS verification is on everywhere, with hostname checking intact.
- [ ] Failures are logged without leaking the secret or the plaintext.

## Common mistakes

- **Storing a JWT secret as an HMAC key and then accepting RS256 too.** Symptom: attackers forge tokens as the service. Fix: pin one algorithm per service and per key.
- **Reusing a nonce with AES-GCM.** Symptom: no error, silent catastrophic key leakage. Fix: fresh nonce from the CSPRNG for every message, and never reset a counter.
- **`==` on an HMAC or an API key.** Symptom: works, is exploitable in theory. Fix: a constant-time comparison.
- **PBKDF2 with parameters copied from a 2010 article.** Symptom: verification is either painfully slow or trivially fast. Fix: tune against current guidance and your own hardware.
- **Storing a password hash without the algorithm and cost.** Symptom: you cannot upgrade the work factor without re-hashing everything. Fix: store the parameters with the hash.
- **Encrypting with a password-derived key.** Symptom: weak key material. Fix: use random keys in a secret store.
- **A key committed to git and rotated in the database only.** Symptom: the key is still in history. Fix: rotate everywhere, then rewrite history. See `secrets-management`.
- **Disabling TLS verification to fix a CI error.** Symptom: works everywhere, including for an attacker with a rogue CA. Fix: install the correct CA bundle.

## Anti-patterns

- **A homegrown "encryption" of configuration files.** XOR with a password, base64, or an obfuscated constant is a plaintext secret with extra steps.
- **Encrypting data at rest with a key stored in the same database.** The encryption then protects nothing.
- **Using AES-GCM without authenticating that the ciphertext came from you.** An attacker who can write to storage can swap a ciphertext unless it is bound with associated data or checked against a signature.
- **Signing with a symmetric secret and calling it digital signature.** A symmetric MAC proves the holder of the secret produced the message; it does not provide the non-repudiation property people expect from a signature.
- **Long-lived self-signed certificates for internal traffic.** Prefer issuance by an internal CA, or workload identity.
- **Believing "the library is safe".** The library implements the algorithm correctly. It cannot know that your key is in git or that your nonce is static.
- **Rolling your own JWT library.** Use one of the maintained implementations; the pitfalls above are exactly what they encode.

## References

- OWASP Password Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Cryptographic Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html
- OWASP Key Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html
- OWASP Cryptographic Failures - https://owasp.org/Top10/A02_2021-Cryptographic_Failures/
- OWASP Authentication Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Forgot Password Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- NIST Cryptographic algorithms and key lengths - https://csrc.nist.gov/projects/cryptographic-standards-and-guidelines
- Python `hashlib` - https://docs.python.org/3/library/hashlib.html
- Python `secrets` - https://docs.python.org/3/library/secrets.html
- Python `hmac` - https://docs.python.org/3/library/hmac.html
- Python `cryptography` (pyca) - https://cryptography.io/en/latest/
- pyca/cryptography AEAD ciphers - https://cryptography.io/en/latest/hazmat/primitives/aead/
- RFC 9106 (Argon2) - https://datatracker.ietf.org/doc/html/rfc9106
- bcrypt (OpenBSD) - https://www.openbsd.org/~abieber/bcrypt.html
- RFC 8446 TLS 1.3 - https://datatracker.ietf.org/doc/html/rfc8446
- RFC 7519 JSON Web Token (JOSE) - https://datatracker.ietf.org/doc/html/rfc7519
- RFC 7518 JSON Web Algorithms - https://datatracker.ietf.org/doc/html/rfc7518
- RFC 7515 JSON Web Signature - https://datatracker.ietf.org/doc/html/rfc7515
- PyJWT documentation - https://pyjwt.readthedocs.io/
- Node.js crypto module - https://nodejs.org/api/crypto.html