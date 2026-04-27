"""Per-type value normalizers.

All normalizers are pure, deterministic, and total: they always return a
canonical string (or None if the input is unrecoverable). NFC unicode is
applied at the very end so equivalent codepoints collapse.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import phonenumbers

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def nfc(s: str) -> str:
    """Unicode NFC normalization. Trims surrounding whitespace."""
    return unicodedata.normalize("NFC", s).strip()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    s = nfc(raw).lower()
    if not _EMAIL_RE.match(s):
        return None
    # Gmail: strip dots in local part and +tag (canonical form for matching).
    local, _, domain = s.partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    else:
        # Generic: strip +tag for major providers as a soft canonicalization.
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# Phone (E.164, default region ES)
# ---------------------------------------------------------------------------

def normalize_phone(raw: str | None, default_region: str = "ES") -> str | None:
    if not raw:
        return None
    candidate = nfc(raw)
    try:
        parsed = phonenumbers.parse(candidate, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# URL — strip tracking params, lowercase host, drop fragment
# ---------------------------------------------------------------------------

_TRACKER_PREFIXES = ("utm_", "mc_", "icid_", "_hs", "fbclid", "gclid", "yclid",
                     "mkt_tok", "spm")
_TRACKER_EXACT = {"fbclid", "gclid", "yclid", "msclkid", "ref", "ref_src",
                  "ref_url", "igshid", "si"}


def _is_tracker(name: str) -> bool:
    n = name.lower()
    if n in _TRACKER_EXACT:
        return True
    return any(n.startswith(p) for p in _TRACKER_PREFIXES)


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    s = nfc(raw)
    if "://" not in s:
        s = "http://" + s
    try:
        u = urlparse(s)
    except ValueError:
        return None
    if not u.netloc:
        return None
    host = u.netloc.lower()
    # Drop default ports, strip leading 'www.' for canonicalization
    if host.startswith("www."):
        host = host[4:]
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False) if not _is_tracker(k)]
    qs.sort()
    path = u.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((u.scheme.lower(), host, path, "", urlencode(qs), ""))


def normalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    s = nfc(raw).lower()
    if "://" in s:
        try:
            s = urlparse(s).netloc or s
        except ValueError:
            return None
    s = s.split("/", 1)[0].split(":", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    if "." not in s:
        return None
    return s


# ---------------------------------------------------------------------------
# Username — per-platform rules
# ---------------------------------------------------------------------------

# Mapping of platform → (lowercase?, strip @?, allowed_chars_re)
_PLATFORM_RULES: dict[str, dict] = {
    "twitter":   {"lower": True,  "strip_at": True},
    "x":         {"lower": True,  "strip_at": True},
    "instagram": {"lower": True,  "strip_at": True},
    "tiktok":    {"lower": True,  "strip_at": True},
    "github":    {"lower": True,  "strip_at": True},
    "gitlab":    {"lower": True,  "strip_at": True},
    "reddit":    {"lower": True,  "strip_at": True},
    "telegram":  {"lower": True,  "strip_at": True},
    # Mastodon usernames keep case but the @host suffix is lowercased.
    "mastodon":  {"lower": False, "strip_at": False, "host_lower": True},
    "linkedin":  {"lower": True,  "strip_at": True},
    "keybase":   {"lower": True,  "strip_at": True},
}


def normalize_username(raw: str | None, platform: str | None = None) -> str | None:
    if not raw:
        return None
    s = nfc(raw)
    rules = _PLATFORM_RULES.get((platform or "").lower(), {"lower": True, "strip_at": True})
    if rules.get("strip_at") and s.startswith("@"):
        s = s[1:]
    if rules.get("host_lower") and "@" in s:
        local, _, host = s.partition("@")
        s = f"{local}@{host.lower()}"
    elif rules.get("lower"):
        s = s.lower()
    return s or None


# ---------------------------------------------------------------------------
# Person name
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize_name(raw: str | None) -> str | None:
    if not raw:
        return None
    s = nfc(raw)
    # Strip diacritics for matching key, but keep original casing folded.
    s = _WS_RE.sub(" ", s).strip().lower()
    return s or None


def fold_diacritics(s: str) -> str:
    """ASCII-fold for fuzzy comparison (Jose ≈ José)."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
