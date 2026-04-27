# Deployment

## Quick start (local)

```bash
git clone https://github.com/m3estrategia/osint-tool
cd osint-tool
cp .env.example .env
docker compose up -d
```

Services:

| Service | Port | Notes |
|---|---|---|
| `caddy` | 80 / 443 | TLS termination, basic auth, reverse proxy |
| `backend` | 8000 (internal) | FastAPI + orchestrator |
| `postgres` | 5432 (internal) | Cases, findings, audit log |
| `redis` | 6379 (internal) | Cache + rate-limit buckets |
| `searxng` | 8080 (internal) | Backs `searxng_dorks` collector |

## Production checklist

- [ ] Pin image digests in `docker-compose.yml`.
- [ ] Provide TLS certificates to Caddy (Let's Encrypt or imported).
- [ ] Set strong `ADMIN_PASSWORD`, `JWT_SECRET`, `DB_PASSWORD` via secret manager.
- [ ] Configure off-site Postgres backups (`scripts/backup.sh`).
- [ ] Set per-collector API keys in `.env` (`HIBP_KEY`, `SHODAN_KEY`, ...).
- [ ] Restrict outbound egress to required FQDNs.
- [ ] Enable audit-log shipping to your SIEM.
- [ ] Run `scripts/smoke.sh` after every deploy.

## Migrations

```bash
docker compose run --rm backend alembic upgrade head
```

Failed migration? See [runbooks/migration_failed.md](runbooks/migration_failed.md).

## Scaling

- Backend is stateless; scale horizontally behind Caddy.
- Postgres is the single source of truth — vertical scale + read replicas.
- Long-running collectors (e.g. `maigret`, `sherlock`) can be moved to a
  dedicated worker pool by setting `COLLECTOR_WORKERS=4`.

## Monitoring

- `/api/health` — liveness.
- `/api/health/ready` — readiness, includes DB and Redis.
- `/api/metrics` — Prometheus exposition (per-collector latency, error rate,
  breaker state).

## Backups & DR

Postgres dumps + a snapshot of the combo-list FTS directory are sufficient to
restore a node. Object storage for exports is optional and configured via
`EXPORT_BUCKET`.
