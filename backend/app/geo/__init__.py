"""Geographic intelligence subsystem (Wave 2/B5).

Aggregates location signals (IPs, registry addresses, social posts, github
timezone hints) and projects them onto an H3 hex heatmap for visual
correlation. See ``extractor``, ``aggregator``, ``home_inferer``.

# DEPS: geopy, h3 (h3-py), pycountry
"""
from __future__ import annotations

from app.geo.aggregator import HexAggregate, build_heatmap
from app.geo.extractor import GeoSignal, extract_signals
from app.geo.home_inferer import HomeGuess, infer_home

__all__ = [
    "GeoSignal",
    "extract_signals",
    "HexAggregate",
    "build_heatmap",
    "HomeGuess",
    "infer_home",
]
