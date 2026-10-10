# Symmetric and Asymmetric Cryptography

## Rule

Never implement a primitive. Pick a construction from this document, and let a vetted library perform it.

## Symmetric encryption

### Choose an AEAD

Authenticated encryption with associated data (AEAD) gives you confidentiality and integrity in one pass. Any scheme where you must encrypt and then separately compute a MAC over the ciphertext is an encrypt-then-MAC construction you would have to get right yourself. Use an AEAD unless there is a specific reason not to.

| AEAD | Notes |
| --- | --- |
| AES-GCM | Hardware-accelerated on modern CPUs (via AES-NI/CLMUL). 96-bit nonce. Well supported |
| ChaCha20-Poly1305 | Software-only environments and low-end hardware. 96-bit nonce. Constant-time in software |

Both take a 256-bit key. Both require a unique nonce per message under a given key.

### Nonce rules

This is the rule people break, and the break is silent.

- **Never reuse a nonce with AES-GCM under the same key.** Reuse leaks the GHASH authentication key, enabling tag forgery and, with enough data, decryption of other ciphertexts. The library will not warn you.
- Random 96-bit nonces from a CSPRNG are the default choice. The birthday bound is what makes this safe; if you encrypt an enormous number of messages under one key, compute your own margin.
- Counter-based nonces must be strictly increasing and must never reset, including across restarts and failovers. Persist the counter or use a database sequence with an appropriate lock.
- The nonce is not secret. Store it alongside the ciphertext, with the key identifier.

### AES-GCM with associated data

Associated data is authenticated but not encrypted. It is how you bind a ciphertext to its context, so a ciphertext copied from one row into another fails to decrypt.

```python
import os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Encryptor:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16, 24, or 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, aad: bytes | None = None) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, aad: bytes | None = None) -> bytes:
        try:
            return self._aead.decrypt(blob[:12], blob[12:], aad)
        except InvalidTag:
            raise ValueError("authentication failed")
```

### Modes to avoid

- **ECB:** identical plaintext blocks give identical ciphertext blocks; structure leaks. Never for structured data.
- **CBC without authentication:** malleable. Attacker bit-flipping changes plaintext predictably.
- **CTR or CFB without a MAC:** unauthenticated; same problem.

## Key derivation and separation

Derive purpose-specific subkeys from a master secret with HKDF. Never reuse one key for two purposes, and never derive an encryption key by stretching a password with a fast hash.

```python
import os
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_subkey(master: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,
    ).derive(master)


encryption_key = derive_subkey(master_secret, b"orders-at-rest-v1")
mac_key = derive_subkey(master_secret, b"orders-integrity-v1")
```

The `info` label is part of the contract. Change it when you rotate, so an old subkey cannot be confused with a new one.

## Envelope encryption

Encrypt bulk data with a symmetric data key; wrap the data key with a key-management service or an RSA public key. The master key never touches the data.

```
[ key_id | nonce | AES-GCM ciphertext ]        <- stored
[ data key ]                                  <- wrapped by the KMS, stored separately
```

Rotating becomes cheap: re-wrap the data key under a new master key, and leave the bulk ciphertext alone.

```python
import os
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt_with_rsa(recipient_public_pem: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    recipient = serialization.load_pem_public_key(recipient_public_pem)
    if not isinstance(recipient, rsa.RSAPublicKey):
        raise TypeError("expected an RSA public key")

    data_key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, None)
    wrapped = recipient.encrypt(
        data_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return nonce + ciphertext, wrapped
```

Check the current library documentation for the exact serialization and key-loading APIs, and for the recommended key sizes.

## Asymmetric cryptography

### Choosing

| Use | Good choices | Avoid |
| --- | --- | --- |
| Signature | Ed25519, ECDSA with P-256, RSASSA-PSS | RSA PKCS#1 v1.5 signatures ("SHA256withRSA") |
| Key transport | RSA-OAEP, or an AEAD hybrid | RSA PKCS#1 v1.5 encryption |
| Key agreement | ECDH (X25519), or the TLS handshake | Reimplementing a handshake |

Key sizes and recommended curves change. Consult a current authority (NIST SP 800-57 or your platform's KMS documentation) rather than a fixed number in this document.

### Key generation and serialization

```python
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

ed_key = ed25519.Ed25519PrivateKey.generate()
ec_key = ec.generate_private_key(ec.SECP256R1())
rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)

private_pem = ed_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.BestAvailableEncryption(b"passphrase"),
)

public_pem = ed_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
```

On load, validate before use: check the key type, the curve or modulus size, and that it is the expected state. An attacker-supplied key file is untrusted input.

```python
from cryptography.hazmat.primitives import serialization

def load_signing_key(pem: bytes) -> ed25519.Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("unexpected key type")
    return key
```

## TLS and PKI

### What verification checks

A client that verifies properly checks all of:

1. The chain builds to a trusted root.
2. Each certificate is within its validity window.
3. The hostname matches the certificate (subject alternative name).
4. Revocation, where your platform supports and requires it.

Failing to check the hostname is the most common TLS bug outside of libraries, and it removes most of the protection TLS provides.

```python
import ssl
import requests

# Verification is on by default. Do not pass verify=False.
response = requests.get(url, timeout=10)

# For a private CA, provide the bundle rather than disabling verification.
response = requests.get(url, timeout=10, verify="/etc/ssl/certs/internal-ca.pem")

# Or build a context explicitly.
ctx = ssl.create_default_context(cafile="/etc/ssl/certs/internal-ca.pem")
with ctx.wrap_socket(socket.socket(), server_hostname=host) as tls:
    tls.connect((host, 443))
```

### Common TLS failures

| Message | Cause | Fix |
| --- | --- | --- |
| `unable to get local issuer certificate` | The CA is not in the trust store | Install the correct CA bundle |
| `certificate verify failed: self signed certificate` | A private or self-signed CA | Add the CA to the trust store, or use a managed CA |
| `certificate has expired` | Stale server certificate, or wrong system clock | Renew the certificate; fix the clock |
| Works in a browser, fails in the service | Missing intermediate in the server chain, or a different trust store | Fix the chain or install the CA; never disable verification |
| Intermittent failures behind a proxy | TLS-intercepting proxy with an untrusted CA | Add the corporate root to the service trust store |

### Mutual TLS

Service-to-service mTLS proves the identity of both ends. It is only as good as the issuance and revocation process:

- Issue short-lived certificates from an internal CA rather than distributing long-lived ones.
- Automate renewal before expiry.
- Verify the peer's identity in addition to validating the chain: a certificate without a hostname check authenticates nobody in particular.
- Revoke on decommission. A certificate that outlives its workload is a credential that nobody is tracking.

### Certificate pinning

Pinning prevents interception, including by a CA you trust. Trade-offs are real:

- Pinning to a leaf certificate breaks on every renewal unless you automate the rollover.
- Pinning to an intermediate survives leaf renewal but still requires a backup pin.
- Pinning plus certificate transparency creates availability obligations.

Most services rely on correct hostname verification plus controlled CA distribution instead. If you pin, pin at the CDN layer and maintain a documented rollover runbook.

## Key rotation

Design every stored artefact to name the key that produced it.

```python
class VersionedEncryptor:
    def __init__(self, keyring: dict[str, bytes]):
        self._keys = keyring

    def encrypt(self, plaintext: bytes, key_id: str) -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[key_id]).encrypt(nonce, plaintext, None)
        return key_id.encode().ljust(4, b"\x00") + nonce + ct

    def decrypt(self, blob: bytes) -> bytes:
        key_id = blob[:4].rstrip(b"\x00").decode()
        nonce, ct = blob[4:16], blob[16:]
        return AESGCM(self._keys[key_id]).decrypt(nonce, ct, None)
```

Rotation procedure:

1. Generate the new key.
2. Publish it alongside the old one; keep a key ring that resolves both.
3. Use the new key for all new artefacts.
4. Re-wrap (cheap) or re-encrypt (expensive) existing artefacts.
5. Remove the old key only after every artefact it produced is gone or expired.

## Checklist

- [ ] AEAD only, never ECB and never unauthenticated CBC.
- [ ] Fresh CSPRNG nonce per message; nonce stored with the ciphertext.
- [ ] No nonce reuse, including across restarts.
- [ ] Subkeys derived with HKDF, one purpose per label.
- [ ] Key material in a secret store or a KMS, never in source, images, or logs.
- [ ] Key type, size, and state validated on load.
- [ ] TLS verification and hostname checking enabled everywhere.
- [ ] Every artefact records its key identifier; rotation rehearsed.
- [ ] Asymmetric operations limited to keys and signatures; bulk data stays symmetric.

## References

- OWASP Cryptographic Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html
- OWASP Key Management Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html
- OWASP TLS Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html
- pyca/cryptography documentation - https://cryptography.io/en/latest/
- pyca/cryptography AEAD - https://cryptography.io/en/latest/hazmat/primitives/aead/
- pyca/cryptography asymmetric keys - https://cryptography.io/en/latest/hazmat/primitives/asymmetric/
- NIST SP 800-57 key management - https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final
- NIST cryptographic standards and guidelines - https://csrc.nist.gov/projects/cryptographic-standards-and-guidelines
- RFC 8446 TLS 1.3 - https://datatracker.ietf.org/doc/html/rfc8446
- RFC 5869 HKDF - https://datatracker.ietf.org/doc/html/rfc5869
- Python `secrets` - https://docs.python.org/3/library/secrets.html
- Python `ssl` - https://docs.python.org/3/library/ssl.html