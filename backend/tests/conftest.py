"""Shared pytest configuration for the backend test suite.

Ensures `app.*` imports resolve when running `pytest backend/tests/...`
from the repo root, and exposes pytest-asyncio in auto mode so async
tests can be written without per-test decorators.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the `backend/` directory importable so `from app.collectors...` works.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def pytest_collection_modifyitems(config, items):  # noqa: D401
    """Auto-mark `async def` tests as asyncio tests."""
    import inspect

    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio)


@pytest.fixture(scope="session")
def vcr_record_mode() -> str:
    """Record mode driven by the VCR_RECORD env var.

    - unset / "none": pure replay (CI default)
    - "once":         record missing cassettes, replay existing ones
    - "new_episodes": append new interactions to existing cassettes
    - "all":          re-record everything
    """
    return os.environ.get("VCR_RECORD", "none")
