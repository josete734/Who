"""Holehe collector: runs the `holehe` CLI against an email address.

Holehe checks ~120 services for account registration via the forgot-password flow.
Free, no auth. Installed in the container.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput


@register
class HoleheCollector(Collector):
    name = "holehe"
    category = "email"
    needs = ("email",)
    timeout_seconds = 180
    description = "Holehe: enumerates ~120 services where the email is registered."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.email
        if not shutil.which("holehe"):
            raise RuntimeError("holehe CLI not available in container")

        proc = await asyncio.create_subprocess_exec(
            "holehe", "--only-used", "--no-color", input.email,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("holehe timeout") from None

        stdout = stdout_b.decode("utf-8", errors="ignore")

        # holehe output lines look like: "[+] amazon.com"
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("[+]"):
                continue
            service = line[3:].strip()
            if not service:
                continue
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="ServiceAccount",
                title=f"Cuenta detectada en {service}",
                url=f"https://{service}" if "." in service else None,
                confidence=0.7,
                payload={"service": service, "email": input.email},
            )
