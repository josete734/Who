"""Wikidata SPARQL collector: find humans matching the full name."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.http_util import client
from app.schemas import SearchInput

SPARQL = """
SELECT ?person ?personLabel ?descLabel ?occupationLabel ?birth ?countryLabel ?article WHERE {
  ?person rdfs:label "%s"@es ;
          wdt:P31 wd:Q5 .
  OPTIONAL { ?person wdt:P106 ?occupation }
  OPTIONAL { ?person wdt:P569 ?birth }
  OPTIONAL { ?person wdt:P27 ?country }
  OPTIONAL {
    ?article schema:about ?person ;
             schema:isPartOf <https://es.wikipedia.org/> .
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". }
}
LIMIT 10
"""


@register
class WikidataCollector(Collector):
    name = "wikidata"
    category = "knowledge"
    needs = ("full_name", "birth_name", "aliases")
    timeout_seconds = 30
    description = "Wikidata SPARQL for notable persons with the same name."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        variants = input.name_variants()
        if not variants:
            return
        for name in variants:
            async for f in self._lookup(name):
                yield f

    async def _lookup(self, name: str) -> AsyncIterator[Finding]:
        query = SPARQL % name.replace('"', "")
        async with client(timeout=20, headers={"Accept": "application/sparql-results+json"}) as c:
            try:
                r = await c.get(
                    "https://query.wikidata.org/sparql",
                    params={"query": query, "format": "json"},
                )
            except httpx.HTTPError:
                return
        if r.status_code != 200:
            return
        try:
            bindings = r.json().get("results", {}).get("bindings", [])
        except ValueError:
            return
        for b in bindings[:10]:
            qid = b.get("person", {}).get("value", "")
            label = b.get("personLabel", {}).get("value")
            yield Finding(
                collector=self.name,
                category="name",
                entity_type="WikidataEntity",
                title=f"Wikidata: {label}",
                url=qid,
                confidence=0.55,
                payload={
                    "qid": qid.rsplit("/", 1)[-1],
                    "occupation": b.get("occupationLabel", {}).get("value"),
                    "birth": b.get("birth", {}).get("value"),
                    "country": b.get("countryLabel", {}).get("value"),
                    "wikipedia_es": b.get("article", {}).get("value"),
                },
            )
