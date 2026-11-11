"""Passwords, tokens, CSRF and rate limiting. Standard library only, no framework imports.

- Passwords: scrypt with a random salt per password, parameters stored with the hash.
- Tokens (sessions, password resets, invites): random, and only their SHA-256 is stored, so a
  leaked database can't be replayed as live sessions.
- CSRF: a signed random cookie that every form must echo back (double submit).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD = 10
MAX_PASSWORD = 256
COMMON_PASSWORDS = frozenset(
    {
        "password123", "password1234", "1234567890", "123456789a", "qwertyuiop", "iloveyou123",
        "letmein1234", "welcome123", "admin12345", "passw0rd123", "abcdefghij", "1q2w3e4r5t",
    }
)  # fmt: skip


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(digest))
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


_DUMMY: list[str] = []


def dummy_verify(password: str) -> None:
    """Spend the same time as a real check, so a missing account can't be told apart by timing."""
    if not _DUMMY:
        _DUMMY.append(hash_password("not a real password", salt=b"\0" * 16))
    verify_password(password, _DUMMY[0])


def password_problem(password: str, email: str = "") -> str | None:
    """Why a new password isn't acceptable, or None."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "That password is too easy to guess."
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in password.lower():
        return "Don't use your email address in your password."
    return None


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_value(value: str, key: str) -> str:
    mac = hmac.new(key.encode(), value.encode(), hashlib.sha256).digest()
    return f"{value}.{_b64(mac)}"


def unsign_value(signed: str | None, key: str) -> str | None:
    if not signed or "." not in signed:
        return None
    value, _, mac = signed.rpartition(".")
    expected = _b64(hmac.new(key.encode(), value.encode(), hashlib.sha256).digest())
    return value if hmac.compare_digest(mac, expected) else None


def new_csrf(key: str) -> str:
    return sign_value(new_token(16), key)


def csrf_ok(cookie: str | None, submitted: str | None, key: str) -> bool:
    """The form's token must equal the cookie, and the cookie must carry our signature."""
    if not cookie or not submitted:
        return False
    return hmac.compare_digest(cookie, submitted) and unsign_value(cookie, key) is not None


class RateLimiter:
    """At most `limit` hits per `window` seconds per key. In memory: one process, reset on restart."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        hits, cutoff = self._hits[key], self.clock() - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.limit

    def hit(self, key: str) -> None:
        self._prune(key).append(self.clock())

    def retry_after(self, key: str) -> int:
        hits = self._prune(key)
        return max(1, int(hits[0] + self.window - self.clock()) + 1) if len(hits) >= self.limit else 0

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
