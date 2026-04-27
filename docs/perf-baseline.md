# Performance & Cache Baseline

Status: estimated baseline. Real per-case figures should be regenerated from
`collector_duration_seconds_bucket` and `collector_runs_total{status="timeout"}`
once metrics are populated for the latest case.

## Top-10 slowest collectors (estimated, p95)

Based on declared `timeout_seconds` and category, the highest expected p95s:

1. `gemini_websearch` (LLM grounding, ~30 - 60 s)
2. `linkedin_public` (heavy scrape)
3. `instagram_public` (multi-step scrape)
4. `garmin_connect_public`
5. `polar_flow_public`
6. `mapmyrun`
7. `alltrails`
8. `companies_house_uk`
9. `borme` / `boe` (BOE PDFs)
10. `crtsh` (large CT result sets)

## Top-10 most-timeout-prone collectors

1. `linkedin_public`
2. `instagram_public`
3. `gemini_websearch`
4. `dehashed`
5. `hibp` (rate-limited)
6. `holehe`
7. `phoneinfoga`
8. `pastebin_search`
9. `archive_advanced`
10. `domain_photon`

## Theoretical cache hit rate

If `auto_cache` is enabled across all deterministic collectors, expected
hit rate over a 24 h window with 30 % query overlap:

| Tier                                 | Expected hit % |
| ------------------------------------ | -------------- |
| Deterministic (gravatar, dns_mx, crtsh, wikipedia, semantic_scholar) | 60 - 75 % |
| Username enumeration (whatsmyname, maigret, holehe) | 50 - 65 % |
| LLM-grounded (gemini_websearch)       | 20 - 30 %      |
| Volatile (reddit live, bluesky)       | < 10 %         |

Global blended estimate: ~40 - 50 %.

## Recommendations (priority order)

1. Wrap `gravatar`, `dns_mx`, `crtsh`, `wikipedia`, `semantic_scholar`
   with `@auto_cache(category=...)` -- highest determinism, immediate win.
2. Enable `@auto_cache(category="username")` on `whatsmyname` (already)
   and extend to `maigret`, `holehe`.
3. Apply backoff to `linkedin_public` / `instagram_public` (already
   covered by the new timeout-streak logic in the orchestrator).
4. Skip caching for `reddit`, `bluesky`, `gemini_websearch` (low TTL
   benefit; staleness risk).
5. Add an arq cron calling `refresh_cache_hit_rate()` every 60 s so the
   `cache_hit_rate{collector}` gauge stays fresh in Prometheus.
