# Password Storage

Storing passwords is the highest-value crypto decision most applications make, and the easiest to get wrong quietly. The database is eventually exfiltrated; the password hash is the last thing between that attacker and every user's account on your service and on any service where reuse applies.

## Why not a general-purpose hash

MD5, SHA-1, and SHA-256 are designed to be fast. That is exactly wrong for passwords: a GPU can compute billions of SHA-256 candidates per second, and a fast hash turns a stolen database into a cracked password list within hours. Preimage resistance is what you want, and there is no need for collision resistance at all here.

Salt is also not enough. A salt defeats rainbow tables and stops two users with the same password sharing a hash, but it does not slow an attacker down. Only a deliberately slow, memory-hard function does.

## Choosing an algorithm

| Algorithm | Notes |
| --- | --- |
| Argon2id | Current best-practice recommendation; memory-hard, resistant to both side-channel and time-memory tradeoffs. Parameterized (memory, iterations, parallelism) |
| scrypt | Memory-hard, available in `hashlib.scrypt` and in most KDF libraries. A solid choice with fewer knobs |
| bcrypt | Long established, widely deployed, well understood. Insecure compare handling in some older C implementations, a 72-byte input limit, and not memory-hard |
| PBKDF2 | Available everywhere, including in web browser APIs for client-side hashing. Not memory-hard. Acceptable, especially with a high iteration count |

Check the OWASP Password Storage Cheat Sheet for current parameter recommendations; the numbers change as hardware improves.

## Implementation

### Argon2id

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
```

### bcrypt

```python
import bcrypt


def hash_password(password: str) -> bytes:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt)


def verify_password(password: str, stored: bytes) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored)
    except ValueError:
        return False
```

### scrypt from the standard library

```python
import hashlib
import hmac
import os

N = 2 ** 14      # CPU/memory cost
R = 8            # block size
P = 1            # parallelization


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=N,
        r=R,
        p=P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"
```

`hashlib.scrypt` requires an OpenSSL build with scrypt support. Check `hashlib.scrypt` availability for your deployment platform.

### PBKDF2

```python
import hashlib
import hmac
import os

ITERATIONS = 600_000   # choose from current guidance and measure


def derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = derive(password, salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"
```

## The stored format

Store everything needed to verify and to keep verifying:

```
<algorithm>$<parameters>$<salt>$<hash>
```

- Algorithm name, so you can migrate later.
- Parameters (cost factor, iterations, memory), so you can raise them without a migration.
- Salt, random per password.
- The hash.

With a well-known library this format is handled for you. With a hand-rolled format you now own parsing, validation, and migration.

## Verification

```python
def authenticate(email: str, password: str, user_repository) -> User | None:
    user = user_repository.find_by_email(email)
    if user is None:
        # Run a verification against a dummy hash so that the response time
        # does not reveal whether the account exists.
        verify_password(password, DUMMY_HASH)
        return None

    if not verify_password(password, user.password_hash):
        return None

    # Hashes created with older parameters can be upgraded transparently.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        user_repository.save(user)

    return user
```

## Rehash on login

When you raise the work factor, existing hashes keep their old parameters and remain valid. Rehash during a successful login:

```python
from argon2 import PasswordHasher

_hasher = PasswordHasher()


def verify_and_upgrade(password: str, stored: str) -> str | None:
    """Return an upgraded hash, or None if the password is wrong."""
    try:
        _hasher.verify(stored, password)
    except Exception:
        return None
    if _hasher.check_needs_rehash(stored):
        return _hasher.hash(password)
    return None
```

This avoids a forced reset for every user, which trains people to reuse weak passwords and creates an operational spike when they all try to reset at once.

## Handling long and unusual passwords

- bcrypt truncates at 72 bytes. If you support arbitrarily long passwords with bcrypt, pre-hash with SHA-256 and a per-deployment key, and document the trade-off. Note that a per-deployment key makes hashes non-portable between environments.
- Normalize with a documented Unicode normalization form (NFKC is common) before hashing so that visually identical inputs hash identically.
- Decide a maximum length. 1024 characters is a common ceiling; the point is to bound the work an attacker can force, not to reject long passphrases.
- Never silently truncate to a length that would let two different passwords match.

## Password policies

What actually helps:

- Length over composition. A long passphrase beats `P@ssw0rd!`.
- Check against a breach list (commonly available as a password corpus or via an offline-checkable blocklist). Reject known-compromised passwords outright.
- No forced periodic rotation without evidence of compromise. Rotation drives predictable patterns and reuse.
- No user-specific complexity rules beyond length.
- Support password managers: allow paste, allow long values, do not block unusual characters.
- Rate limit and (optionally) lock out after repeated failures, per account and per source.
- Require multi-factor authentication for privileged accounts.

## Migration from a fast hash

1. Add the new hash column alongside the old one.
2. On successful login, compute the new hash and store it; clear the old value.
3. For accounts that never log in, force a reset after a communicated deadline.
4. Drop the old column.

Never keep the old fast hash around "just in case". The moment you store it, it is the weakest link.

## Related controls

- Reset tokens must be generated with a CSPRNG, stored hashed, single-use, and short-lived.
- Avoid revealing whether an account exists: identical response body and comparable timing for both the "no such user" and "wrong password" paths.
- Rate limit resets and resend-confirmation endpoints, or they become an email bomb.
- Log authentication outcomes with the source, never the password.

## References

- OWASP Password Storage Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Authentication Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html
- OWASP Forgot Password Cheat Sheet - https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- argon2-cffi - https://argon2-cffi.readthedocs.io/
- bcrypt (OpenBSD) - https://www.openbsd.org/~abieber/bcrypt.html
- RFC 9106 Argon2 - https://datatracker.ietf.org/doc/html/rfc9106
- Python `hashlib` - https://docs.python.org/3/library/hashlib.html
- NIST SP 800-63B, authenticator and password requirements - https://pages.nist.gov/800-63-3/sp800-63b.html