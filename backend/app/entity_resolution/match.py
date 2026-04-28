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
  R11 organization_vat       Same VAT/NIF on Organization entities       1.00 (Wave 5)
  R12 event_temporal_geo     Event within 24 h and 5 km                  0.85 (Wave 5)
  R13 homonym_disambiguation Penalises identical names with discordant   -0.20 (Wave 5)
                             city / occupation / birth year context;
                             rewards confirmed shared context.
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
    base = min(0.90, base)

    # R13 — homonym disambiguation. Apply contextual bonuses/maluses on top
    # of the base similarity. The motivation is the classic OSINT failure
    # mode where two people share an extremely common name (Juan García) but
    # are clearly distinct based on city / birth year / occupation. The
    # signal here is *attribute-level*: we only act when both sides expose
    # the same attribute, otherwise the value is "unknown" and we don't
    # punish for missing data.
    bonus = 0.0
    a_city = _norm_lower(_attr_get(a, "city"))
    b_city = _norm_lower(_attr_get(b, "city"))
    if a_city and b_city:
        if a_city == b_city:
            bonus += 0.10
        else:
            bonus -= 0.20

    a_yob = _attr_get(a, "birth_year")
    b_yob = _attr_get(b, "birth_year")
    if a_yob and b_yob:
        try:
            if abs(int(a_yob) - int(b_yob)) <= 2:
                bonus += 0.10
            else:
                bonus -= 0.15
        except (ValueError, TypeError):
            pass

    a_occ = _norm_lower(_attr_get(a, "occupation"))
    b_occ = _norm_lower(_attr_get(b, "occupation"))
    if a_occ and b_occ:
        if a_occ == b_occ:
            bonus += 0.05
        elif _occupations_conflict(a_occ, b_occ):
            bonus -= 0.15

    # Final score is clamped to [0, 0.95]; bonuses can lift it past the
    # original 0.90 ceiling when the context strongly confirms the match,
    # and maluses can drop it to 0.0 (= "no link") when the context says
    # these are different people who happen to share a name.
    return max(0.0, min(0.95, base + bonus))


def _norm_lower(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _occupations_conflict(a: str, b: str) -> bool:
    """Heuristic: occupations conflict only when they share NO meaningful token.

    "software engineer" vs "civil engineer" share "engineer" (≥4 letters) so
    we treat them as compatible — could be the same person at different
    companies. "doctor" vs "lawyer" share no 4+ letter token → conflict.
    Substring-containment is also accepted (e.g. "engineer" vs
    "software engineer").
    """
    if a == b:
        return False
    if a in b or b in a:
        return False
    a_tokens = {t for t in re.split(r"\W+", a) if len(t) >= 4}
    b_tokens = {t for t in re.split(r"\W+", b) if len(t) >= 4}
    if a_tokens & b_tokens:
        return False
    return True


def organization_vat(a: Entity, b: Entity) -> float:
    """R11 — VAT / NIF / company-id match on Organization entities.

    Organizations carry their canonical fiscal identifier in ``attrs.vat_id``
    (or ``attrs.tax_id`` for non-EU). Different formats (e.g. ``B12345678``
    vs ``ESB12345678``) are normalised by stripping country prefixes and
    non-alphanumerics before comparison.
    """
    if a.type != "Organization" or b.type != "Organization":
        return 0.0
    va = _normalize_vat(_attr_get(a, "vat_id") or _attr_get(a, "tax_id") or "")
    vb = _normalize_vat(_attr_get(b, "vat_id") or _attr_get(b, "tax_id") or "")
    if va and vb and va == vb:
        return 1.0
    return 0.0


_EU_COUNTRY_CODES = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI",
        "FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
        "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI",
    }
)


def _normalize_vat(v: str) -> str:
    """Strip non-alphanumerics and a leading EU country prefix.

    Real-world VAT identifiers come prefixed with the 2-letter country code
    of the issuing state ("ES", "GB"…). Some collectors strip the prefix,
    others keep it. We always normalise to the un-prefixed form so the
    same-VAT-across-formats test below succeeds.
    """
    if not v:
        return ""
    s = re.sub(r"[^A-Z0-9]", "", str(v).upper())
    if len(s) > 2 and s[:2] in _EU_COUNTRY_CODES:
        s = s[2:]
    return s


def event_temporal_geo(a: Entity, b: Entity) -> float:
    """R12 — events within 24 h and 5 km of each other are the same event.

    Events keep their timestamp in ``attrs.when`` (ISO-8601 string or epoch
    int) and their coordinates in ``attrs.lat``/``attrs.lon``. Both
    attributes must be present on both sides; missing data → no match.
    """
    if a.type != "Event" or b.type != "Event":
        return 0.0
    ta = _to_epoch(_attr_get(a, "when"))
    tb = _to_epoch(_attr_get(b, "when"))
    if ta is None or tb is None:
        return 0.0
    if abs(ta - tb) > 24 * 3600:
        return 0.0
    la, lo_a = _to_float(_attr_get(a, "lat")), _to_float(_attr_get(a, "lon"))
    lb, lo_b = _to_float(_attr_get(b, "lat")), _to_float(_attr_get(b, "lon"))
    if la is None or lo_a is None or lb is None or lo_b is None:
        # Without spatial confirmation we still allow a temporal-only match
        # when the kind matches exactly (e.g. two BORME nombramientos within
        # 24 h are almost certainly the same event).
        ka = _attr_get(a, "kind")
        kb = _attr_get(b, "kind")
        return 0.7 if ka and ka == kb else 0.0
    if _haversine_km(la, lo_a, lb, lo_b) <= 5.0:
        return 0.85
    return 0.0


def _to_epoch(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            import datetime as _dt
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return _dt.datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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
    organization_vat,    # R11 (Wave 5)
    event_temporal_geo,  # R12 (Wave 5)
)


def best_match(a: Entity, b: Entity) -> tuple[float, str]:
    """Return (best_confidence, rule_name) over all rules."""
    best, name = 0.0, ""
    for rule in ALL_RULES:
        c = rule(a, b)
        if c > best:
            best, name = c, rule.__name__
    return best, name
