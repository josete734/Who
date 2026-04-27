"""Pydantic model + hashing helpers for the watchlist table."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Iterable

from pydantic import BaseModel, Field


class Watchlist(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    owner: str = ""
    query_inputs: dict[str, Any] = Field(default_factory=dict)
    schedule_cron: str = "0 * * * *"
    last_run_at: dt.datetime | None = None
    last_results_hash: str | None = None
    enabled: bool = True
    created_at: dt.datetime | None = None


class WatchlistIn(BaseModel):
    owner: str = ""
    query_inputs: dict[str, Any] = Field(default_factory=dict)
    schedule_cron: str = "0 * * * *"
    enabled: bool = True


class WatchlistOut(BaseModel):
    id: uuid.UUID
    owner: str
    query_inputs: dict[str, Any]
    schedule_cron: str
    last_run_at: dt.datetime | None
    last_results_hash: str | None
    enabled: bool
    created_at: dt.datetime | None


def _normalize(item: Any) -> Any:
    """Return a JSON-stable representation of a finding-like dict."""
    if isinstance(item, dict):
        keys = ("fingerprint", "id", "url", "title", "category", "collector")
        for k in keys:
            if k in item and item[k] is not None:
                return f"{k}:{item[k]}"
        return json.dumps(item, sort_keys=True, default=str)
    return str(item)


def hash_findings(findings: Iterable[Any]) -> str:
    """Stable hash over a set of finding-like records.

    Ordering-independent: identical sets always hash the same.
    """
    norm = sorted({_normalize(f) for f in findings})
    h = hashlib.sha256()
    for n in norm:
        h.update(n.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def diff_findings(prev_hash: str | None, current: Iterable[Any]) -> tuple[str, bool]:
    """Return (new_hash, changed)."""
    new_hash = hash_findings(current)
    return new_hash, (prev_hash != new_hash)
