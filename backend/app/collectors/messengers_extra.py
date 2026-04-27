"""Extra messenger presence collectors (Wave 3 / C12).

Covers a small set of legacy/alt messengers that still expose passive
public surfaces:

* **Skype** — ``https://login.skype.com/json/validator?new_username={u}``
  is the live-username validator used by the signup flow. It returns a
  small JSON payload telling whether ``new_username`` is *available* for
  registration. If the username is **not available**, it usually means
  an account with that handle already exists (existence signal).
* **ICQ** — ``https://icq.com/people/{u}`` renders an HTML profile page
  which we scrape (BeautifulSoup) for a display name and avatar URL.

Findings emitted:

* ``MessengerAccountExists`` — boolean presence on the platform.
* ``display_name`` / ``avatar_url`` — when scraped from ICQ.

Confidence is capped at 0.5 for HTML-scraped sources because layouts
change frequently and false positives are easy.

Out of scope (intentionally skipped):

* **Threema** — paid, no public lookup.
* **Signal** — closed, end-to-end only, no presence endpoint.
* **WeChat** — N/A: there is no public unauthenticated presence API
  for WeChat usernames; lookup requires the mobile app.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput


SKYPE_VALIDATOR_URL = "https://login.skype.com/json/validator"
ICQ_PROFILE_URL = "https://icq.com/people/{username}"


@register
class MessengersExtraCollector(Collector):
    name = "messengers_extra"
    category = "social"
    needs = ("username", "phone")
    timeout_seconds = 20
    description = "Skype + ICQ passive presence checks (HTML scrape; Threema/Signal/WeChat out of scope)."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        u = (input.username or "").lstrip("@").strip()
        if not u:
            return

        async with client(timeout=12) as c:
            # --- Skype validator ---------------------------------------------
            try:
                r = await c.get(SKYPE_VALIDATOR_URL, params={"new_username": u})
            except httpx.HTTPError:
                r = None

            if r is not None and r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                # The validator returns a status code; non-zero / "not available"
                # means the handle is taken (== an account exists).
                status = data.get("status")
                markup = (data.get("markup") or "").lower()
                taken = False
                if isinstance(status, int) and status != 0:
                    taken = True
                elif "not available" in markup or "already" in markup or "taken" in markup:
                    taken = True
                if taken:
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="MessengerAccountExists",
                        title=f"Skype handle posiblemente registrado: {u}",
                        url=f"https://www.skype.com/en/",
                        confidence=0.45,
                        payload={
                            "platform": "skype",
                            "username": u,
                            "messenger_account_exists": True,
                            "source": "skype_validator",
                            "raw_status": status,
                        },
                    )

            # --- ICQ HTML scrape ---------------------------------------------
            icq_url = ICQ_PROFILE_URL.format(username=u)
            try:
                ricq = await c.get(icq_url, headers={"Accept": "text/html"})
            except httpx.HTTPError:
                ricq = None

            if ricq is not None and ricq.status_code == 200 and ricq.text:
                soup = BeautifulSoup(ricq.text, "html.parser")
                display_name = None
                avatar_url = None

                og_title = soup.find("meta", attrs={"property": "og:title"})
                if og_title and og_title.get("content"):
                    display_name = og_title["content"].strip() or None
                if not display_name:
                    h1 = soup.find("h1")
                    if h1 and h1.get_text(strip=True):
                        display_name = h1.get_text(strip=True)

                og_image = soup.find("meta", attrs={"property": "og:image"})
                if og_image and og_image.get("content"):
                    avatar_url = og_image["content"].strip() or None
                if not avatar_url:
                    img = soup.find("img", attrs={"class": lambda v: v and "avatar" in v.lower()})
                    if img and img.get("src"):
                        avatar_url = img["src"]

                if display_name or avatar_url:
                    yield Finding(
                        collector=self.name,
                        category="username",
                        entity_type="MessengerAccountExists",
                        title=f"ICQ perfil encontrado: {display_name or u}",
                        url=icq_url,
                        confidence=0.5,
                        payload={
                            "platform": "icq",
                            "username": u,
                            "messenger_account_exists": True,
                            "display_name": display_name,
                            "avatar_url": avatar_url,
                            "source": "icq_html",
                        },
                    )
