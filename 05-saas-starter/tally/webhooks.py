"""Billing webhook signatures, in Stripe's scheme. Pure: no I/O, the time is passed in.

Header: `Stripe-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">` (several v1
values are allowed while a secret is being rolled). The signature covers the exact bytes
received, so verify before parsing, and reject anything older than the tolerance window: a
captured request can't be replayed later.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

TOLERANCE = 300  # seconds


class SignatureError(ValueError):
    pass


class EventError(ValueError):
    pass


def compute(payload: bytes, secret: str, timestamp: int) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    return f"t={timestamp},v1={compute(payload, secret, timestamp)}"


def verify(payload: bytes, header: str | None, secret: str, now: int, tolerance: int = TOLERANCE) -> None:
    if not header:
        raise SignatureError("missing Stripe-Signature header")
    timestamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                raise SignatureError("timestamp isn't a number") from None
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("header has no timestamp or no v1 signature")
    if abs(now - timestamp) > tolerance:
        raise SignatureError(f"timestamp is outside the {tolerance}s tolerance window (possible replay)")
    expected = compute(payload, secret, timestamp)
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise SignatureError("signature doesn't match")


def parse_event(payload: bytes) -> dict[str, Any]:
    try:
        event = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EventError("body isn't JSON") from None
    if not isinstance(event, dict):
        raise EventError("event isn't an object")
    obj = (event.get("data") or {}).get("object") if isinstance(event.get("data"), dict) else None
    if not (isinstance(event.get("id"), str) and isinstance(event.get("type"), str)):
        raise EventError("event has no id or type")
    if not isinstance(event.get("created"), int) or not isinstance(obj, dict):
        raise EventError("event has no created time or data.object")
    return event
