# who — Plan maestro: "la mejor herramienta OSINT del mundo"

Objetivo: convertir `who` en una plataforma OSINT brutal, con cobertura, fiabilidad,
inteligencia y UX superiores a sherlock/maigret/spiderfoot/IntelOwl combinados.

## Principios

1. **Pivot automático**: cada hallazgo dispara nuevos colectores (cascada).
2. **Entity resolution**: hallazgos se reconcilian en un grafo de identidad con score.
3. **AI-driven**: un agente LLM decide qué colectores correr y cuándo parar.
4. **Resiliencia**: timeouts, retries, circuit breakers, caché, sin colectores que tumben el case.
5. **Observabilidad**: cada colector mide latencia, tasa de éxito, hallazgos, fuente.
6. **Cumplimiento**: base legal obligatoria, audit log inmutable, derecho al olvido.
7. **Distribución**: API REST + SSE + MCP + extensión navegador + export PDF/STIX/JSON.

## Oleadas

### Wave 1 — Foundations & Fixes (paralelo, 8 agentes)
- **A1** — Resiliencia: circuit breaker + timeouts + retries en `base.py`; arreglar maigret/wayback/orcid.
- **A2** — Cache layer Redis con TTL por tipo de colector.
- **A3** — Auth v2: API keys por usuario + rate limiting + CORS endurecido.
- **A4** — Test harness con VCR.py + fixtures para 10 colectores críticos.
- **A5** — Audit log inmutable + base legal obligatoria por case (GDPR).
- **A6** — Entity resolution engine: dedupe + scoring probabilístico cross-collector.
- **A7** — Observabilidad: métricas Prometheus + structured logging por colector.
- **A8** — Pivot automático: motor de cascada (email descubierto → dispara holehe/hibp/...).

### Wave 2 — Innovation Core (paralelo, 8 agentes)
- **B1** — Grafo de identidad (Postgres + recursive CTE / Apache AGE) + endpoint `/graph`.
- **B2** — AI Investigator: agente LLM autónomo que orquesta colectores con tool-use.
- **B3** — Photo gallery + face clustering (face_recognition lib).
- **B4** — Timeline aggregator: eventos cronológicos de todas las fuentes.
- **B5** — Geographic heatmap: agregación de señales geo (IP, registros, posts).
- **B6** — UI nueva: grafo Cytoscape + tabs (Graph/Timeline/Photos/Geo/Raw).
- **B7** — Confidence scoring visible en UI por hallazgo + fuente.
- **B8** — Pattern miner: genera variantes email/username y verifica.

### Wave 3 — Coverage Expansion (paralelo, 12 agentes)
- **C1**–**C12** — Nuevos colectores: LinkedIn, X/Twitter (nitter), Pastebin, Discord lookup, Dehashed, Snusbase, ENS/Etherscan, EU registries (companies house, opencorporates), reverse image (TinEye/Yandex), domain CT monitor, breach combo lists, archive.org cdx avanzado.

### Wave 4 — Distribution (paralelo, 6 agentes)
- **D1** — Servidor MCP exponiendo cases/findings/run como tools para Claude/Cursor.
- **D2** — Extensión navegador (Chrome/Firefox) con investigación contextual.
- **D3** — Export PDF profesional + STIX 2.1 + MISP feed.
- **D4** — Webhooks de eventos + watchlists con re-run programado.
- **D5** — Sistema de reglas/alertas (cambios entre runs).
- **D6** — CLI `who` distribuible (pipx install who-osint).

### Wave 5 — Hardening (paralelo, 6 agentes)
- **E1** — Proxy rotator + UA rotation para colectores web-scrape.
- **E2** — i18n UI (es/en/ca).
- **E3** — RBAC + multi-tenant (orgs/teams/cases).
- **E4** — Compactación: tasks Arq con backoff exponencial + dead letter queue.
- **E5** — Documentación completa MkDocs Material + API docs OpenAPI.
- **E6** — Suite e2e Playwright contra producción staging.

## Métricas de éxito

- 70+ colectores activos.
- p95 case completion < 90s con timeouts agresivos.
- 0 colectores que tumben el case por excepción.
- Cobertura tests > 60%.
- Cross-validation con confidence > 0.8 en al menos 5 ejes (email↔username, etc.).
- Score de UX: grafo navegable, fotos clusterizadas, timeline navegable.
