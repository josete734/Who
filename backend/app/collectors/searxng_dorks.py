"""SearXNG-backed dorks.

Runs a set of structured queries against the local SearXNG instance
which meta-searches Google, Bing, DDG, Yandex, Brave, Mojeek, etc. at once.
Free, no API keys.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


def _build_queries(i: SearchInput) -> list[tuple[str, str]]:
    """(label, query) pairs — ~60+ dorks when all inputs present."""
    out: list[tuple[str, str]] = []
    names = i.name_variants()
    email = i.email
    username = i.username
    phone = i.phone
    linkedin = i.linkedin_url

    for name in names:
        ctx = f' "{i.city}"' if i.city else ""
        # Redes profesionales / sociales
        out += [
            ("LinkedIn", f'site:linkedin.com/in/ "{name}"{ctx}'),
            ("Xing", f'site:xing.com "{name}"'),
            ("X/Twitter", f'site:x.com OR site:twitter.com "{name}"'),
            ("Nitter xcancel", f'site:xcancel.com "{name}"'),
            ("Facebook", f'site:facebook.com "{name}"'),
            ("Instagram", f'site:instagram.com "{name}"'),
            ("TikTok", f'site:tiktok.com "{name}"'),
            ("YouTube", f'site:youtube.com "{name}"'),
            ("Mastodon (fediverse)", f'site:mastodon.social OR site:mas.to OR site:hachyderm.io "{name}"'),
            ("Bluesky", f'site:bsky.app "{name}"'),
        ]
        # Oficiales ES
        out += [
            ("BOE España", f'site:boe.es "{name}"'),
            ("BORME España", f'site:boe.es/diario_borme "{name}"'),
            ("BOE subvenciones", f'site:infosubvenciones.es "{name}"'),
            ("Transparencia", f'site:transparencia.gob.es "{name}"'),
            ("Congreso", f'site:congreso.es "{name}"'),
            ("Senado", f'site:senado.es "{name}"'),
        ]
        # Hemerotecas
        out += [
            ("El País", f'site:elpais.com "{name}"'),
            ("La Vanguardia", f'site:lavanguardia.com "{name}"'),
            ("El Mundo", f'site:elmundo.es "{name}"'),
            ("ABC", f'site:abc.es "{name}"'),
            ("20 Minutos", f'site:20minutos.es "{name}"'),
            ("El Confidencial", f'site:elconfidencial.com "{name}"'),
            ("eldiario.es", f'site:eldiario.es "{name}"'),
            ("Público", f'site:publico.es "{name}"'),
        ]
        # Documentos
        out += [
            ("CV/PDF", f'"{name}" (filetype:pdf OR filetype:docx) (CV OR resume OR curriculum)'),
            ("Presentaciones PPT", f'"{name}" filetype:pptx'),
            ("Actas", f'"{name}" (acta OR minuta) filetype:pdf'),
            ("Scribd", f'site:scribd.com "{name}"'),
            ("SlideShare", f'site:slideshare.net "{name}"'),
            ("Issuu", f'site:issuu.com "{name}"'),
        ]
        # Academia
        out += [
            ("Google Scholar", f'site:scholar.google.com "{name}"'),
            ("ResearchGate", f'site:researchgate.net "{name}"'),
            ("Academia.edu", f'site:academia.edu "{name}"'),
            ("ORCID", f'site:orcid.org "{name}"'),
            ("Dialnet", f'site:dialnet.unirioja.es "{name}"'),
        ]
        # Startups
        out += [
            ("Crunchbase", f'site:crunchbase.com "{name}"'),
            ("Wellfound", f'site:wellfound.com OR site:angel.co "{name}"'),
            ("Dealroom", f'site:dealroom.co "{name}"'),
        ]
        # Foros / comunidad
        out += [
            ("Foros ES", f'"{name}" (site:forocoches.com OR site:meneame.net OR site:burbuja.info)'),
            ("Reddit", f'site:reddit.com "{name}"'),
            ("Medium", f'site:medium.com "{name}"'),
            ("Dev.to", f'site:dev.to "{name}"'),
            ("Substack", f'site:substack.com "{name}"'),
        ]
        # Gaming / rutas / varios
        out += [
            ("Strava", f'site:strava.com "{name}"'),
            ("Chess", f'site:chess.com OR site:lichess.org "{name}"'),
            ("Steam", f'site:steamcommunity.com "{name}"'),
            ("Spotify", f'site:open.spotify.com/user "{name}"'),
        ]
        out += [
            ("Pastebin", f'site:pastebin.com "{name}"'),
            ("Gist", f'site:gist.github.com "{name}"'),
        ]

    if email:
        out += [
            ("Email en LinkedIn", f'"{email}" site:linkedin.com'),
            ("Email general", f'"{email}"'),
            ("Email en Pastebin", f'"{email}" site:pastebin.com'),
            ("Email en GitHub", f'"{email}" site:github.com'),
            ("Email en GitLab", f'"{email}" site:gitlab.com'),
            ("Email en Gist", f'"{email}" site:gist.github.com'),
            ("Email en documentos", f'"{email}" (filetype:pdf OR filetype:docx OR filetype:xlsx)'),
        ]

    if username:
        out += [
            ("Username general", f'"{username}"'),
            ("Username foros", f'intext:"{username}" (forum OR thread OR reply)'),
            ("Username en GitHub", f'site:github.com "{username}"'),
            ("Username en GitLab", f'site:gitlab.com "{username}"'),
            ("Username en Reddit", f'site:reddit.com/user/ "{username}"'),
            ("Username en HackerNews", f'site:news.ycombinator.com "{username}"'),
            ("Username en Keybase", f'site:keybase.io "{username}"'),
        ]

    if phone:
        out += [
            ("Teléfono general", f'"{phone}"'),
            ("Teléfono Wallapop", f'"{phone}" site:wallapop.com'),
            ("Teléfono Milanuncios", f'"{phone}" site:milanuncios.com'),
            ("Teléfono Idealista", f'"{phone}" site:idealista.com'),
            ("Teléfono Fotocasa", f'"{phone}" site:fotocasa.es'),
            ("Teléfono coches.net", f'"{phone}" site:coches.net'),
        ]

    if linkedin:
        out += [
            ("LinkedIn URL", f'"{linkedin}"'),
            ("LinkedIn en Wayback", f'"{linkedin}" site:web.archive.org'),
        ]

    return out


@register
class SearXNGDorksCollector(Collector):
    name = "searxng_dorks"
    category = "dork"
    needs = ("full_name", "birth_name", "aliases", "email", "username", "phone", "linkedin_url")
    timeout_seconds = 240
    description = "Multi-engine dorks via self-hosted SearXNG."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        queries = _build_queries(input)
        if not queries:
            return

        s = get_settings()
        async with client(timeout=20) as c:
            for label, q in queries[:60]:
                try:
                    r = await c.get(
                        f"{s.searxng_url}/search",
                        params={"q": q, "format": "json", "language": s.default_language},
                    )
                except httpx.HTTPError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except ValueError:
                    continue
                results = data.get("results", [])[:10]
                for it in results:
                    yield Finding(
                        collector=self.name,
                        category="dork",
                        entity_type="WebResult",
                        title=f"[{label}] {it.get('title', '')[:180]}",
                        url=it.get("url"),
                        confidence=0.45,
                        payload={
                            "engine": it.get("engine"),
                            "query": q,
                            "snippet": (it.get("content") or "")[:600],
                            "label": label,
                        },
                    )
