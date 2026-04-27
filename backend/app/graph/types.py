"""Shared dataclasses for the identity graph.

Kept SQLAlchemy-free so projection / tests can import without a DB driver.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    id: uuid.UUID
    case_id: uuid.UUID
    type: str
    key: str
    attrs: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


@dataclass(slots=True)
class GraphEdge:
    id: uuid.UUID
    case_id: uuid.UUID
    src: uuid.UUID
    dst: uuid.UUID
    rel: str
    weight: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)
