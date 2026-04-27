# Runbook: Database Migration Failed

**Severity:** P1 if production is down, P2 if a deploy is rolled back cleanly.

## Symptoms

- `alembic upgrade head` exits non-zero during deploy.
- Backend pods crashloop with `relation "..." does not exist` or
  `column "..." does not exist`.
- `/api/health/ready` reports `db: degraded`.

## Triage

1. Capture the failing revision and error:
   ```bash
   docker compose run --rm backend alembic current
   docker compose run --rm backend alembic history | head -20
   docker compose logs backend | grep -A3 alembic | tail -40
   ```
2. Determine whether the migration partially applied:
   ```bash
   docker compose exec postgres psql -U osint -d osint \
     -c "select version_num from alembic_version;"
   ```
3. Snapshot the database **before** any further action:
   ```bash
   ./scripts/backup.sh --tag pre-rollback-$(date +%s)
   ```

## Decision tree

- **Forward-fix possible?** (e.g. typo in migration) — write a follow-up
  revision, redeploy. Preferred path.
- **Migration is destructive and partially applied?** — restore from the
  snapshot above, then redeploy the previous image.
- **Migration is purely additive but failed mid-way?** — manually apply the
  remaining DDL inside a transaction, then `alembic stamp head`.

## Recovery — forward-fix

```bash
# create a fixup revision locally
alembic revision -m "fixup <broken-rev>"
# edit, commit, redeploy
docker compose run --rm backend alembic upgrade head
```

## Recovery — rollback

```bash
# downgrade one step (only if the migration's downgrade() is implemented):
docker compose run --rm backend alembic downgrade -1

# or restore the snapshot:
./scripts/restore.sh pre-rollback-<timestamp>
```

Then redeploy the previous image tag and verify:

```bash
curl -s /api/health/ready | jq
./scripts/smoke.sh
```

## Postmortem checklist

- [ ] Was the migration tested against a copy of production data?
- [ ] Does it have a working `downgrade()`?
- [ ] Did CI run `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`?
- [ ] Is the migration idempotent (uses `IF NOT EXISTS`)?
- [ ] Add a regression test in `backend/tests/migrations/`.

## Prevention

- All migrations must be reviewed by a second engineer.
- Long-running DDL (>5s) must be split with `op.execute("SET lock_timeout = '5s'")`.
- Use `CREATE INDEX CONCURRENTLY` for new indexes on large tables.
