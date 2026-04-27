"""Pytest setup: make the parent directory importable as the `mcp`
namespace package so `from mcp import server` resolves to ../server.py."""
from __future__ import annotations

import sys
from pathlib import Path

# Add the directory *containing* the `mcp/` directory to sys.path.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
