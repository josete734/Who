"""Pivot policy: which atoms may pivot at which depth, and the safety knobs.

These rules govern the cascade so we don't fan out forever. They are kept
deliberately small and pure (no I/O) so they can be unit-tested cheaply.
"""
from __future__ import annotations

from typing import Final

# Canonical pivot kinds the extractor may emit.
PIVOT_KINDS: Final[tuple[str, ...]] = (
    "username",
    "email",
    "phone",
    "domain",
    "url",
    "full_name",
    "photo_url",
    "profile_id",
    "ip",
    "crypto_address",
    "plate",
    "city",
)

# Default safety knobs. Caller (dispatcher / orchestrator) may override.
DEFAULT_MAX_PIVOT_DEPTH: Final[int] = 2
DEFAULT_MAX_COLLECTORS_PER_CASE: Final[int] = 200
DEFAULT_CONFIDENCE_FLOOR: Final[float] = 0.4

# Which kinds are allowed to *trigger* further collectors at each depth.
# Depth 0 = the original user inputs (always allowed). Depth 1 = atoms
# extracted from depth-0 findings. Depth 2 = atoms from depth-1 findings.
#
# Past depth 2 we only trust direct identifiers (email/phone/domain) and
# refuse to chase ambiguous things like names or photo URLs.
_ALLOWED_BY_DEPTH: Final[dict[int, frozenset[str]]] = {
    0: frozenset(PIVOT_KINDS),
    1: frozenset(PIVOT_KINDS),
    2: frozenset({"email", "phone", "domain", "username", "profile_id"}),
}

# Map a pivot kind to the SearchInput field a collector's `needs` tuple
# would match. Kinds with no direct field (photo_url, ip, crypto_address)
# are stuffed into `extra_context` so collectors that opt-in via that
# field still get a shot.
_KIND_TO_FIELD: Final[dict[str, str]] = {
    "username": "username",
    "email": "email",
    "phone": "phone",
    "domain": "domain",
    "url": "linkedin_url",       # closest typed URL slot in SearchInput
    "full_name": "full_name",
    "photo_url": "extra_context",
    "profile_id": "username",
    "ip": "extra_context",
    "crypto_address": "extra_context",
    "plate": "extra_context",
    "city": "city",
}


def kind_to_search_field(kind: str) -> str:
    """Return the SearchInput field name a collector would `need` for this kind."""
    return _KIND_TO_FIELD.get(kind, "extra_context")


def allowed_at_depth(kind: str, depth: int) -> bool:
    """True if a pivot of this `kind` may dispatch new collectors at `depth`."""
    if depth < 0:
        return False
    if depth not in _ALLOWED_BY_DEPTH:
        # Beyond the table, only direct identifiers survive.
        return kind in _ALLOWED_BY_DEPTH[max(_ALLOWED_BY_DEPTH)]
    return kind in _ALLOWED_BY_DEPTH[depth]


def passes_confidence_floor(confidence: float, floor: float = DEFAULT_CONFIDENCE_FLOOR) -> bool:
    """Pivots from low-confidence findings are dropped to avoid amplifying noise."""
    return confidence >= floor
