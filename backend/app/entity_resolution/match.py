"""Pairwise match rules between candidate entities.

Each rule receives two `Entity` instances (or proto-dicts) and returns a
match confidence in [0, 1] (0 = no link). The engine combines rule outputs
into union-find clusters.

Rule catalogue (confidence weights):
  R1  exact_email           same normalized email                       1.00
  R2  exact_phone            same E.164 phone                            1.00
  R3  exact_url              same canonicalized URL                      1.00
  R4  exact_username         same (platform, normalized username)        0.95
  R5  same_domain_handle     same handle on same provider domain         0.85
  R6  fuzzy_name             Jaro-Winkler ≥ 0.92 + same domain/context   0.60-0.90
  R7  gravatar_hash          md5(email) == gravatar hash from any source 1.00
  R8  github_bio_email       email substring in github profile bio       0.80
  R9  github_bio_username    username token in github profile bio        0.65
  R10 commit_email_login     CommitEmail payload links email→login       0.90
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from rapidfuzz.distance import JaroWinkler

from app.entity_resolution.entities import Entity
from app.entity_resolution.normalize import fold_diacritics

# ---------------------------------------------------------------------------
# Atomic predicates
# ---------------------------------------------------------------------------

def _gravatar_hash(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()  # noqa: S324


def _attr_get(e: Entity, key: str) -> Any:
    return e.attrs.get(key)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def exact_email(a: Entity, b: Entity) -> float:
    if a.type == "Email" and b.type == "Email" and a.value and a.value == b.value:
        return 1.0
    return 0.0


def exact_phone(a: Entity, b: Entity) -> float:
    if a.type == "Phone" and b.type == "Phone" and a.value and a.value == b.value:
        return 1.0
    return 0.0


def exact_url(a: Entity, b: Entity) -> float:
    if a.type == "URL" and b.type == "URL" and a.value and a.value == b.value:
        return 1.0
    return 0.0


def exact_username(a: Entity, b: Entity) -> float:
    if a.type == "Account" and b.type == "Account":
        pa = (_attr_get(a, "platform") or "").lower()
        pb = (_attr_get(b, "platform") or "").lower()
        if a.value and a.value == b.value and pa and pa == pb:
            return 0.95
    return 0.0


def same_domain_handle(a: Entity, b: Entity) -> float:
    """Email local-part matches an account handle on a related domain."""
    if {a.type, b.type} != {"Email", "Account"}:
        return 0.0
    email_e = a if a.type == "Email" else b
    acct_e = b if a.type == "Email" else a
    local = email_e.value.split("@", 1)[0] if "@" in email_e.value else ""
    if local and local == acct_e.value:
        return 0.85
    return 0.0


def fuzzy_name(a: Entity, b: Entity) -> float:
    if a.type != "Person" or b.type != "Person":
        return 0.0
    na, nb = fold_diacritics(a.value), fold_diacritics(b.value)
    if not na or not nb:
        return 0.0
    sim = JaroWinkler.normalized_similarity(na, nb)
    if sim < 0.92:
        return 0.0
    # Boost if shared context (same domain or shared collector cluster).
    shared_domain = (
        _attr_get(a, "domain")
        and _attr_get(a, "domain") == _attr_get(b, "domain")
    )
    base = 0.60 + (sim - 0.92) * (0.30 / 0.08)  # 0.60..0.90 across [0.92..1.0]
    if shared_domain:
        base = min(0.90, base + 0.10)
    return min(0.90, base)


def gravatar_hash(a: Entity, b: Entity) -> float:
    """If one side has a gravatar hash and the other is an email whose md5
    matches, they refer to the same identity."""
    pairs = [(a, b), (b, a)]
    for x, y in pairs:
        h = _attr_get(x, "gravatar_hash")
        if h and y.type == "Email" and y.value:
            if _gravatar_hash(y.value) == h.lower():
                return 1.0
    return 0.0


_BIO_TOKEN_RE = re.compile(r"[A-Za-z0-9_.+\-@]+")


def github_bio_email(a: Entity, b: Entity) -> float:
    """github profile bio mentions an email value."""
    pairs = [(a, b), (b, a)]
    for prof, target in pairs:
        if _attr_get(prof, "platform") == "github" and target.type == "Email":
            bio = (_attr_get(prof, "bio") or "") + " " + (_attr_get(prof, "blog") or "")
            if target.value and target.value.lower() in bio.lower():
                return 0.80
    return 0.0


def github_bio_username(a: Entity, b: Entity) -> float:
    pairs = [(a, b), (b, a)]
    for prof, target in pairs:
        if _attr_get(prof, "platform") == "github" and target.type == "Account":
            bio = (_attr_get(prof, "bio") or "")
            tokens = {t.lower() for t in _BIO_TOKEN_RE.findall(bio)}
            if target.value and target.value.lower() in tokens:
                return 0.65
    return 0.0


def commit_email_login(a: Entity, b: Entity) -> float:
    """A CommitByEmail payload binds an email → login (account)."""
    pairs = [(a, b), (b, a)]
    for em, acct in pairs:
        if em.type != "Email" or acct.type != "Account":
            continue
        login = _attr_get(em, "author_login")
        if login and login.lower() == acct.value.lower():
            return 0.90
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ALL_RULES = (
    exact_email,
    exact_phone,
    exact_url,
    exact_username,
    same_domain_handle,
    fuzzy_name,
    gravatar_hash,
    github_bio_email,
    github_bio_username,
    commit_email_login,
)


def best_match(a: Entity, b: Entity) -> tuple[float, str]:
    """Return (best_confidence, rule_name) over all rules."""
    best, name = 0.0, ""
    for rule in ALL_RULES:
        c = rule(a, b)
        if c > best:
            best, name = c, rule.__name__
    return best, name
