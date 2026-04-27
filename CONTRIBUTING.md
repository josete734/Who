# Contributing to `who`

Thanks for considering a contribution! Before opening a PR please read this
short guide.

## Ground rules

1.  **Use only public, lawful sources.** Pull requests that add scrapers
    against logged-in-only data, leaked credentials, paid breach data, or
    any source whose ToS prohibits automated access will be rejected.
2.  **No shipped credentials.** Never hard-code API keys, tokens, OAuth
    client secrets, cookies or any user-identifying data in PRs. Add
    placeholders to `.env.example` instead.
3.  **No targeted personal data in tests.** Use synthetic fixtures, not
    real people. `tests/fixtures/` must contain only made-up identifiers.
4.  **Respect rate limits and `robots.txt`.** Use `app.netfetch.get_client`
    so the shared rate limiter applies.

## Adding a new collector

The pattern is small and stable:

```python
# backend/app/collectors/my_source.py
from app.collectors.base import Collector, Finding, register
from app.netfetch import get_client
from app.schemas import SearchInput

@register
class MyCollector(Collector):
    name = "my_source"
    category = "lifestyle"          # or "registry", "domain", "email", ...
    needs = ("username",)           # any non-empty SearchInput field triggers run()
    timeout_seconds = 15

    async def run(self, input: SearchInput):
        async with await get_client("gentle") as c:
            r = await c.get(f"https://example.com/{input.username}")
            if r.status_code != 200:
                return
            yield Finding(
                collector=self.name,
                category=self.category,
                entity_type="account",
                title=f"example.com/{input.username}",
                url=str(r.url),
                confidence=0.8,
                payload={"platform": "example", "username": input.username},
            )
```

Then register it in `backend/app/collectors/__init__.py`:

```python
from app.collectors import (
    ...,
    my_source,
)
```

And add a respx-mocked test in `backend/tests/test_my_source.py`.

## Coding style

- Python 3.12, `from __future__ import annotations`, type hints everywhere.
- `ruff check` and `mypy` should be clean.
- Prefer small, single-purpose functions. Keep collectors stateless.
- Never raise out of `Collector.run()` — return early or yield nothing on
  any non-2xx, blocked, or rate-limited response.

## Commit messages

We follow a relaxed Conventional Commits style:

```
feat(collector): add example.com lookup
fix(orchestrator): swallow errors from optional pivot module
docs(readme): tighten attribution section
chore(deps): bump httpx to 0.28
```

## PR checklist

- [ ] No secrets committed (run `git diff --staged | grep -iE "key|token|password"`).
- [ ] New collectors register via `@register` and are imported in
      `collectors/__init__.py`.
- [ ] Tests added with `respx` mocks (no live network calls).
- [ ] Documentation updated (README collector list, `.env.example` if a new
      key is needed).
- [ ] `ruff` and `mypy` clean.

## Code of conduct

By participating you agree to abide by the
[Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
Be respectful. Harassment, doxxing of contributors, or threatening behaviour
results in immediate ban.
