# Writing a Collector

A collector is a Python class that yields `Finding` objects from one external
data source. The orchestrator handles concurrency, retries, deduplication, and
persistence — your code only has to fetch and parse.

## Minimum viable collector

```python
# backend/app/collectors/example.py
from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.schemas import SearchInput


@register
class ExampleCollector(Collector):
    name = "example"
    category = "social"
    needs = ("username",)
    timeout_seconds = 30
    max_retries = 2
    description = "Looks up a username on example.com."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.get(f"https://example.com/u/{input.username}")
        if r.status_code != 200:
            return
        yield Finding(
            collector=self.name,
            category=self.category,
            entity_type="profile",
            title=f"example.com/{input.username}",
            url=str(r.url),
            confidence=0.9,
            payload={"http_status": r.status_code},
        )
```

That's it. Importing the module registers the class — no manual wiring.

## Required class attributes

| Attribute | Type | Purpose |
|---|---|---|
| `name` | `str` | Unique key. Used in URLs, audit log, and admin toggles. |
| `category` | `str` | Group in the UI (`social`, `code`, `infra`, ...). |
| `needs` | `tuple[str, ...]` | `SearchInput` fields that make the collector applicable. Empty means always applicable. |
| `requires_all` | `bool` | If `True`, **all** `needs` fields must be present. Default `False` (any-of). |
| `description` | `str` | One-line summary used by `docs/site/collectors.md`. |

## Resilience knobs

Override on the class to tune retries, timeouts, and circuit breaker:

```python
timeout_seconds = 60
max_retries = 1
circuit_breaker_threshold = 5
```

The orchestrator wraps `run()` in `app.collectors.resilience.run_with_resilience`
which honours these values. Failures are recorded as `CollectorFailure` rows
without aborting the case.

## Best practices

- **Stream early.** `yield` the first `Finding` as soon as you can — the UI
  shows results live.
- **Set realistic confidence.** `0.9` for high-signal sources (certificate
  transparency, ORCID), `0.5` for fuzzy matches (web search dorks).
- **Stable fingerprints.** `Finding.fingerprint()` defaults to a hash of
  `category|entity_type|url-or-title`. If your URL is volatile, override
  `payload["fingerprint_hint"]` and document why.
- **No PII in logs.** Log identifiers as their SHA-256 prefix.
- **Honour rate limits.** Use the shared `httpx.AsyncClient` patterns; respect
  `Retry-After`. See [runbooks/collector_429.md](runbooks/collector_429.md).
- **Add a test.** Drop a fixture in `backend/tests/collectors/` covering both
  the happy path and an error path.

## Testing locally

```bash
docker compose run --rm backend pytest backend/tests/collectors/test_example.py
```

To smoke-test against a live case:

```bash
./cli/osint case create --input '{"username":"torvalds"}' --collectors example
```
