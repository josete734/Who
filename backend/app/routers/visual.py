"""Visual aggregation endpoints: /photos and /profiles derived from findings."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth import check_auth
from app.db import Finding, session_scope

router = APIRouter(prefix="/api", tags=["visual"])


_URL_RX = re.compile(r'https?://[^\s"\'<>]+', re.I)
_IMAGE_EXT_RX = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:\?|$)", re.I)


def _iter_urls(val) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return _URL_RX.findall(val)
    if isinstance(val, list):
        return [u for item in val for u in _iter_urls(item)]
    if isinstance(val, dict):
        out = []
        for v in val.values():
            out.extend(_iter_urls(v))
        return out
    return []


def _extract_photos(finding: Finding) -> list[dict]:
    """Heuristically collect likely-person photo URLs from a finding's payload."""
    urls: set[str] = set()
    payload = finding.payload or {}

    # Explicit keys that commonly contain profile pictures
    for key in ("picture", "avatar_url", "photo", "profile_pic_url", "og_image", "picture_url", "image_url"):
        v = payload.get(key)
        for u in _iter_urls(v):
            urls.add(u)

    # Gravatar photos array
    for p in (payload.get("photos") or []):
        v = p.get("value") if isinstance(p, dict) else p
        for u in _iter_urls(v):
            urls.add(u)

    # PhotoURL entity from gemini_websearch etc.
    if finding.entity_type in ("PhotoURL", "ImageMatch") and finding.url:
        urls.add(finding.url)

    # Any URL in payload that looks like an image
    for u in _iter_urls(payload):
        if _IMAGE_EXT_RX.search(u):
            urls.add(u)

    out = []
    for u in urls:
        out.append({
            "url": u,
            "source_collector": finding.collector,
            "source_entity": finding.entity_type,
            "source_title": finding.title,
            "source_link": finding.url,
        })
    return out


@router.get("/cases/{case_id}/photos", dependencies=[Depends(check_auth)])
async def case_photos(case_id: uuid.UUID) -> dict:
    async with session_scope() as s:
        rows = (await s.execute(select(Finding).where(Finding.case_id == case_id))).scalars().all()
    seen: set[str] = set()
    photos: list[dict] = []
    for f in rows:
        for p in _extract_photos(f):
            if p["url"] in seen:
                continue
            seen.add(p["url"])
            photos.append(p)
    return {"count": len(photos), "photos": photos}


@router.get("/cases/{case_id}/profiles", dependencies=[Depends(check_auth)])
async def case_profiles(case_id: uuid.UUID) -> dict:
    """Return structured social/profile entries for the visual grid view."""
    profile_entity_types = {
        "SocialProfile", "GitHubProfile", "GitLabProfile", "BlueskyProfile",
        "MastodonProfile", "KeybaseProfile", "NpmProfile", "PyPIProfile",
        "DockerHubProfile", "StackOverflowUser", "RedditProfile",
        "TikTokProfile", "TelegramPresence", "WhatsAppProfile",
        "ORCIDProfile", "GravatarProfile", "SocialAccount",
    }
    async with session_scope() as s:
        rows = (await s.execute(
            select(Finding).where(Finding.case_id == case_id).order_by(Finding.confidence.desc())
        )).scalars().all()
    profiles = []
    for f in rows:
        if f.entity_type not in profile_entity_types:
            continue
        payload = f.payload or {}
        avatar = None
        for k in ("picture", "avatar_url", "photo", "profile_pic_url", "picture_url"):
            v = payload.get(k)
            if isinstance(v, str) and v.startswith("http"):
                avatar = v
                break
        if not avatar:
            for p in (payload.get("photos") or []):
                v = p.get("value") if isinstance(p, dict) else p
                if isinstance(v, str) and v.startswith("http"):
                    avatar = v
                    break
        profiles.append({
            "platform": (payload.get("platform") or payload.get("service") or f.entity_type.replace("Profile","").replace("Presence","")).strip() or f.collector,
            "handle": (payload.get("handle") or payload.get("username") or payload.get("login") or payload.get("uniqueId") or payload.get("name")),
            "display_name": payload.get("name") or payload.get("display_name") or payload.get("full_name") or payload.get("nickname"),
            "bio": payload.get("bio") or payload.get("about") or payload.get("description") or payload.get("signature"),
            "avatar": avatar,
            "url": f.url,
            "collector": f.collector,
            "confidence": f.confidence,
            "extras": {k: v for k, v in payload.items() if k in ("followers", "followerCount", "public_repos", "reputation", "followersCount", "followingCount", "verified", "location", "company", "country_code")},
        })
    return {"count": len(profiles), "profiles": profiles}
