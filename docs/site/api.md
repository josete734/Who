# API Reference

The backend is a FastAPI app exposed through Caddy at `/api`. OpenAPI docs are
served at `/api/docs` and `/api/redoc` when `DEBUG=true`.

## Authentication

- **Operator UI / extension** — session cookie issued after Caddy basic-auth.
- **Programmatic clients (CLI, MCP)** — `Authorization: Bearer <api-token>`.

Tokens are minted from the admin panel and scoped per case-group.

## Core endpoints

### `POST /api/cases`

Create a new investigation case.

```json
{
  "title": "Acme Corp due diligence",
  "purpose": "kyc",
  "retention_days": 90,
  "input": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "domain": "example.com"
  }
}
```

Returns `{ "case_id": "...", "status": "queued" }`.

### `GET /api/cases/{case_id}`

Returns case metadata, collector statuses, and aggregate counts.

### `GET /api/cases/{case_id}/findings`

Streams findings as they are produced (server-sent events). Each event carries:

```json
{
  "collector": "crtsh_domain",
  "category": "domain",
  "entity_type": "certificate",
  "title": "*.example.com",
  "url": "https://crt.sh/?id=...",
  "confidence": 0.85,
  "fingerprint": "ab12...",
  "payload": { "issuer": "Let's Encrypt" }
}
```

### `POST /api/cases/{case_id}/export`

Produces a signed ZIP containing JSON, CSV, and a PDF report.

### `DELETE /api/cases/{case_id}`

Triggers a GDPR right-to-erasure flow. See [GDPR](gdpr.md).

## Admin endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/collectors` | List registered collectors and their resilience config. |
| `POST` | `/api/admin/collectors/{name}/disable` | Temporarily disable a collector. |
| `GET` | `/api/admin/audit` | Tail the audit log. |
| `POST` | `/api/admin/cache/purge` | Invalidate cache namespaces. |

## Errors

All errors are returned as RFC 7807 problem documents:

```json
{
  "type": "https://osint.example/errors/collector-failure",
  "title": "Collector failed",
  "status": 502,
  "detail": "crtsh_domain timed out after 3 retries",
  "instance": "/api/cases/abc123/findings"
}
```
