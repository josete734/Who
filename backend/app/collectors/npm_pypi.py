"""npm and PyPI author/maintainer lookups."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


@register
class NpmAuthorCollector(Collector):
    name = "npm"
    category = "code"
    needs = ("username",)
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        if not input.username:
            return
        u = input.username.lstrip("@")
        async with client(timeout=12) as c:
            try:
                r = await c.get(f"https://www.npmjs.com/~{u}", headers={"Accept": "application/json"})
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except ValueError:
            return
        pkgs = data.get("packages", {}).get("objects", []) or data.get("packages", []) or []
        if not pkgs:
            return
        yield Finding(
            collector=self.name,
            category="username",
            entity_type="NpmProfile",
            title=f"npm: ~{u} ({len(pkgs)} paquetes)",
            url=f"https://www.npmjs.com/~{u}",
            confidence=0.85,
            payload={"packages": pkgs[:25]},
        )


@register
class PypiAuthorCollector(Collector):
    name = "pypi"
    category = "code"
    needs = ("username", "email")
    timeout_seconds = 15

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        async with client(timeout=12) as c:
            if input.username:
                u = input.username.lstrip("@")
                try:
                    r = await c.get(f"https://pypi.org/user/{u}/")
                except httpx.HTTPError:
                    r = None
                if r is not None and r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    pkgs = [a.get_text(strip=True) for a in soup.select("h3.package-snippet__title")]
                    if pkgs:
                        yield Finding(
                            collector=self.name,
                            category="username",
                            entity_type="PyPIProfile",
                            title=f"PyPI: {u} ({len(pkgs)} paquetes)",
                            url=f"https://pypi.org/user/{u}/",
                            confidence=0.85,
                            payload={"packages": pkgs[:30]},
                        )
