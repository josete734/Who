"""Passive subdomain enumeration via subfinder/amass binaries (Wave 4 / A4.2).

Self-hosted, no third-party API keys. If neither binary is available on
``$PATH`` the collector silently emits nothing.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import AsyncIterator

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput


logger = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_.-]+\.[A-Za-z]{2,}$")


async def _run(cmd: list[str], timeout: float) -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as e:
        logger.debug("subdomain_passive: failed to spawn %s: %s", cmd[0], e)
        return []
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return []
    text = (stdout or b"").decode("utf-8", errors="ignore")
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lower()
        # amass enum -passive prints "name (source)" style sometimes; take first token.
        line = line.split()[0] if line else ""
        if line and _DOMAIN_RE.match(line):
            out.append(line)
    return out


@register
class SubdomainPassiveCollector(Collector):
    name = "subdomain_passive"
    category = "domain"
    needs = ("domain",)
    timeout_seconds = 120
    description = "Passive subdomain enumeration via subfinder/amass (self-hosted)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.domain
        domain = input.domain.strip().lower()
        if not _DOMAIN_RE.match(domain):
            return

        subs: set[str] = set()

        if shutil.which("subfinder"):
            for sub in await _run(
                ["subfinder", "-d", domain, "-silent", "-timeout", "30"],
                timeout=60.0,
            ):
                subs.add(sub)

        if shutil.which("amass"):
            for sub in await _run(
                ["amass", "enum", "-passive", "-d", domain, "-timeout", "60"],
                timeout=120.0,
            ):
                subs.add(sub)

        for sub in sorted(subs):
            if sub == domain or not sub.endswith("." + domain):
                # Keep only true subdomains.
                continue
            yield Finding(
                collector=self.name,
                category=self.category,
                entity_type="subdomain",
                title=f"Subdomain: {sub}",
                url=f"https://{sub}",
                confidence=0.7,
                payload={
                    "domain": domain,
                    "subdomain": sub,
                    "source": "subfinder+amass",
                },
            )


__all__ = ["SubdomainPassiveCollector"]
