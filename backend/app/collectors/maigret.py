"""Maigret collector: Sherlock on steroids, 3000+ sites with metadata extraction."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.schemas import SearchInput

logger = logging.getLogger("osint.collectors.maigret")


@register
class MaigretCollector(Collector):
    name = "maigret"
    category = "username"
    needs = ("username",)
    # Aggressive default — production logs showed 600s hangs with no findings.
    # The resilience wrapper enforces this as a wall-clock; we *also* enforce
    # it on the subprocess directly so we can salvage partial output on kill.
    timeout_seconds = 90
    max_retries = 0  # do not retry — maigret is expensive and rarely transient
    description = "Maigret: username across 3000+ sites, extracts metadata."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        if not shutil.which("maigret"):
            logger.info("maigret CLI not installed — skipping", extra={"collector": self.name})
            return

        s = get_settings()
        # Per-site timeout for maigret itself; cap so it can't accumulate
        # past our wall clock.
        per_site_timeout = min(int(getattr(s, "maigret_timeout", 10) or 10), 10)

        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                "maigret",
                "--json", "ndjson",
                "--no-color",
                "--no-progressbar",
                "--timeout", str(per_site_timeout),
                "--folderoutput", str(tmp),
                "-T", "300",  # top-300 sites — keeps wall time bounded
                input.username,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                # Reserve ~5s headroom inside our class timeout for parsing.
                await asyncio.wait_for(
                    proc.communicate(), timeout=max(self.timeout_seconds - 5, 10)
                )
            except asyncio.TimeoutError:
                timed_out = True
                logger.warning(
                    "maigret subprocess timeout — killing and salvaging partial output",
                    extra={"collector": self.name, "username": input.username},
                )
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                # Drain so we don't leak the process.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

            # Parse whatever ndjson maigret managed to flush before exit/kill.
            candidates = list(Path(tmp).glob("*.ndjson"))
            if not candidates:
                if timed_out:
                    # Surface as a soft failure to the resilience layer.
                    raise asyncio.TimeoutError("maigret killed before producing output")
                return

            yielded = 0
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
                    status = entry.get("status") or {}
                    if not isinstance(status, dict):
                        continue
                    if status.get("status") != "CLAIMED":
                        continue
                    site = entry.get("site") or status.get("site") or "unknown"
                    url = status.get("url") or entry.get("url_user")
                    ids_info = status.get("ids_usernames") or {}
                    yielded += 1
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
                            "partial": timed_out,
                        },
                    )
            logger.info(
                "maigret done",
                extra={
                    "collector": self.name,
                    "findings": yielded,
                    "timed_out": timed_out,
                },
            )
