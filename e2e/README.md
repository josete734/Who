# OSINT Tool E2E (Playwright)

End-to-end tests covering case lifecycle, auth/rate-limit, and GDPR forget.

## Setup

```bash
cd e2e
npm install
npx playwright install
```

## Run

```bash
WHO_BASE_URL=http://localhost:8000 \
WHO_API_KEY=your-api-key \
npx playwright install && npx playwright test
```

## Environment

- `WHO_BASE_URL` (default `http://localhost:8000`)
- `WHO_API_KEY` (bearer token, required for non-auth tests)

## Projects

- `chromium`
- `webkit`

Run a single project: `npx playwright test --project=chromium`.
