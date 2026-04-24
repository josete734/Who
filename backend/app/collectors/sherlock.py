"""Sherlock collector: hunts a username across 400+ platforms."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.schemas import SearchInput


@register
class SherlockCollector(Collector):
    name = "sherlock"
    category = "username"
    needs = ("username",)
    timeout_seconds = 300
    description = "Sherlock: username across ~400 platforms."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        if not shutil.which("sherlock"):
            raise RuntimeError("sherlock CLI not installed")

        s = get_settings()
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            cmd = [
                "sherlock",
                "--timeout", str(s.sherlock_timeout),
                "--print-found",
                "--folderoutput", str(outdir),
                input.username,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("sherlock timeout") from None

            stdout = stdout_b.decode("utf-8", errors="ignore")

            # Sherlock emits "[+] SiteName: URL" for found hits
            for line in stdout.splitlines():
                line = line.strip()
                if not line.startswith("[+]"):
                    continue
                try:
                    body = line[3:].strip()
                    site, url = body.split(":", 1)
                    url = url.strip()
                    if not url.startswith("http"):
                        continue
                except ValueError:
                    continue
                yield Finding(
                    collector=self.name,
                    category="username",
                    entity_type="SocialProfile",
                    title=f"{site.strip()}: {input.username}",
                    url=url,
                    # Conservative: Sherlock has known false-positive rate (~10-15%) vs
                    # Cloudflare-protected sites. Confidence stays medium.
                    confidence=0.6,
                    payload={
                        "platform": site.strip(),
                        "username": input.username,
                        "note": "Sherlock acierta ~85% en sitios sin protección. Si Maigret también confirma, súbelo a alta confianza.",
                    },
                )
