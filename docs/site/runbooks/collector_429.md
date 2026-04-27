# Runbook: Collector Rate-Limited (HTTP 429)

**Severity:** P3 unless the collector is in the critical path of `kyc` cases,
then P2.

## Symptoms

- `/api/admin/collectors` shows the collector's `error_rate` rising.
- Logs include `CollectorFailure: 429 Too Many Requests`.
- The circuit breaker has opened (`state=open`) and findings stop appearing.

## Triage

1. Identify the collector and upstream:
   ```bash
   docker compose logs backend | grep -E '429|Too Many Requests' | tail -50
   ```
2. Read the `Retry-After` header from the most recent failure — it dictates
   the cool-down.
3. Check whether the limit is per-IP, per-API-key, or per-account by reading
   the upstream's docs.

## Immediate mitigation

1. **Let the breaker do its job.** The default threshold is 5 failures; once
   open it stays open for 60 s before half-opening. Do nothing if the issue
   is transient.
2. **Tune the collector** for sustained pressure:
   ```python
   class FooCollector(Collector):
       timeout_seconds = 30
       max_retries = 0          # do not retry 429
       circuit_breaker_threshold = 3
   ```
3. **Slow the orchestrator's fan-out** for this collector via env:
   ```bash
   COLLECTOR_FOO_CONCURRENCY=1
   ```
4. **Rotate / upgrade the API key** if the limit is per-account.

## Long-term fixes

- Add a token-bucket limiter in `resilience.py` keyed on the collector name.
- Cache positive results aggressively (raise TTL).
- Coalesce duplicate inflight requests (the resilience layer already supports
  `singleflight=True`).
- Move the collector behind a queue with a configurable RPS ceiling.

## Verification

```bash
# breaker should be closed:
curl -s /api/admin/collectors | jq '.[] | select(.name=="<name>") | .breaker_state'

# error rate should fall below 1%:
curl -s /api/metrics | grep collector_errors_total
```

## Escalation

If the upstream has banned the source IP:

1. Stop the collector immediately.
2. Contact the upstream support with your account id and request reinstatement.
3. Document the incident in `docs/plan/incidents/`.
