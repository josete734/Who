"""Smoke tests for a handful of stable collectors.

These tests run in pure-replay mode by default (`VCR_RECORD` unset). On a
fresh checkout the cassettes do not exist yet; run them once with
`VCR_RECORD=once` to record:

    VCR_RECORD=once pytest backend/tests/test_collectors_smoke.py

After that, regular `pytest` invocations replay the recorded fixtures
and never touch the network.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.collector_harness import CASSETTE_DIR, run_collector_with_cassette


def _cassette_required(name: str) -> None:
    """Skip the test if the cassette is missing and we're not recording."""
    if os.environ.get("VCR_RECORD", "none") != "none":
        return
    path = CASSETTE_DIR / f"{name}.yaml"
    if not path.exists():
        pytest.skip(
            f"Cassette {path.name} not recorded yet. "
            f"Run with VCR_RECORD=once to record."
        )


async def test_gravatar_smoke():
    from app.collectors.gravatar import GravatarCollector

    name = "gravatar_test_example_com"
    _cassette_required(name)
    findings = await run_collector_with_cassette(
        GravatarCollector,
        {"email": "test@example.com"},
        cassette_name=name,
    )
    assert isinstance(findings, list)
    for f in findings:
        assert f.collector == "gravatar"
        assert f.category == "email"


async def test_crtsh_domain_smoke():
    from app.collectors.crtsh import CrtShCollector  # type: ignore[attr-defined]

    name = "crtsh_example_com"
    _cassette_required(name)
    findings = await run_collector_with_cassette(
        CrtShCollector,
        {"domain": "example.com"},
        cassette_name=name,
    )
    assert isinstance(findings, list)


async def test_dns_mx_smoke():
    from app.collectors.dns_mx import DnsMxCollector  # type: ignore[attr-defined]

    name = "dns_mx_example_com"
    _cassette_required(name)
    findings = await run_collector_with_cassette(
        DnsMxCollector,
        {"domain": "example.com"},
        cassette_name=name,
    )
    assert isinstance(findings, list)


async def test_wayback_smoke():
    # Module name guess: wayback.py — collector class discovered at import time.
    from app.collectors import wayback  # type: ignore

    collector_cls = next(
        getattr(wayback, n) for n in dir(wayback)
        if n.endswith("Collector") and isinstance(getattr(wayback, n), type)
    )
    name = "wayback_example_com"
    _cassette_required(name)
    findings = await run_collector_with_cassette(
        collector_cls,
        {"domain": "example.com"},
        cassette_name=name,
    )
    assert isinstance(findings, list)


async def test_wikidata_smoke():
    from app.collectors import wikidata  # type: ignore

    collector_cls = next(
        getattr(wikidata, n) for n in dir(wikidata)
        if n.endswith("Collector") and isinstance(getattr(wikidata, n), type)
    )
    name = "wikidata_octocat"
    _cassette_required(name)
    findings = await run_collector_with_cassette(
        collector_cls,
        {"full_name": "Ada Lovelace"},
        cassette_name=name,
    )
    assert isinstance(findings, list)
