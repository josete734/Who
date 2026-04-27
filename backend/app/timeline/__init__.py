"""Chronological timeline subsystem (Wave 2/B4).

Extracts dated events from findings, deduplicates them, and renders
them grouped by month/day for UI consumption.
"""
from app.timeline.extractor import TimelineEvent, extract_events
from app.timeline.aggregator import build_timeline
from app.timeline.renderer import render_timeline

__all__ = ["TimelineEvent", "extract_events", "build_timeline", "render_timeline"]
