"""HMAC signing + verification tests for the webhook dispatcher."""
from __future__ import annotations

import hashlib
import hmac
import json

from app.webhooks.signing import sign, verify


def test_sign_matches_manual_hmac_sha256():
    body = b'{"event":"alert.fired","payload":{"x":1}}'
    secret = "topsecret"
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign(body, secret) == expected


def test_sign_accepts_str_body():
    body_str = '{"a":1}'
    body_bytes = body_str.encode("utf-8")
    assert sign(body_str, "k") == sign(body_bytes, "k")


def test_verify_round_trip_ok_and_tamper_fails():
    body = json.dumps({"event": "x", "payload": {"a": 1}}).encode()
    secret = "s3cret-value"
    sig = sign(body, secret)
    assert verify(body, secret, sig) is True
    # Tampered body
    assert verify(body + b"!", secret, sig) is False
    # Tampered signature
    assert verify(body, secret, sig[:-1] + ("0" if sig[-1] != "0" else "1")) is False
    # Wrong secret
    assert verify(body, "other", sig) is False


def test_verify_handles_empty_signature():
    assert verify(b"x", "s", "") is False
    assert verify(b"x", "s", None) is False  # type: ignore[arg-type]


def test_signature_is_hex_64_chars():
    sig = sign(b"hello", "k")
    assert len(sig) == 64
    int(sig, 16)  # parses as hex
