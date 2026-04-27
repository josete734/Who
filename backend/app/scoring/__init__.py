"""Confidence scoring & explainability layer (Wave 2/B7).

Turns aggregated entity-resolution scores into human-readable explanations
with concrete drivers (signals raising confidence) and penalties (signals
lowering confidence). The router/UI uses these to show *why* a finding has
its current confidence.

Public API:
    from app.scoring.engine import explain_entity
    from app.scoring.model import ConfidenceExplanation, Driver, Penalty
    from app.scoring.quality import (
        DEFAULT_QUALITY, get_quality_table, set_collector_weight,
    )
"""
from app.scoring.engine import explain_entity
from app.scoring.model import ConfidenceExplanation, Driver, Penalty

__all__ = ["ConfidenceExplanation", "Driver", "Penalty", "explain_entity"]
