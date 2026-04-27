"""Confidence aggregation across independent observations."""
from __future__ import annotations

from collections.abc import Iterable

CAP = 0.99


def combine_confidences(sources: Iterable[float]) -> float:
    """Aggregate independent source confidences.

    Uses the noisy-OR / independence assumption:
        score = 1 - prod(1 - c_i)

    Capped at 0.99 so we never claim absolute certainty from probabilistic
    sources. Values outside [0, 1] are clamped.
    """
    prod = 1.0
    seen = False
    for c in sources:
        seen = True
        if c < 0.0:
            c = 0.0
        elif c > 1.0:
            c = 1.0
        prod *= (1.0 - c)
    if not seen:
        return 0.0
    return min(CAP, 1.0 - prod)
