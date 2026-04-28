"""Collector base class and registry.

A Collector takes a SearchInput and emits Findings asynchronously.
Each Collector self-registers on import via the @register decorator.
"""
from __future__ import annotations

import abc
import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas import SearchInput


# Query params that are tracking noise and should not affect the fingerprint.
_TRACKER_PARAMS: frozenset[str] = frozenset({
    "ref", "fbclid", "gclid", "mc_cid", "mc_eid", "yclid", "msclkid",
    "_hsenc", "_hsmi", "vero_id", "vero_conv",
})
_TRACKER_PREFIXES: tuple[str, ...] = ("utm_",)
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def _is_tracker_param(key: str) -> bool:
    k = key.lower()
    if k in _TRACKER_PARAMS:
        return True
    return any(k.startswith(p) for p in _TRACKER_PREFIXES)


def _normalize_url(url: str) -> str:
    """Lowercase scheme/host, strip tracker query params, drop trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except Exception:  # noqa: BLE001
        return url.strip().lower()
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not _is_tracker_param(k)]
        kept.sort()
        query = urlencode(kept, doseq=True)
    else:
        query = ""
    rebuilt = urlunsplit((scheme, netloc, path, query, ""))
    return rebuilt.lower()


def _normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip extra punctuation."""
    t = title.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


@dataclass
class Finding:
    collector: str
    category: str
    entity_type: str
    title: str
    url: str | None = None
    confidence: float = 0.7
    payload: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable hash for dedup across collectors.

        Normalises the URL (or, if absent, the title) so cosmetic differences
        (tracker query params, trailing slashes, casing, punctuation,
        whitespace) collapse to the same fingerprint.
        """
        if self.url:
            normalized = _normalize_url(self.url)
        else:
            normalized = _normalize_title(self.title or "")
        base = "|".join([self.category, self.entity_type, normalized])
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


class Collector(abc.ABC):
    """Abstract collector. Subclasses declare `name`, `category`, `needs`, and implement `run`."""

    name: str = ""
    category: str = ""
    # Which SearchInput fields are required (any of them is enough unless `requires_all`)
    needs: tuple[str, ...] = ()
    requires_all: bool = False
    # Resilience knobs — consumed by app.collectors.resilience.run_with_resilience.
    # Subclasses may override any of these.
    timeout_seconds: int = 60
    max_retries: int = 1
    circuit_breaker_threshold: int = 5
    description: str = ""

    def applicable(self, input: SearchInput) -> bool:
        if not self.needs:
            return True
        available = set(input.non_empty_fields().keys())
        needs = set(self.needs)
        return needs.issubset(available) if self.requires_all else bool(available & needs)

    @abc.abstractmethod
    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:  # type: ignore[misc]
        """Async generator yielding Finding objects."""
        if False:
            yield  # pragma: no cover


def _identity_matches(input: SearchInput, haystack: str, fields: tuple[str, ...] = ("username", "full_name", "phone", "email")) -> bool:
    """Cheap identity guard for HTML scrapers.

    Returns True when at least one identity-bearing field from ``input`` shows up
    (case-insensitively) in the ``haystack`` text. Use before yielding findings
    on collectors whose endpoints return generic OG/SPA shells when the subject
    is absent — prevents emitting a "valid" finding for a login-wall page.
    """
    if not haystack:
        return False
    needle_set = []
    for f in fields:
        v = getattr(input, f, None)
        if not v:
            continue
        v = str(v).strip().lstrip("@")
        if len(v) >= 3:
            needle_set.append(v.lower())
    if not needle_set:
        return True  # nothing to compare against — fall through (caller decides)
    h = haystack.lower()
    return any(n in h for n in needle_set)


def _dynamic_confidence(base: float, payload: dict[str, Any], required_keys: tuple[str, ...] | None = None) -> float:
    """Degrade confidence when the extracted payload has no meaningful values.

    Scrapers that yield a finding even when their payload is mostly None should
    pass confidence through this helper so the orchestrator's downstream
    consensus and synthesis layers can distinguish high-signal from low-signal
    rows.

    If ``required_keys`` is provided, returns the empty-payload penalty unless
    any of those keys carries truthy data. Otherwise checks every value in the
    payload.
    """
    if required_keys:
        any_signal = any(payload.get(k) for k in required_keys)
    else:
        any_signal = any(v not in (None, "", [], {}, 0) for v in payload.values())
    return base if any_signal else base * 0.4


class _Registry:
    def __init__(self) -> None:
        self._collectors: dict[str, type[Collector]] = {}

    def register(self, cls: type[Collector]) -> type[Collector]:
        if not cls.name:
            raise ValueError(f"Collector {cls.__name__} missing `name`")
        if cls.name in self._collectors:
            raise ValueError(f"Duplicate collector: {cls.name}")
        self._collectors[cls.name] = cls
        return cls

    def all(self) -> list[type[Collector]]:
        return list(self._collectors.values())

    def applicable_for(self, input: SearchInput) -> list[Collector]:
        instances = [c() for c in self._collectors.values()]
        return [c for c in instances if c.applicable(input)]

    def by_name(self, name: str) -> type[Collector] | None:
        return self._collectors.get(name)


collector_registry = _Registry()


def register(cls: type[Collector]) -> type[Collector]:
    return collector_registry.register(cls)


# ---------------------------------------------------------------------------
# ORCHESTRATOR WIRING — TODO for the integration agent (Wave 1 / A-wiring)
# ---------------------------------------------------------------------------
# The resilience wrapper lives in ``app.collectors.resilience``. To activate it
# the orchestrator must be changed from:
#
#     async for f in collector.run(search_input):
#         ...
#
# to something like:
#
#     from app.collectors.resilience import CircuitBreaker, run_with_resilience, CollectorFailure
#
#     breaker = CircuitBreaker(threshold=5)   # one per case
#     for collector in registry.applicable_for(search_input):
#         async for item in run_with_resilience(collector, search_input, breaker=breaker):
#             if isinstance(item, CollectorFailure):
#                 # persist as a per-collector status row, do NOT abort the case
#                 record_failure(case_id, item)
#             else:
#                 emit_finding(case_id, item)
#
# Per-collector overrides for ``timeout_seconds``, ``max_retries`` and
# ``circuit_breaker_threshold`` are picked up automatically from class attrs.
# ---------------------------------------------------------------------------
