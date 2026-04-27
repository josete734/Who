"""Finding -> Pivot atom extractor.

Pivots are the small, typed identifiers we can hand to other collectors.
We mine them from three places on a Finding:
  1. structured `payload` keys we conventionally use across collectors
     (email, phone, domain, username, full_name, photo_url, profile_id,
     ip, crypto_address);
  2. the `url` (host -> domain, path -> username heuristics);
  3. free text inside `title` and string payload values via regex.

The extractor is deliberately conservative: it normalises values and
deduplicates within the finding. Cross-finding / cross-case dedup is
the dispatcher's job.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.pivot.policy import PIVOT_KINDS

# --------------------------------------------------------------------------- #
# Regex bank — kept simple on purpose. False positives are filtered later by  #
# normalisation + the dispatcher's confidence floor.                          #
# --------------------------------------------------------------------------- #
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+\d[\d\s\-().]{6,}\d")
_DOMAIN_RE = re.compile(r"\b(?=[a-z0-9-]{1,63}\.)([a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"\bhttps?://[^\s'\"<>]+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")
_BTC_RE = re.compile(r"\b(?:bc1[0-9a-z]{20,80}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Hostnames we never treat as a pivotable "domain of interest" (too generic).
_DOMAIN_BLACKLIST: frozenset[str] = frozenset({
    "github.com", "gitlab.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "linkedin.com", "reddit.com", "tiktok.com",
    "wikipedia.org", "wikimedia.org", "google.com", "youtube.com",
    "stackoverflow.com", "stackexchange.com", "mastodon.social",
    "bsky.app", "keybase.io", "gravatar.com", "wa.me", "t.me",
    "archive.org", "web.archive.org", "shodan.io", "urlscan.io",
    "ipinfo.io", "leakix.net", "crt.sh",
})


@dataclass(frozen=True)
class Pivot:
    kind: str
    value: str
    source_finding_id: str | None
    confidence: float = 0.7
    evidence: tuple[tuple[str, Any], ...] = ()  # frozen key/value pairs (e.g. ("role","secondary"))

    def __post_init__(self) -> None:  # pragma: no cover — guard
        if self.kind not in PIVOT_KINDS:
            raise ValueError(f"unknown pivot kind: {self.kind!r}")

    @property
    def evidence_dict(self) -> dict[str, Any]:
        return dict(self.evidence)


# --------------------------------------------------------------------------- #
# Normalisation                                                               #
# --------------------------------------------------------------------------- #
def _norm_email(v: str) -> str | None:
    v = v.strip().lower()
    return v if _EMAIL_RE.fullmatch(v) else None


def _norm_phone(v: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", v.strip())
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("0") if digits else digits
    return digits if len(re.sub(r"\D", "", digits)) >= 7 else None


def _norm_domain(v: str) -> str | None:
    v = v.strip().lower().lstrip(".")
    # Drop leading scheme/path if a URL slipped through.
    if "://" in v:
        v = urlparse(v).hostname or ""
    v = v.split("/", 1)[0]
    if not v or v in _DOMAIN_BLACKLIST:
        return None
    if not _DOMAIN_RE.fullmatch(v):
        return None
    return v


def _norm_username(v: str) -> str | None:
    v = v.strip().lstrip("@")
    if not v or len(v) > 64:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{2,64}", v):
        return None
    return v.lower()


def _norm_url(v: str) -> str | None:
    v = v.strip()
    return v if _URL_RE.fullmatch(v) else None


# --------------------------------------------------------------------------- #
# Extraction                                                                  #
# --------------------------------------------------------------------------- #
def _scan_text(text: str, sink: dict[tuple[str, str], float], confidence: float) -> None:
    """Mine a free-text blob for emails / phones / urls / ips / crypto."""
    for m in _EMAIL_RE.findall(text):
        n = _norm_email(m)
        if n:
            sink.setdefault(("email", n), confidence)
    for m in _PHONE_RE.findall(text):
        n = _norm_phone(m)
        if n:
            sink.setdefault(("phone", n), confidence * 0.9)
    for m in _URL_RE.findall(text):
        n = _norm_url(m)
        if n:
            sink.setdefault(("url", n), confidence * 0.9)
            host = urlparse(n).hostname
            if host:
                d = _norm_domain(host)
                if d:
                    sink.setdefault(("domain", d), confidence * 0.8)
    for m in _IPV4_RE.findall(text):
        sink.setdefault(("ip", m), confidence * 0.8)
    for m in _BTC_RE.findall(text):
        sink.setdefault(("crypto_address", m), confidence * 0.9)
    for m in _ETH_RE.findall(text):
        sink.setdefault(("crypto_address", m.lower()), confidence * 0.9)


def _from_payload(
    payload: dict[str, Any],
    sink: dict[tuple[str, str], float],
    confidence: float,
    evidence_sink: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> None:
    """Pull the conventional structured keys our collectors set."""
    direct: dict[str, callable] = {  # type: ignore[type-arg]
        "email": _norm_email,
        "phone": _norm_phone,
        "domain": _norm_domain,
        "username": _norm_username,
        "url": _norm_url,
        "photo_url": _norm_url,
        "avatar_url": _norm_url,
        "profile_id": lambda x: x.strip() if isinstance(x, str) and x.strip() else None,
        "id": lambda x: x.strip() if isinstance(x, str) and x.strip() else None,
        "ip": lambda x: x.strip() if isinstance(x, str) and _IPV4_RE.fullmatch(x.strip()) else None,
        "full_name": lambda x: x.strip() if isinstance(x, str) and 2 < len(x.strip()) < 200 else None,
        "name": lambda x: x.strip() if isinstance(x, str) and 2 < len(x.strip()) < 200 else None,
        "crypto_address": lambda x: x.strip() if isinstance(x, str) else None,
    }
    key_to_kind = {
        "email": "email", "phone": "phone", "domain": "domain",
        "username": "username", "url": "url",
        "photo_url": "photo_url", "avatar_url": "photo_url",
        "profile_id": "profile_id", "id": "profile_id",
        "ip": "ip", "full_name": "full_name", "name": "full_name",
        "crypto_address": "crypto_address",
    }
    for k, raw in payload.items():
        if not isinstance(raw, str):
            continue
        if k not in direct:
            continue
        norm = direct[k](raw)
        if norm:
            sink.setdefault((key_to_kind[k], norm), confidence)

    # Secondary email: emit it as a regular `email` pivot but tag the
    # evidence so the dispatcher / UI can show role=secondary.
    raw_sec = payload.get("email_secondary")
    if isinstance(raw_sec, str):
        norm_sec = _norm_email(raw_sec)
        if norm_sec:
            sink.setdefault(("email", norm_sec), confidence)
            if evidence_sink is not None:
                evidence_sink.setdefault(("email", norm_sec), {"role": "secondary"})

    # Mine remaining string values as free text (catches leakix-style nested blobs).
    for v in payload.values():
        if isinstance(v, str) and len(v) <= 4000:
            _scan_text(v, sink, confidence)


def extract(finding: Any) -> list[Pivot]:
    """Extract pivotable atoms from a Finding (or anything duck-typed similarly).

    Accepts either an ORM `Finding` row or the dataclass `Finding` from
    ``app.collectors.base``. We deliberately avoid an isinstance check so
    tests can pass plain SimpleNamespace stubs.
    """
    title = getattr(finding, "title", "") or ""
    url = getattr(finding, "url", None)
    payload = getattr(finding, "payload", None) or {}
    confidence = float(getattr(finding, "confidence", 0.7) or 0.7)
    fid = getattr(finding, "id", None)
    fid_str = str(fid) if fid is not None else None

    # Map of (kind, value) -> confidence; preserves first-seen confidence.
    sink: dict[tuple[str, str], float] = {}
    evidence_sink: dict[tuple[str, str], dict[str, Any]] = {}

    if isinstance(payload, dict):
        _from_payload(payload, sink, confidence, evidence_sink=evidence_sink)

    if title:
        _scan_text(title, sink, confidence * 0.85)

    if url:
        n = _norm_url(url)
        if n:
            sink.setdefault(("url", n), confidence)
            host = urlparse(n).hostname
            if host:
                d = _norm_domain(host)
                if d:
                    sink.setdefault(("domain", d), confidence * 0.8)
                # username heuristic: /<name> at top of path on social hosts
                path = urlparse(n).path.strip("/")
                if path and "/" not in path:
                    u = _norm_username(path)
                    if u:
                        sink.setdefault(("username", u), confidence * 0.7)

    return [
        Pivot(
            kind=k,
            value=v,
            source_finding_id=fid_str,
            confidence=c,
            evidence=tuple(sorted(evidence_sink.get((k, v), {}).items())),
        )
        for (k, v), c in sink.items()
    ]
