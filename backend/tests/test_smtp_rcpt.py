"""Tests for smtp_rcpt collector — DNS + aiosmtplib are mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.collectors.smtp_rcpt import SmtpRcptCollector
from app.schemas import SearchInput


def _mock_dns(hosts):
    """Build a mock dns.asyncresolver.Resolver returning given MX hosts."""
    fake_records = [MagicMock(preference=10, exchange=h) for h in hosts]
    for r, h in zip(fake_records, hosts, strict=False):
        r.exchange.__str__ = lambda self=h: h  # type: ignore[method-assign]

    resolver = MagicMock()
    resolver.timeout = 5.0
    resolver.lifetime = 5.0
    resolver.resolve = AsyncMock(return_value=fake_records)
    return resolver


@pytest.mark.asyncio
async def test_smtp_rcpt_skips_known_provider():
    findings = [f async for f in SmtpRcptCollector().run(SearchInput(email="x@gmail.com"))]
    assert findings == []


@pytest.mark.asyncio
async def test_smtp_rcpt_deliverable():
    smtp_mock = MagicMock()
    smtp_mock.connect = AsyncMock()
    smtp_mock.ehlo = AsyncMock()
    smtp_mock.mail = AsyncMock()
    smtp_mock.rcpt = AsyncMock(return_value=(250, "OK"))
    smtp_mock.quit = AsyncMock()

    with (
        patch("dns.asyncresolver.Resolver", return_value=_mock_dns(["mx.example.org"])),
        patch("aiosmtplib.SMTP", return_value=smtp_mock),
    ):
        findings = [
            f async for f in SmtpRcptCollector().run(SearchInput(email="user@example.org"))
        ]

    assert len(findings) == 1
    assert findings[0].payload["verdict"] == "deliverable"
    assert findings[0].payload["rcpt_code"] == 250


@pytest.mark.asyncio
async def test_smtp_rcpt_not_deliverable():
    smtp_mock = MagicMock()
    smtp_mock.connect = AsyncMock()
    smtp_mock.ehlo = AsyncMock()
    smtp_mock.mail = AsyncMock()
    smtp_mock.rcpt = AsyncMock(return_value=(550, "No such user"))
    smtp_mock.quit = AsyncMock()

    with (
        patch("dns.asyncresolver.Resolver", return_value=_mock_dns(["mx.example.org"])),
        patch("aiosmtplib.SMTP", return_value=smtp_mock),
    ):
        findings = [
            f async for f in SmtpRcptCollector().run(SearchInput(email="ghost@example.org"))
        ]

    assert len(findings) == 1
    assert findings[0].payload["verdict"] == "not_deliverable"


@pytest.mark.asyncio
async def test_smtp_rcpt_tcp_error_emits_unknown():
    smtp_mock = MagicMock()
    smtp_mock.connect = AsyncMock(side_effect=ConnectionError("boom"))

    with (
        patch("dns.asyncresolver.Resolver", return_value=_mock_dns(["mx.example.org"])),
        patch("aiosmtplib.SMTP", return_value=smtp_mock),
    ):
        findings = [
            f async for f in SmtpRcptCollector().run(SearchInput(email="user@example.org"))
        ]

    assert len(findings) == 1
    assert findings[0].payload["verdict"] == "unknown"
    assert "error" in findings[0].payload
