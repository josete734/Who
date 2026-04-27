"""HMAC-SHA256 signing for webhook payloads (Wave 4 / D4)."""
from __future__ import annotations

import hashlib
import hmac


def sign(body: bytes | str, secret: str) -> str:
    """Return hex HMAC-SHA256 of ``body`` with ``secret``.

    The returned value is suitable for the ``X-Who-Signature`` header
    and stable for receiver-side verification via ``hmac.compare_digest``.
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not isinstance(secret, (bytes, bytearray)):
        secret_b = secret.encode("utf-8")
    else:
        secret_b = bytes(secret)
    return hmac.new(secret_b, body, hashlib.sha256).hexdigest()


def verify(body: bytes | str, secret: str, signature: str) -> bool:
    expected = sign(body, secret)
    return hmac.compare_digest(expected, signature or "")
