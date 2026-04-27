"""Tests for the pattern miner: variant generation, ES diminutives, mocked verifier."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.pattern_miner import (
    generate_email_variants,
    generate_username_variants,
    verify_candidate,
)
from app.pattern_miner.miner import mine_patterns
from app.pattern_miner.verifier import VerifierResult


def test_username_variants_deterministic() -> None:
    a = generate_username_variants(full_name="Ada Lovelace")
    b = generate_username_variants(full_name="Ada Lovelace")
    assert a == b
    assert len(a) >= 20
    # Core patterns must appear.
    assert "adalovelace" in a
    assert "ada.lovelace" in a
    assert "alovelace" in a
    assert "ada_lovelace" in a
    # Cap respected.
    assert len(generate_username_variants(full_name="Ada Lovelace", max_variants=15)) <= 15


def test_username_variants_es_diminutive_pepe_jose() -> None:
    out = generate_username_variants(full_name="José Martínez García")
    # Diminutive expansion: Pepe should be present as a candidate first name.
    pepe_hits = [u for u in out if u.startswith("pepe")]
    assert pepe_hits, f"expected pepe* variants, got sample {out[:10]}"
    # ASCII folding of accented characters.
    assert any("martinez" in u for u in out)
    assert not any("é" in u for u in out)


def test_username_variants_reverse_diminutive_paco_francisco() -> None:
    out = generate_username_variants(full_name="Paco Ruiz")
    # Should expand back to canonical "francisco".
    assert any(u.startswith("francisco") for u in out)


def test_username_variants_aliases_string() -> None:
    out = generate_username_variants(
        full_name="Ada Lovelace", aliases="Augusta, Countess Lovelace"
    )
    assert any(u.startswith("augusta") for u in out)


def test_username_variants_empty() -> None:
    assert generate_username_variants() == []
    assert generate_username_variants(full_name="   ") == []


def test_email_variants_basic_patterns() -> None:
    out = generate_email_variants("acme.com", full_name="Ada Lovelace")
    assert "ada.lovelace@acme.com" in out
    assert "a.lovelace@acme.com" in out
    assert "ada@acme.com" in out
    assert all(e.endswith("@acme.com") for e in out)


def test_email_variants_multiple_domains() -> None:
    out = generate_email_variants(
        ["acme.com", "example.org"], full_name="Ada Lovelace", max_variants=40
    )
    assert any(e.endswith("@acme.com") for e in out)
    assert any(e.endswith("@example.org") for e in out)
    assert len(out) <= 40


def test_email_variants_no_domain() -> None:
    assert generate_email_variants("", full_name="Ada Lovelace") == []


@pytest.mark.asyncio
async def test_verify_candidate_email_mocked_positive() -> None:
    with patch("app.pattern_miner.verifier._mx_check",
               new=AsyncMock(return_value=(True, {"mx": ["mx.acme.com"]}))), \
         patch("app.pattern_miner.verifier._gravatar_check",
               new=AsyncMock(return_value=(True, {"hash": "abc"}))):
        r = await verify_candidate("ada@acme.com", "email")
    assert r.verified
    assert "mx" in r.confirmations
    assert "gravatar" in r.confirmations
    assert r.score >= 0.5


@pytest.mark.asyncio
async def test_verify_candidate_email_mocked_negative() -> None:
    with patch("app.pattern_miner.verifier._mx_check",
               new=AsyncMock(return_value=(False, {}))), \
         patch("app.pattern_miner.verifier._gravatar_check",
               new=AsyncMock(return_value=(False, {}))):
        r = await verify_candidate("nope@acme.com", "email")
    assert not r.verified
    assert r.score == 0.0


@pytest.mark.asyncio
async def test_verify_candidate_username_with_quick_check() -> None:
    class FakeCollector:
        name = "fake"

        async def quick_check(self, username: str) -> dict:
            return {"hit": True, "url": f"https://x/{username}"}

    class FakeRegistry:
        def all(self) -> list:
            return [FakeCollector()]

    r = await verify_candidate("adalovelace", "username",
                               collector_registry=FakeRegistry())
    assert r.verified
    assert any(c.startswith("collector:fake") for c in r.confirmations)


@pytest.mark.asyncio
async def test_mine_patterns_orchestration_no_persist() -> None:
    async def fake_verify_many(candidates, **kwargs):
        # Mark one specific email and one username as verified.
        out: list[VerifierResult] = []
        for c, k in candidates:
            score = 0.9 if c in ("ada.lovelace@acme.com", "adalovelace") else 0.0
            confirmations = ["mock"] if score > 0 else []
            out.append(VerifierResult(candidate=c, kind=k,
                                      confirmations=confirmations, score=score))
        return out

    with patch("app.pattern_miner.miner.verify_many", new=fake_verify_many):
        res = await mine_patterns(
            full_name="Ada Lovelace",
            domains=["acme.com"],
            persist=False,
            enable_network=False,
        )
    verified_emails = [r for r in res.emails if r.verified]
    verified_users = [r for r in res.usernames if r.verified]
    assert any(v.candidate == "ada.lovelace@acme.com" for v in verified_emails)
    assert any(v.candidate == "adalovelace" for v in verified_users)
    # Sorted by score descending.
    assert res.emails[0].score >= res.emails[-1].score
