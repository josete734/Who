# Backend collector test harness

This directory contains a VCR.py-backed test harness for the 44 OSINT
collectors. Cassettes live in `fixtures/cassettes/` and are checked in
so CI runs are fully offline.

## Install

```bash
pip install -r backend/requirements-dev.txt
```

## Replay (default, offline, CI)

```bash
pytest backend/tests/
```

`VCR_RECORD` is unset, so VCR runs in `record_mode='none'`: any HTTP
call that is not present in a cassette will fail. Tests whose cassette
does not yet exist are skipped (see `_cassette_required` in
`test_collectors_smoke.py`).

## Recording cassettes

The first time a test is added — or when an upstream API changes its
response shape — record real traffic:

```bash
# Record only missing cassettes; keep existing ones intact.
VCR_RECORD=once pytest backend/tests/test_collectors_smoke.py

# Add new HTTP episodes to an existing cassette without rewriting it.
VCR_RECORD=new_episodes pytest backend/tests/test_collectors_smoke.py

# Re-record everything from scratch.
VCR_RECORD=all pytest backend/tests/test_collectors_smoke.py
```

After recording, inspect the YAML in `fixtures/cassettes/` and commit
it. Authorization headers and common API key query parameters
(`api_key`, `apikey`, `key`, `token`, `access_token`, `auth`,
`authorization`, `client_secret`) are scrubbed automatically by
`collector_harness._build_vcr`.

## Updating a cassette when an API changes

1. Delete the stale cassette: `rm backend/tests/fixtures/cassettes/<name>.yaml`
2. Re-record: `VCR_RECORD=once pytest backend/tests/test_collectors_smoke.py::<test>`
3. Diff and commit the new YAML.

## Writing a new collector test

```python
from app.collectors.mycollector import MyCollector
from tests.collector_harness import run_collector_with_cassette

async def test_mycollector_smoke():
    findings = await run_collector_with_cassette(
        MyCollector,
        {"email": "test@example.com"},
        cassette_name="mycollector_basic",
    )
    assert findings  # or whatever invariant you care about
```

The harness builds a minimal `SearchInput` from your dict, runs the
collector's async generator to exhaustion, and returns a list of
`Finding` objects.
