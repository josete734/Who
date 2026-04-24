"""OpenAI ChatGPT client via Chat Completions API."""
from __future__ import annotations

import httpx

from app.dynamic_settings import get_runtime
from app.llm.prompts import SYSTEM_PROMPT


async def openai_generate(user_prompt: str, max_tokens: int = 8000) -> tuple[str, str]:
    rt = await get_runtime()
    key = rt.get("OPENAI_API_KEY") or ""
    model = rt.get("OPENAI_MODEL") or "gpt-4o-mini"
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
        )
        r.raise_for_status()
        data = r.json()
    txt = data["choices"][0]["message"]["content"]
    return txt, f"openai/{model}"
