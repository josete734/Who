"""Redis-backed event bus for streaming per-case events to SSE subscribers."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from app.config import get_settings

_redis: aioredis.Redis | None = None


async def redis_client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        s = get_settings()
        _redis = aioredis.from_url(s.redis_url, decode_responses=True)
    return _redis


def channel_for(case_id: uuid.UUID | str) -> str:
    return f"case_events:{case_id}"


async def publish(case_id: uuid.UUID | str, event: dict) -> None:
    r = await redis_client()
    await r.publish(channel_for(case_id), json.dumps(event, default=str))
    # Also keep a bounded history in a list so late subscribers can replay
    key = f"case_hist:{case_id}"
    async with r.pipeline(transaction=False) as p:
        p.rpush(key, json.dumps(event, default=str))
        p.expire(key, 3600 * 6)
        p.ltrim(key, -1000, -1)
        await p.execute()


async def replay(case_id: uuid.UUID | str) -> list[dict]:
    r = await redis_client()
    raw = await r.lrange(f"case_hist:{case_id}", 0, -1)
    out: list[dict] = []
    for x in raw:
        try:
            out.append(json.loads(x))
        except ValueError:
            continue
    return out


async def subscribe(case_id: uuid.UUID | str) -> AsyncIterator[dict]:
    """Async iterator of events for a case. Replays history first, then streams live."""
    r = await redis_client()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel_for(case_id))
    try:
        # Replay history first so a refresh mid-case doesn't lose anything
        for ev in await replay(case_id):
            yield ev

        last_heartbeat = 0.0
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
            if msg is None:
                # emit heartbeat roughly every 10s
                yield {"type": "heartbeat", "data": {}}
                continue
            data = msg.get("data")
            if not data:
                continue
            try:
                yield json.loads(data)
            except ValueError:
                continue
    finally:
        await pubsub.unsubscribe(channel_for(case_id))
        await pubsub.close()
