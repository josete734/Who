"""Tests for Wave 9 — confidence consensus boost.

The pure helper ``_consensus_boost`` is the one we exercise in unit tests
since the SQL UPDATE that wires it into Postgres requires the real DB and
is covered by the broader integration suite. The maths must be:

* 1 source → unchanged
* 2 sources → +0.10
* 3 sources → +0.20
* N sources → never above the 0.98 ceiling
"""
from __future__ import annotations

import pytest

from app.orchestrator import _consensus_boost


@pytest.mark.parametrize(
    "base, n, expected",
    [
        (0.6, 1, 0.6),       # single source: unchanged
        (0.6, 2, 0.7),       # +0.10
        (0.6, 3, 0.8),       # +0.20
        (0.6, 4, 0.9),       # +0.30
        (0.7, 5, 0.98),      # 0.7 + 0.40 = 1.10 → clamped to 0.98
        (0.0, 6, 0.5),       # 0 + 0.50 = 0.5 (no clamping at the low end)
    ],
)
def test_consensus_boost_math(base, n, expected):
    assert _consensus_boost(base, n) == pytest.approx(expected, abs=1e-9)


def test_consensus_boost_zero_sources_treated_as_one():
    # Defensive: degenerate input shouldn't crash or invent confidence.
    assert _consensus_boost(0.7, 0) == 0.7


def test_consensus_boost_respects_custom_ceiling():
    # If a caller wants to be more conservative, the ceiling can be lowered.
    assert _consensus_boost(0.8, 5, ceiling=0.85) == 0.85
    assert _consensus_boost(0.8, 1, ceiling=0.85) == 0.8


def test_consensus_boost_negative_base_passthrough():
    # We never produce negative confidences elsewhere, but the helper must
    # still behave sanely (no exception, addition still works).
    assert _consensus_boost(-0.1, 2) == pytest.approx(0.0)
