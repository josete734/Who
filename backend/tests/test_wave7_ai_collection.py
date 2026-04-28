"""Tests for Wave 7 — IA-augmented collection.

Covers:

* ``netfetch.jina.fetch_markdown`` — happy path, cache hit, network error,
  bad status, no-redis fallback (cache-less mode).
* ``collectors._ai_parse.llm_parse_text`` — strict-JSON contract:
  parses a clean response, rejects garbage, returns None on LLM error.
* ``llm.query_expansion`` — deterministic fallback covers the basics,
  cache hit returns stored result, LLM failure ⇒ fallback, LLM success ⇒
  merged list (LLM-first, fallback dedupe).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel

from app.collectors import _ai_parse as ai_parse_mod
from app.llm import query_expansion as qe_mod
from app.netfetch import jina as jina_mod
from app.netfetch.jina import cache_key, fetch_markdown


# ---------------------------------------------------------------------------
# Tiny in-memory Redis stand-in (matches the subset used by W7).
# ---------------------------------------------------------------------------
class _FakeRedis:
    def __init__(self):
        self._kv: dict[str, tuple[str, int | None]] = {}

    async def get(self, key):
        v = self._kv.get(key)
        return v[0] if v else None

    async def setex(self, key, ttl, value):
        self._kv[key] = (str(value), int(ttl))

    async def set(self, key, value):
        self._kv[key] = (str(value), None)


# ---------------------------------------------------------------------------
# jina.fetch_markdown
# ---------------------------------------------------------------------------
def test_jina_cache_key_is_stable_per_url():
    a = cache_key("https://example.com/x")
    b = cache_key("https://example.com/x")
    c = cache_key("https://example.com/y")
    assert a == b
    assert a != c
    assert a.startswith("jina:md:")


@pytest.mark.asyncio
async def test_jina_fetch_returns_markdown_on_200():
    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            assert url.startswith(jina_mod.JINA_BASE)
            return httpx.Response(200, text="# Hello\n\nWorld")

    out = await fetch_markdown(
        "https://example.com/page", redis=None, client_factory=lambda: _Stub()
    )
    assert out is not None
    assert out.startswith("# Hello")


@pytest.mark.asyncio
async def test_jina_fetch_returns_none_on_non_200():
    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(503, text="upstream error")

    out = await fetch_markdown(
        "https://example.com/page", redis=None, client_factory=lambda: _Stub()
    )
    assert out is None


@pytest.mark.asyncio
async def test_jina_fetch_returns_none_on_network_error():
    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("dns down")

    out = await fetch_markdown(
        "https://example.com/page", redis=None, client_factory=lambda: _Stub()
    )
    assert out is None


@pytest.mark.asyncio
async def test_jina_cache_hit_skips_network():
    """When Redis already has the value, no network call is issued."""
    r = _FakeRedis()
    url = "https://example.com/cached"
    await r.setex(cache_key(url), 60, "# Cached body")

    factory_calls = {"count": 0}

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            factory_calls["count"] += 1
            return httpx.Response(200, text="# Live body")

    out = await fetch_markdown(url, redis=r, client_factory=lambda: _Stub())
    assert out == "# Cached body"
    assert factory_calls["count"] == 0  # never hit the network


@pytest.mark.asyncio
async def test_jina_cache_miss_writes_and_returns_body():
    r = _FakeRedis()
    url = "https://example.com/uncached"

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(200, text="# Fresh content")

    out = await fetch_markdown(url, redis=r, client_factory=lambda: _Stub())
    assert out == "# Fresh content"
    cached = await r.get(cache_key(url))
    assert cached == "# Fresh content"


# ---------------------------------------------------------------------------
# _ai_parse.llm_parse_text
# ---------------------------------------------------------------------------
class _ProfileSchema(BaseModel):
    name: str
    bio: str | None = None
    follower_count: int | None = None


@pytest.mark.asyncio
async def test_ai_parse_text_extracts_clean_json(monkeypatch):
    async def fake_llm(llm, prompt):
        return (
            '{"name": "Jane Doe", "bio": "Madrid runner", "follower_count": 142}',
            "gemini-fake",
        )

    monkeypatch.setattr(ai_parse_mod, "_llm_call_real", fake_llm, raising=True)

    out = await ai_parse_mod.llm_parse_text(
        "Some markdown body", _ProfileSchema, hint="extract profile"
    )
    assert out is not None
    assert out.name == "Jane Doe"
    assert out.bio == "Madrid runner"
    assert out.follower_count == 142


@pytest.mark.asyncio
async def test_ai_parse_text_returns_none_on_garbage(monkeypatch):
    async def fake_llm(llm, prompt):
        return ("nope, no JSON to be found anywhere here", "gemini-fake")

    monkeypatch.setattr(ai_parse_mod, "_llm_call_real", fake_llm, raising=True)

    out = await ai_parse_mod.llm_parse_text(
        "body", _ProfileSchema, hint="x"
    )
    assert out is None


@pytest.mark.asyncio
async def test_ai_parse_text_returns_none_on_schema_violation(monkeypatch):
    async def fake_llm(llm, prompt):
        # Missing the required 'name' field → Pydantic ValidationError.
        return ('{"bio": "no name"}', "gemini-fake")

    monkeypatch.setattr(ai_parse_mod, "_llm_call_real", fake_llm, raising=True)

    out = await ai_parse_mod.llm_parse_text(
        "body", _ProfileSchema, hint="x"
    )
    assert out is None


@pytest.mark.asyncio
async def test_ai_parse_text_returns_none_on_llm_error(monkeypatch):
    async def fake_llm(llm, prompt):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_parse_mod, "_llm_call_real", fake_llm, raising=True)

    out = await ai_parse_mod.llm_parse_text(
        "body", _ProfileSchema, hint="x"
    )
    assert out is None


@pytest.mark.asyncio
async def test_ai_parse_text_empty_input_returns_none(monkeypatch):
    out = await ai_parse_mod.llm_parse_text("", _ProfileSchema, hint="x")
    assert out is None


def test_ai_parse_confidence_is_below_native_parser():
    # Native happy paths typically emit ≥0.85; the LLM fallback must not
    # outrank them.
    assert ai_parse_mod.AI_PARSE_CONFIDENCE < 0.85


# ---------------------------------------------------------------------------
# query_expansion
# ---------------------------------------------------------------------------
class _Subject:
    def __init__(self, **kw):
        for k in ("full_name", "city", "country", "email", "username", "phone", "domain"):
            setattr(self, k, kw.get(k))


def test_deterministic_fallback_includes_basics():
    subj = _Subject(full_name="Juan García", city="Madrid", country="ES")
    out = qe_mod.deterministic_fallback(subj)
    assert "Juan García" in out
    # Quoted variant for exact-phrase searching.
    assert any('"Juan García"' in q for q in out)
    # City-augmented variant.
    assert any("Madrid" in q for q in out)
    assert all(isinstance(q, str) and q for q in out)
    assert len(out) <= qe_mod.MAX_QUERIES


def test_deterministic_fallback_no_data_returns_empty():
    out = qe_mod.deterministic_fallback(_Subject())
    assert out == []


def test_deterministic_fallback_dedupes():
    subj = _Subject(full_name="Juan García")
    out = qe_mod.deterministic_fallback(subj)
    assert len(out) == len(set(out))


def test_cache_key_is_stable_per_input():
    a = qe_mod.cache_key(_Subject(full_name="X", city="Madrid"))
    b = qe_mod.cache_key(_Subject(full_name="X", city="Madrid"))
    c = qe_mod.cache_key(_Subject(full_name="X", city="Barcelona"))
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_expand_queries_falls_back_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(qe_mod, "_llm_call_real", None, raising=True)
    subj = _Subject(full_name="Juan García", city="Madrid")
    out = await qe_mod.expand_queries(subj, redis=None)
    assert out, "fallback must yield at least the deterministic queries"
    assert "Juan García" in out


@pytest.mark.asyncio
async def test_expand_queries_uses_llm_and_merges_with_fallback(monkeypatch):
    async def fake_llm(llm, prompt):
        return (
            json.dumps([
                "Juan García abogado Madrid",
                "site:linkedin.com Juan García",
                "Juan García \"Madrid\" CV filetype:pdf",
            ]),
            "gemini-fake",
        )

    monkeypatch.setattr(qe_mod, "_llm_call_real", fake_llm, raising=True)
    subj = _Subject(full_name="Juan García", city="Madrid")
    out = await qe_mod.expand_queries(subj, redis=None)
    # LLM entries first, fallback entries appended for safety.
    assert out[0] == "Juan García abogado Madrid"
    assert "Juan García" in out  # fallback basic still present
    assert len(out) <= qe_mod.MAX_QUERIES


@pytest.mark.asyncio
async def test_expand_queries_cache_hit(monkeypatch):
    """When Redis has a cached list, return it without calling the LLM."""
    r = _FakeRedis()
    subj = _Subject(full_name="Juan García", city="Madrid")
    cached = ["Q1", "Q2", "Q3"]
    await r.setex(qe_mod.cache_key(subj), 60, json.dumps(cached))

    monkeypatch.setattr(
        qe_mod,
        "_llm_call_real",
        AsyncMock(side_effect=AssertionError("LLM must not be called")),
        raising=True,
    )

    out = await qe_mod.expand_queries(subj, redis=r)
    assert out == cached


@pytest.mark.asyncio
async def test_expand_queries_writes_cache_after_llm_call(monkeypatch):
    r = _FakeRedis()
    subj = _Subject(full_name="Juan García", city="Madrid")

    async def fake_llm(llm, prompt):
        return ('["A", "B", "Juan García"]', "gemini-fake")

    monkeypatch.setattr(qe_mod, "_llm_call_real", fake_llm, raising=True)

    out = await qe_mod.expand_queries(subj, redis=r)
    assert out  # something came back
    cached = await r.get(qe_mod.cache_key(subj))
    assert cached is not None
    parsed = json.loads(cached)
    assert "A" in parsed
