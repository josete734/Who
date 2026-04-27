# Security Policy

## Reporting a vulnerability

If you discover a security issue in this project, please **do not open a
public GitHub issue**. Send a private report instead:

- Email: open a private security advisory through GitHub
  → <https://github.com/josete734/Who/security/advisories/new>

Please include:

- a clear description of the issue,
- steps to reproduce,
- the affected version / commit hash,
- the expected vs. observed behaviour,
- any proof-of-concept you can share.

We aim to acknowledge security reports within **72 hours** and to publish a
fix or mitigation within **30 days** for high-severity issues. Lower-severity
findings are addressed on a best-effort basis.

## Scope

In scope:

- the FastAPI backend (`backend/app/`)
- the Arq workers
- the Docker / Compose layout
- the database schema and migrations
- the OAuth flows shipped with the project (Strava, etc.)
- secret-handling and key-storage logic

Out of scope:

- third-party services queried by collectors (report to those vendors)
- user-supplied LLM provider issues (report to the LLM vendor)
- generic Python/library CVEs (track upstream)

## What we will not do

- We will not pay bug bounties.
- We do not run any public hosted instance of this software. Findings
  against `who.worldmapsound.com` (the maintainer's personal instance)
  belong to the operator of that instance, not to this repository.

## Hardening recommendations for operators

If you self-host `who`:

1.  **Generate strong secrets.** Replace every `change-me-*` placeholder in
    `.env` with `openssl rand -hex 32` output.
2.  **Set `ADMIN_TOKEN`.** Without it the admin endpoints fail closed (503).
3.  **Restrict CORS.** Edit `allowed_origins` in `app/config.py` to only the
    hosts you actually serve from.
4.  **Put the API behind a reverse proxy with TLS.** The shipped Caddyfile
    is a starting point; tighten it for production.
5.  **Encrypt the volume that holds Postgres data.** That database stores
    OAuth tokens (encrypted with `STRAVA_ENCRYPTION_KEY`), audit log,
    findings and entities — treat it as PII storage.
6.  **Rotate API keys regularly** and never hard-code them in collector
    files.
7.  **Limit external network egress** to only the hosts your collectors
    need; OSINT tools are a juicy SSRF target if compromised.
