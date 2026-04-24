"""Ollama Cloud client (OpenAI-compatible endpoint with native fallback)."""
from __future__ import annotations

import httpx

from app.dynamic_settings import get_runtime
from app.llm.prompts import SYSTEM_PROMPT


async def ollama_generate(user_prompt: str, max_tokens: int = 8000) -> tuple[str, str]:
    """Try OpenAI-compatible /v1/chat/completions first, fall back to /api/chat native."""
    rt = await get_runtime()
    key = rt.get("OLLAMA_API_KEY") or ""
    model = rt.get("OLLAMA_MODEL") or "gpt-oss:120b"
    base = (rt.get("OLLAMA_BASE_URL") or "https://ollama.com").rstrip("/")
    if not key:
        raise RuntimeError("OLLAMA_API_KEY not configured")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300, http2=True) as c:
        # Attempt 1: OpenAI-compatible
        try:
            r = await c.post(
                f"{base}/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "stream": False,
                },
            )
            if r.status_code == 200:
                data = r.json()
                txt = data["choices"][0]["message"]["content"]
                return txt, f"ollama/{model}"
        except httpx.HTTPError:
            pass

        # Attempt 2: native /api/chat
        r = await c.post(
            f"{base}/api/chat",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.3},
            },
        )
        r.raise_for_status()
        data = r.json()
        txt = data.get("message", {}).get("content") or data.get("response") or ""
        if not txt:
            raise RuntimeError(f"Ollama returned empty response: {data}")
        return txt, f"ollama/{model}"
