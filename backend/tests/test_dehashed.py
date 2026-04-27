"""Unit tests for the Dehashed v2 breach collector.

Network is fully mocked via ``httpx.MockTransport`` -- no cassettes,
no live calls. We patch ``app.collectors.dehashed.client`` to inject
the mock transport and ``get_runtime`` to inject credentials.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest

from app.collectors import dehashed as dh_mod
from app.collectors.dehashed import DehashedCollector, _entry_to_finding, _mask_password
from app.schemas import SearchInput


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    async def fake_runtime() -> dict[str, str]:
        return values

    monkeypatch.setattr(dh_mod, "get_runtime", fake_runtime)


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Replace ``client()`` so it produces an AsyncClient backed by MockTransport."""
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    def fake_client(timeout: float = 15.0, **extra: Any) -> httpx.AsyncClient:
        extra.pop("headers", None)
        return httpx.AsyncClient(transport=transport, timeout=timeout)

    monkeypatch.setattr(dh_mod, "client", fake_client)
    return captured


async def _collect(coll: DehashedCollector, inp: SearchInput):
    return [f async for f in coll.run(inp)]


# --------------------------------------------------------------------------- #
# Pure unit helpers                                                           #
# --------------------------------------------------------------------------- #


def test_mask_password_short():
    assert _mask_password("") == ""
    assert _mask_password("a") == "a***"
    assert _mask_password("ab") == "a***"
    assert _mask_password("hunter2") == "hu***"


def test_entry_to_finding_masks_password_and_hashes_it():
    entry = {
        "database_name": "linkedin_2012",
        "email": "victim@example.com",
        "password": "hunter2",
        "name": "Victim",
    }
    f = _entry_to_finding("dehashed", entry)
    assert f is not None
    assert f.category == "breach"
    assert f.entity_type == "DehashedRecord"
    ev = f.payload["evidence"]
    assert ev["password_sha256"] == hashlib.sha256(b"hunter2").hexdigest()
    assert ev["password_masked"] == "hu***"
    # Critical: raw password must not appear anywhere in payload.
    flat = repr(f.payload)
    assert "hunter2" not in flat
    assert "hu***" in f.payload["value"]


def test_entry_to_finding_keeps_hash_field():
    entry = {
        "database_name": "test",
        "email": "x@y.com",
        "hashed_password": "$2y$10$abc",
        "hash_type": "bcrypt",
    }
    f = _entry_to_finding("dehashed", entry)
    assert f is not None
    ev = f.payload["evidence"]
    assert ev["hashed_password"] == "$2y$10$abc"
    assert ev["hash_algo"] == "bcrypt"
    assert "password_sha256" not in ev


def test_entry_to_finding_skips_empty():
    assert _entry_to_finding("dehashed", {}) is None


# --------------------------------------------------------------------------- #
# Collector behaviour                                                         #
# --------------------------------------------------------------------------- #


async def test_no_credentials_returns_empty(monkeypatch):
    _patch_runtime(monkeypatch, {})
    coll = DehashedCollector()
    out = await _collect(coll, SearchInput(email="x@example.com"))
    assert out == []


async def test_partial_credentials_returns_empty(monkeypatch):
    _patch_runtime(monkeypatch, {"DEHASHED_EMAIL": "me@me.com"})
    coll = DehashedCollector()
    out = await _collect(coll, SearchInput(email="x@example.com"))
    assert out == []


async def test_no_input_fields_returns_empty(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "k"},
    )
    coll = DehashedCollector()
    # SearchInput needs at least one searchable field; build one with only name.
    out = await _collect(coll, SearchInput(full_name="John Doe"))
    assert out == []


async def test_happy_path_maps_entries(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "secret"},
    )

    payload = {
        "entries": [
            {
                "database_name": "breach_a",
                "email": "victim@example.com",
                "password": "p@ssw0rd",
            },
            {
                "database_name": "breach_b",
                "email": "victim@example.com",
                "hashed_password": "deadbeef",
                "hash_type": "md5",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.host == "api.dehashed.com"
        assert request.url.path == "/v2/search"
        assert request.headers.get("Authorization", "").startswith("Bearer ")
        return httpx.Response(200, json=payload)

    captured = _patch_http(monkeypatch, handler)

    coll = DehashedCollector()
    out = await _collect(coll, SearchInput(email="victim@example.com"))

    assert len(captured) == 1
    body = captured[0].read().decode()
    assert 'email:"victim@example.com"' in body
    assert len(out) == 2
    titles = [f.title for f in out]
    assert "Dehashed: breach_a" in titles
    # Plaintext leaked password never appears in output.
    serialized = repr([f.payload for f in out])
    assert "p@ssw0rd" not in serialized
    assert "p@***" in serialized


async def test_401_returns_empty(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "bad"},
    )
    _patch_http(monkeypatch, lambda req: httpx.Response(401, json={"message": "unauthorized"}))
    out = await _collect(DehashedCollector(), SearchInput(email="x@example.com"))
    assert out == []


async def test_402_returns_empty(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "k"},
    )
    _patch_http(monkeypatch, lambda req: httpx.Response(402, json={"message": "payment required"}))
    out = await _collect(DehashedCollector(), SearchInput(email="x@example.com"))
    assert out == []


async def test_429_returns_empty(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "k"},
    )
    _patch_http(monkeypatch, lambda req: httpx.Response(429, text="slow down"))
    out = await _collect(DehashedCollector(), SearchInput(email="x@example.com"))
    assert out == []


async def test_query_combines_multiple_inputs(monkeypatch):
    _patch_runtime(
        monkeypatch,
        {"DEHASHED_EMAIL": "me@me.com", "DEHASHED_API_KEY": "k"},
    )
    captured = _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"entries": []}))
    out = await _collect(
        DehashedCollector(),
        SearchInput(email="a@b.com", username="alice", phone="+34123", domain="b.com"),
    )
    assert out == []
    body = captured[0].read().decode()
    for needle in ("a@b.com", "alice", "+34123", "b.com"):
        assert needle in body
    # OR-joined, not AND.
    assert " OR " in body
