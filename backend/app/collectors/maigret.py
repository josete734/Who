"""Maigret collector: Sherlock on steroids, 3000+ sites with metadata extraction."""
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
class MaigretCollector(Collector):
    name = "maigret"
    category = "username"
    needs = ("username",)
    timeout_seconds = 600
    description = "Maigret: username across 3000+ sites, extracts metadata."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.username
        if not shutil.which("maigret"):
            raise RuntimeError("maigret CLI not installed")

        s = get_settings()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            cmd = [
                "maigret",
                "--json", "ndjson",
                "--no-color",
                "--no-progressbar",
                "--timeout", str(s.maigret_timeout),
                "--folderoutput", str(tmp),
                "-T", "500",  # top-500 sites to keep runtime bounded
                input.username,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("maigret timeout") from None

            # Parse the produced ndjson (first match file found in tmp).
            candidates = list(Path(tmp).glob("*.ndjson")) + list(Path(tmp).glob(f"report_{input.username}*.ndjson"))
            if not candidates:
                return

            for p in candidates:
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = entry.get("status", {})
                    if status.get("status") != "CLAIMED":
                        continue
                    site = entry.get("site") or status.get("site") or "unknown"
                    url = status.get("url") or entry.get("url_user")
                    ids_info = status.get("ids_usernames") or {}
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="SocialProfile",
                        title=f"{site}: {input.username}",
                        url=url,
                        confidence=0.75,
                        payload={
                            "platform": site,
                            "username": input.username,
                            "ids": ids_info,
                            "tags": entry.get("tags", []),
                        },
                    )
