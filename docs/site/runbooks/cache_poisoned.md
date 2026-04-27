# Runbook: Cache Poisoned

**Severity:** P2 (degraded results) — escalate to P1 if exports contain
attacker-controlled content.

## Symptoms

- Operators report stale or impossible findings (e.g. a `crtsh` row for a
  domain that does not exist on `crt.sh`).
- `GET /api/admin/cache/stats` shows hit ratio > 99% on a single key.
- Suspicious entries appear simultaneously across multiple unrelated cases.

## Triage

1. Confirm the bad value lives in cache, not the database:
   ```bash
   docker compose exec redis redis-cli GET "collector:<name>:<key>"
   docker compose exec backend psql -c "select count(*) from findings where payload->>'value' = '<bad>';"
   ```
2. Identify the namespace. Cache keys are `collector:<name>:<sha256(input)>`.
3. Check the audit log for the request that wrote it:
   ```bash
   curl -s /api/admin/audit?action=cache_write | jq 'select(.key | contains("<key>"))'
   ```

## Mitigation

1. **Purge the affected namespace** (do not flush all):
   ```bash
   curl -X POST /api/admin/cache/purge -d '{"namespace":"collector:<name>"}'
   ```
2. **Disable the collector** while you investigate the upstream source:
   ```bash
   curl -X POST /api/admin/collectors/<name>/disable
   ```
3. **Re-run impacted cases** — list them and replay:
   ```bash
   ./cli/osint case list --since 24h --collector <name> --replay
   ```

## Root cause analysis

- Upstream defacement / takeover? Validate the source's TLS chain and WHOIS.
- Cache key collision? Audit the key-derivation function for the collector.
- Missing response validation? Add a schema check before `cache.set()`.

## Prevention

- Sign cache values with HMAC if the upstream is mutable.
- Lower TTL for low-trust sources (default 24h, drop to 1h).
- Add a cross-collector sanity check in the orchestrator (e.g. domain in
  finding must match the input domain).

## Communication

- Notify affected operators via the in-app banner.
- If exports were generated, re-issue and append a "Re-issued: poisoned cache"
  note to each case.
