"""Lightweight site crawler inspired by Photon (s0md3v).

If the user provided `domain`, fetch the homepage and a small set of secondary
pages (about, team, contacto, /#about) and extract emails, phone numbers, and
social media links.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


EMAIL_RX = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
PHONE_RX = re.compile(r"(?:\+?\d[\d\s\.\-]{7,}\d)")
SOCIAL_RX = re.compile(r"https?://(?:www\.)?(twitter\.com|x\.com|linkedin\.com/in/|github\.com/|instagram\.com/|facebook\.com/|youtube\.com/|tiktok\.com/@|t\.me/|bsky\.app/profile/|mastodon\.social/@)[A-Za-z0-9_.\-/]+", re.I)

CANDIDATE_PATHS = ["/", "/about", "/about-us", "/sobre-mi", "/sobre-nosotros", "/contact", "/contacto", "/team", "/equipo", "/imprint", "/aviso-legal", "/legal"]


@register
class DomainPhotonCollector(Collector):
    name = "domain_photon"
    category = "domain"
    needs = ("domain",)
    timeout_seconds = 60
    description = "Photon-style crawl of user's own domain: emails, phones, social links."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        assert input.domain
        base = input.domain.strip()
        if not base.startswith("http"):
            base = "https://" + base.lstrip("/")
        parsed = urlparse(base)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        seen_urls: set[str] = set()
        seen_emails: set[str] = set()
        seen_phones: set[str] = set()
        seen_socials: set[str] = set()

        async with client(timeout=15) as c:
            for path in CANDIDATE_PATHS:
                url = urljoin(origin + "/", path.lstrip("/"))
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                try:
                    r = await c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                    continue
                html = r.text
                soup = BeautifulSoup(html, "lxml")
                text = soup.get_text(" ")

                for m in EMAIL_RX.findall(text):
                    if m.lower() in seen_emails:
                        continue
                    seen_emails.add(m.lower())
                    yield Finding(
                        collector=self.name, category="email", entity_type="EmailOnSite",
                        title=f"Email en {parsed.netloc}: {m}", url=url,
                        confidence=0.8, payload={"email": m, "page": url},
                    )

                for m in PHONE_RX.findall(text):
                    clean = re.sub(r"[\s\.\-]", "", m)
                    if len(clean) < 9 or clean in seen_phones:
                        continue
                    seen_phones.add(clean)
                    yield Finding(
                        collector=self.name, category="phone", entity_type="PhoneOnSite",
                        title=f"Teléfono en {parsed.netloc}: {m.strip()}", url=url,
                        confidence=0.55, payload={"phone_raw": m.strip(), "page": url},
                    )

                for m in SOCIAL_RX.findall(html):
                    full = re.search(SOCIAL_RX, html)
                # Grab the actual URLs matched
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if SOCIAL_RX.search(href):
                        norm = href.rstrip("/")
                        if norm in seen_socials:
                            continue
                        seen_socials.add(norm)
                        yield Finding(
                            collector=self.name, category="social", entity_type="SocialLinkOnSite",
                            title=f"Enlace social en {parsed.netloc}: {norm[:140]}",
                            url=norm, confidence=0.75,
                            payload={"source_page": url},
                        )
