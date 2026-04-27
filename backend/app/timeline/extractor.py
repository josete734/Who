"""Extract dated events (TimelineEvent) from findings.

Strategy:
  1. Walk known date-bearing fields on the finding payload (recursive).
  2. For free-text values, run regex passes for ISO-8601 / RFC-2822 /
     common locale forms and let dateutil resolve them.
  3. Each match becomes a TimelineEvent tagged with a `kind` describing
     what the field meant (account_created, breach, post, etc.).

The extractor is intentionally permissive: bogus dates (year < 1990 or
in the far future) are dropped. Confidence is heuristic-driven:
structured field hits score higher than free-text regex hits.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from dateutil import parser as dateparser

# --------------------------------------------------------------------------- #
# Field name -> kind mapping. Lowercased, exact match against payload keys.   #
# --------------------------------------------------------------------------- #
_FIELD_KIND: dict[str, str] = {
    "created_at": "account_created",
    "account_created_at": "account_created",
    "registered": "account_registered",
    "registered_at": "account_registered",
    "registration_date": "account_registered",
    "joined": "account_joined",
    "joined_at": "account_joined",
    "join_date": "account_joined",
    "founded_on": "company_founded",
    "founded": "company_founded",
    "incorporated_on": "company_founded",
    "breach_date": "breach",
    "breached_at": "breach",
    "added_date": "breach_published",
    "post_date": "post",
    "posted_at": "post",
    "published_at": "post",
    "publish_date": "post",
    "last_active": "last_seen",
    "last_seen": "last_seen",
    "last_activity": "last_seen",
    "last_login": "last_seen",
    "modified_at": "modified",
    "updated_at": "modified",
    "expires_at": "expires",
    "expiry": "expires",
    "domain_created": "domain_created",
    "whois_created": "domain_created",
    "deleted_at": "deleted",
    "verified_at": "verified",
}

# Free-text regex patterns. Order matters: the more specific first.
_ISO_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?)\b"
)
_RFC2822_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
    r"\s+\d{2}:\d{2}:\d{2}\s+(?:[+-]\d{4}|GMT|UTC)"
)
_HUMAN_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    re.IGNORECASE,
)

_MIN_YEAR = 1990
_MAX_FUTURE = 50  # years past "now" to accept


@dataclass
class TimelineEvent:
    ts: dt.datetime
    kind: str
    source_collector: str
    label: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    finding_id: str | None = None


def _coerce_dt(value: Any) -> dt.datetime | None:
    """Best-effort parse to an aware UTC datetime; None on failure."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, dt.date):
        d = dt.datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        # Treat as epoch seconds if plausible.
        try:
            if value > 10_000_000_000:  # ms
                value = value / 1000.0
            d = dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            d = dateparser.parse(s, fuzzy=False)
        except (ValueError, TypeError, OverflowError):
            return None
        if d is None:
            return None
    else:
        return None

    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    else:
        d = d.astimezone(dt.timezone.utc)

    now = dt.datetime.now(dt.timezone.utc)
    if d.year < _MIN_YEAR or d.year > now.year + _MAX_FUTURE:
        return None
    return d


def _walk(obj: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (f"[{i}]",))
    else:
        yield path, obj


def _scan_freetext(text: str) -> list[str]:
    """Return raw date strings located inside `text`."""
    out: list[str] = []
    for rx in (_RFC2822_RE, _ISO_RE, _HUMAN_RE):
        out.extend(m.group(0) if m.lastindex is None else m.group(1) for m in rx.finditer(text))
    return out


def _kind_from_path(path: tuple[str, ...]) -> str | None:
    for part in reversed(path):
        key = part.lower()
        if key in _FIELD_KIND:
            return _FIELD_KIND[key]
    return None


def extract_events(finding: Any) -> list[TimelineEvent]:
    """Extract TimelineEvents from a Finding-like object.

    Accepts either an ORM Finding or a plain dict with keys
    {id, collector, title, url, payload, confidence}.
    """
    if isinstance(finding, dict):
        fid = finding.get("id")
        collector = finding.get("collector") or "unknown"
        title = finding.get("title") or ""
        url = finding.get("url")
        payload = finding.get("payload") or {}
        base_conf = float(finding.get("confidence") or 0.7)
    else:
        fid = getattr(finding, "id", None)
        collector = getattr(finding, "collector", None) or "unknown"
        title = getattr(finding, "title", "") or ""
        url = getattr(finding, "url", None)
        payload = getattr(finding, "payload", {}) or {}
        base_conf = float(getattr(finding, "confidence", 0.7) or 0.7)

    events: list[TimelineEvent] = []
    seen: set[tuple[str, int]] = set()

    def _push(ts: dt.datetime, kind: str, conf: float, evidence: dict[str, Any]) -> None:
        # Dedupe within a single finding by (kind, hour-bucket).
        key = (kind, int(ts.timestamp() // 3600))
        if key in seen:
            return
        seen.add(key)
        events.append(
            TimelineEvent(
                ts=ts,
                kind=kind,
                source_collector=str(collector),
                label=title or kind,
                evidence={"url": url, **evidence},
                confidence=round(min(0.99, max(0.05, conf)), 3),
                finding_id=str(fid) if fid is not None else None,
            )
        )

    # 1. Structured walk.
    for path, value in _walk(payload):
        kind = _kind_from_path(path)
        if kind is None:
            continue
        ts = _coerce_dt(value)
        if ts is None:
            continue
        _push(
            ts,
            kind,
            conf=min(0.95, base_conf + 0.1),
            evidence={"field": ".".join(path), "raw": str(value)[:200]},
        )

    # 2. Free-text scan inside title + string payload values without a field hit.
    text_blobs: list[tuple[str, str]] = []
    if title:
        text_blobs.append(("title", title))
    for path, value in _walk(payload):
        if isinstance(value, str) and len(value) <= 4096 and _kind_from_path(path) is None:
            text_blobs.append((".".join(path), value))

    for source, blob in text_blobs:
        for raw in _scan_freetext(blob):
            ts = _coerce_dt(raw)
            if ts is None:
                continue
            _push(
                ts,
                kind="mention",
                conf=max(0.2, base_conf - 0.2),
                evidence={"field": source, "raw": raw[:200]},
            )

    return events
