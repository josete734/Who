# OSINT Tool

> Open-source intelligence platform for investigators, compliance teams, and journalists.

## Value Proposition

The OSINT Tool aggregates **50+ public-data collectors** behind a single API and UI, producing
auditable, GDPR-aware investigation cases. It targets Spanish and EU sources first
(BOE, BORME, EU registries) while including the global standards (HIBP, GitHub, certificate
transparency, archive.org, social media, breach indexes).

## Highlights

- **Pluggable collectors** — drop a Python file in `backend/app/collectors/`, declare `name`,
  `category`, `needs`, and the orchestrator will pick it up automatically.
- **Resilient by design** — per-collector circuit breakers, retries, timeouts, and graceful
  failure rows so one flaky source never aborts a case.
- **GDPR first** — purpose tagging, retention windows, right-to-erasure flows, and an audit
  log baked into the data model.
- **Operator UX** — Caddy-fronted web UI, browser extension, MCP bridge, and CLI for analysts.
- **Self-hostable** — `docker compose up` brings up the full stack including SearXNG.

## Where to next

- New here? Read [Architecture](architecture.md) for the big picture.
- Building integrations? Jump to the [API](api.md) reference.
- Adding a source? Follow [Writing Collectors](writing-collectors.md).
- Operating in production? See [Deployment](deployment.md) and the [Runbooks](runbooks/cache_poisoned.md).
