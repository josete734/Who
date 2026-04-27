"""Generic VCR-backed harness for exercising Collector subclasses.

Usage in a test:

    from app.collectors.gravatar import GravatarCollector
    from tests.collector_harness import run_collector_with_cassette

    async def test_gravatar():
        findings = await run_collector_with_cassette(
            GravatarCollector,
            {"email": "test@example.com"},
            cassette_name="gravatar_basic",
        )
        assert isinstance(findings, list)

The harness:
  * Builds a minimal `SearchInput` from a plain dict.
  * Wraps the call in a VCR cassette (replay by default).
  * Filters auth headers and *_API_KEY / api_key / token query params.
  * Returns the collected list of Finding objects.

Record cassettes locally with:

    VCR_RECORD=once pytest backend/tests/test_collectors_smoke.py
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import vcr

from app.collectors.base import Collector, Finding
from app.schemas import SearchInput


CASSETTE_DIR = Path(__file__).resolve().parent / "fixtures" / "cassettes"
CASSETTE_DIR.mkdir(parents=True, exist_ok=True)


_FILTERED_QUERY_PARAMS = [
    "api_key", "apikey", "key", "token", "access_token",
    "auth", "authorization", "client_secret",
]
_FILTERED_HEADERS = [
    "authorization", "x-api-key", "x-auth-token", "cookie", "set-cookie",
]


def _build_vcr() -> vcr.VCR:
    record_mode = os.environ.get("VCR_RECORD", "none")
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        match_on=("method", "scheme", "host", "port", "path", "query"),
        filter_headers=_FILTERED_HEADERS,
        filter_query_parameters=_FILTERED_QUERY_PARAMS,
        decode_compressed_response=True,
    )


def build_search_input(case_dict: dict[str, Any]) -> SearchInput:
    """Construct a SearchInput from a plain dict, ignoring unknown keys."""
    return SearchInput.model_validate(case_dict)


async def run_collector_with_cassette(
    collector_class: type[Collector],
    case_dict: dict[str, Any],
    cassette_name: str,
) -> list[Finding]:
    """Run a collector inside a VCR cassette and return its findings.

    Parameters
    ----------
    collector_class:
        The Collector subclass under test (uninstantiated).
    case_dict:
        Dict-shaped SearchInput payload (e.g. {"email": "x@y.z"}).
    cassette_name:
        File stem for the cassette (.yaml will be appended by VCR).
    """
    if not cassette_name.endswith((".yaml", ".yml")):
        cassette_name = f"{cassette_name}.yaml"

    search_input = build_search_input(case_dict)
    collector = collector_class()
    findings: list[Finding] = []

    my_vcr = _build_vcr()
    with my_vcr.use_cassette(cassette_name):
        async for finding in collector.run(search_input):
            findings.append(finding)
    return findings
