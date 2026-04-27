"""Tests for entity_resolution: normalize + match + scoring + engine."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.entity_resolution import match, normalize, scoring
from app.entity_resolution.engine import resolve_in_memory
from app.entity_resolution.entities import Entity, EntitySource


# ---------------------------------------------------------------------------
# Lightweight stand-in for app.db.Finding (avoid importing SQLAlchemy mapped
# instances; engine only reads attributes).
# ---------------------------------------------------------------------------

@dataclass
class FakeFinding:
    collector: str
    category: str
    entity_type: str
    title: str
    confidence: float
    payload: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    case_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def test_normalize_email_gmail_canonical() -> None:
    assert normalize.normalize_email("J.Doe+spam@Gmail.com") == "jdoe@gmail.com"
    assert normalize.normalize_email("a@b.c") == "a@b.c"
    assert normalize.normalize_email(" not-an-email ") is None
    assert normalize.normalize_email(None) is None


def test_normalize_phone_es_default() -> None:
    # Spanish number with no country prefix → +34
    assert normalize.normalize_phone("612 345 678") == "+34612345678"
    # Already E.164
    assert normalize.normalize_phone("+34612345678") == "+34612345678"
    # Garbage
    assert normalize.normalize_phone("not a phone") is None


def test_normalize_url_strips_trackers_and_www() -> None:
    u = "https://www.Example.com/path/?utm_source=x&q=1&fbclid=abc#frag"
    out = normalize.normalize_url(u)
    assert out == "https://example.com/path?q=1"


def test_normalize_username_per_platform() -> None:
    assert normalize.normalize_username("@JohnDoe", "twitter") == "johndoe"
    # Mastodon keeps the local case but lowercases the host
    assert normalize.normalize_username("Alice@MASTODON.social", "mastodon") == "Alice@mastodon.social"


def test_normalize_name_nfc_and_lower() -> None:
    # composed vs decomposed e+acute
    assert normalize.normalize_name("José") == normalize.normalize_name("José")


# ---------------------------------------------------------------------------
# match rules
# ---------------------------------------------------------------------------

def _email(v: str, **a: Any) -> Entity:
    e = Entity(type="Email", value=v, attrs=a)
    e.add_source(EntitySource(collector="x", confidence=0.7))
    return e


def _account(v: str, platform: str, **a: Any) -> Entity:
    e = Entity(type="Account", value=v, attrs={"platform": platform, **a})
    e.add_source(EntitySource(collector="x", confidence=0.7))
    return e


def test_exact_email_rule() -> None:
    a, b = _email("foo@bar.com"), _email("foo@bar.com")
    assert match.exact_email(a, b) == 1.0


def test_exact_username_rule_requires_same_platform() -> None:
    a = _account("jdoe", "github")
    b = _account("jdoe", "github")
    c = _account("jdoe", "twitter")
    assert match.exact_username(a, b) == 0.95
    assert match.exact_username(a, c) == 0.0


def test_fuzzy_name_rule() -> None:
    a = Entity(type="Person", value="jose perez", attrs={"domain": "example.com"})
    b = Entity(type="Person", value="josé pérez", attrs={"domain": "example.com"})
    score, _ = match.best_match(a, b)
    assert score >= 0.7


def test_gravatar_rule_links_email_to_photo() -> None:
    import hashlib
    h = hashlib.md5(b"foo@bar.com").hexdigest()  # noqa: S324
    photo = Entity(type="Photo", value=f"gravatar:{h}", attrs={"gravatar_hash": h})
    em = _email("foo@bar.com")
    score, name = match.best_match(em, photo)
    assert score == 1.0
    assert name == "gravatar_hash"


def test_github_bio_email_link() -> None:
    prof = _account("alice", "github", bio="Reach me at alice@example.com")
    em = _email("alice@example.com")
    s, _ = match.best_match(prof, em)
    assert s >= 0.80


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def test_combine_confidences_noisy_or() -> None:
    # 1 - (1-0.5)(1-0.5) = 0.75
    assert abs(scoring.combine_confidences([0.5, 0.5]) - 0.75) < 1e-9


def test_combine_confidences_capped() -> None:
    out = scoring.combine_confidences([0.99, 0.99, 0.99])
    assert out <= 0.99


def test_combine_confidences_empty_is_zero() -> None:
    assert scoring.combine_confidences([]) == 0.0


# ---------------------------------------------------------------------------
# engine — fixture-based run with synthetic findings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_clusters_email_account_and_gravatar() -> None:
    case_id = uuid.uuid4()
    import hashlib
    grav = hashlib.md5(b"alice@example.com").hexdigest()  # noqa: S324

    findings = [
        FakeFinding(
            collector="holehe", category="email", entity_type="ServiceAccount",
            title="cuenta en github.com",
            url="https://github.com",
            confidence=0.7,
            payload={"email": "Alice@Example.com", "service": "github.com"},
            case_id=case_id,
        ),
        FakeFinding(
            collector="github", category="username", entity_type="GitHubProfile",
            title="GitHub: alice",
            url="https://github.com/alice",
            confidence=0.95,
            payload={"login": "alice", "platform": "github",
                     "bio": "Contact: alice@example.com"},
            case_id=case_id,
        ),
        FakeFinding(
            collector="gravatar", category="email", entity_type="Avatar",
            title="gravatar found",
            url=None,
            confidence=0.9,
            payload={"hash": grav, "email": "alice@example.com"},
            case_id=case_id,
        ),
        # Unrelated phone — should land in its own cluster.
        FakeFinding(
            collector="phoneinfoga", category="phone", entity_type="Phone",
            title="phone",
            url=None,
            confidence=0.6,
            payload={"phone": "612 345 678"},
            case_id=case_id,
        ),
    ]

    entities = await resolve_in_memory(findings)

    # We expect at least: 1 person/email cluster, 1 phone cluster.
    assert len(entities) >= 2
    types = {e.type for e in entities}
    assert "Phone" in types

    # The email/account/gravatar should have collapsed into one entity.
    big = max(entities, key=lambda e: len(e.sources))
    collectors = {s.collector for s in big.sources}
    assert {"holehe", "github", "gravatar"}.issubset(collectors)

    # Aggregated score must exceed any individual source.
    assert big.score > 0.95
    assert big.score <= 0.99
