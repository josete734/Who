# GDPR & Data Protection

The OSINT Tool processes personal data. This page describes the controls baked
into the platform and the operator obligations they imply.

## Lawful basis

Every case must declare a `purpose` from a fixed enum:

| Purpose | Lawful basis | Default retention |
|---|---|---|
| `kyc` | Legal obligation (AML/CFT) | 5 years |
| `due_diligence` | Legitimate interest | 1 year |
| `journalism` | Public interest / freedom of expression | 2 years |
| `internal_investigation` | Legitimate interest | 90 days |
| `research` | Legitimate interest | 30 days |

The chosen purpose is stored on the case, propagated into the audit log, and
used to gate which collectors run (e.g. `dehashed` is blocked outside `kyc`).

## Data minimisation

- `SearchInput` only accepts a closed set of identifiers.
- Collectors must not enrich beyond what the orchestrator scopes them for.
- Findings store URLs and small JSON payloads — never full page snapshots
  unless the operator explicitly opts in via `archive_advanced`.

## Retention & erasure

- Each case has a `retention_days` field; a nightly job hard-deletes expired
  cases and their findings.
- `DELETE /api/cases/{id}` triggers immediate erasure (right-to-be-forgotten)
  and logs a tombstone for audit.
- Combo-list FTS hits are stored as hashed fingerprints; raw lines never leave
  the local index.

## Data subject rights

| Right | Endpoint / mechanism |
|---|---|
| Access | `GET /api/cases?subject=<hash>` (operator-only) |
| Rectification | Findings are immutable; corrections via new case |
| Erasure | `DELETE /api/cases/{id}` |
| Restriction | `POST /api/cases/{id}/freeze` |
| Portability | `POST /api/cases/{id}/export` |

## Audit log

Every state-changing request is appended to `audit_log` with actor, IP,
purpose, case id, and a SHA-256 of the request body. The log is append-only
and exportable for DPO review.

## Cross-border transfers

By default the stack is self-hosted in the EU. Collectors that call
non-EU APIs (HIBP, Shodan, GitHub) are tagged `transfer:third-country` in the
registry; operators can disable them per-tenant from the admin panel.

## DPIA checklist

A template DPIA lives at `docs/plan/dpia-template.md` (untouched by this site).
Run it before enabling: `dehashed`, `holehe`, `combo_lists_local`, or any
`rapidapi_*` collector.
