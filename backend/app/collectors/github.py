"""GitHub collector: use the public REST API (5000 req/h with personal token).

Input:
 - username → profile, recent public events, gists
 - email    → try to match a commit author via the 'search/commits' endpoint
 - name     → user search
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


def _auth_headers() -> dict[str, str]:
    s = get_settings()
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if s.github_token:
        headers["Authorization"] = f"Bearer {s.github_token}"
    return headers


@register
class GitHubCollector(Collector):
    name = "github"
    category = "code"
    # Applicable if we have at least one signal that can match a user
    needs = ("username", "email", "full_name", "birth_name", "aliases")
    timeout_seconds = 45
    description = "GitHub public REST: profiles, repos, commit emails, gists."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        async with client(timeout=25, headers=_auth_headers()) as c:
            if input.username:
                async for f in self._username_track(c, input.username):
                    yield f
            if input.email:
                async for f in self._email_track(c, input.email):
                    yield f
            if not input.username:
                for nm in input.name_variants():
                    async for f in self._name_track(c, nm):
                        yield f

    async def _username_track(self, c: httpx.AsyncClient, username: str) -> AsyncIterator[Finding]:
        try:
            r = await c.get(f"https://api.github.com/users/{username}")
        except httpx.HTTPError:
            return
        if r.status_code != 200:
            return
        u = r.json()
        yield Finding(
            collector=self.name,
            category="username",
            entity_type="GitHubProfile",
            title=f"GitHub: {u.get('login')} ({u.get('name') or ''})",
            url=u.get("html_url"),
            confidence=0.95,
            payload={
                "login": u.get("login"),
                "name": u.get("name"),
                "email": u.get("email"),
                "bio": u.get("bio"),
                "company": u.get("company"),
                "blog": u.get("blog"),
                "location": u.get("location"),
                "twitter_username": u.get("twitter_username"),
                "followers": u.get("followers"),
                "public_repos": u.get("public_repos"),
                "created_at": u.get("created_at"),
            },
        )

        # Recent public events (may leak real email in commit events)
        commit_hours: list[int] = []
        try:
            r = await c.get(f"https://api.github.com/users/{username}/events/public", params={"per_page": 100})
            if r.status_code == 200:
                seen_emails = set()
                for ev in r.json():
                    if ev.get("type") == "PushEvent":
                        # Zone-hour inference from created_at
                        ts = ev.get("created_at") or ""
                        if ts[11:13].isdigit():
                            commit_hours.append(int(ts[11:13]))
                        for commit in ev.get("payload", {}).get("commits", []):
                            author = commit.get("author", {})
                            email = author.get("email")
                            if email and "noreply" not in email and email not in seen_emails:
                                seen_emails.add(email)
                                yield Finding(
                                    collector=self.name,
                                    category="email",
                                    entity_type="CommitEmail",
                                    title=f"Email en commit de {username}: {email}",
                                    url=f"https://github.com/{ev.get('repo', {}).get('name')}/commit/{commit.get('sha')}",
                                    confidence=0.85,
                                    payload={
                                        "email": email,
                                        "author_name": author.get("name"),
                                        "repo": ev.get("repo", {}).get("name"),
                                        "sha": commit.get("sha"),
                                    },
                                )
        except httpx.HTTPError:
            pass

        # Timezone heuristic from activity histogram
        if commit_hours:
            avg_h = sum(commit_hours) / len(commit_hours)
            # Rough UTC offset: GitHub API timestamps in UTC. Peak activity hour suggests tz.
            import collections
            hist = collections.Counter(commit_hours)
            top_hour = hist.most_common(1)[0][0]
            yield Finding(
                collector=self.name,
                category="behavior",
                entity_type="ActivityPattern",
                title=f"Actividad GitHub: pico a las {top_hour:02d}:00 UTC (n={len(commit_hours)})",
                url=None,
                confidence=0.55,
                payload={
                    "avg_hour_utc": round(avg_h, 1),
                    "top_hour_utc": top_hour,
                    "histogram_utc": dict(sorted(hist.items())),
                    "sample_size": len(commit_hours),
                },
            )

        # Top languages
        try:
            r = await c.get(f"https://api.github.com/users/{username}/repos", params={"per_page": 50, "sort": "updated"})
            if r.status_code == 200:
                lang_count = {}
                for repo in r.json():
                    lng = repo.get("language")
                    if lng: lang_count[lng] = lang_count.get(lng, 0) + 1
                if lang_count:
                    top = sorted(lang_count.items(), key=lambda x: -x[1])[:5]
                    yield Finding(
                        collector=self.name,
                        category="code",
                        entity_type="LanguagesDominant",
                        title=f"Lenguajes dominantes: {', '.join(f'{k}({v})' for k,v in top)}",
                        url=None,
                        confidence=0.7,
                        payload={"languages": dict(lang_count), "top": top},
                    )
        except httpx.HTTPError:
            pass

    async def _email_track(self, c: httpx.AsyncClient, email: str) -> AsyncIterator[Finding]:
        try:
            r = await c.get(
                "https://api.github.com/search/commits",
                params={"q": f"author-email:{email}", "per_page": 10},
                headers={"Accept": "application/vnd.github.cloak-preview+json"},
            )
        except httpx.HTTPError:
            return
        if r.status_code != 200:
            return
        items = r.json().get("items", [])
        for it in items:
            author = it.get("author") or {}
            commit = it.get("commit", {}).get("author", {})
            yield Finding(
                collector=self.name,
                category="email",
                entity_type="CommitByEmail",
                title=f"Commit de {commit.get('name')} ({email})",
                url=it.get("html_url"),
                confidence=0.9,
                payload={
                    "author_login": author.get("login"),
                    "author_name": commit.get("name"),
                    "repo": it.get("repository", {}).get("full_name"),
                    "message": it.get("commit", {}).get("message"),
                    "date": commit.get("date"),
                },
            )

    async def _name_track(self, c: httpx.AsyncClient, name: str) -> AsyncIterator[Finding]:
        try:
            r = await c.get("https://api.github.com/search/users", params={"q": name, "per_page": 5})
        except httpx.HTTPError:
            return
        if r.status_code != 200:
            return
        for u in r.json().get("items", []):
            yield Finding(
                collector=self.name,
                category="name",
                entity_type="GitHubProfileCandidate",
                title=f"Candidato GitHub: {u.get('login')}",
                url=u.get("html_url"),
                confidence=0.4,
                payload={"login": u.get("login"), "score": u.get("score")},
            )
