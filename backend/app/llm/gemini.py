"""Google Gemini client via the `google-genai` SDK."""
from __future__ import annotations

from google import genai
from google.genai import types

from app.dynamic_settings import get_runtime
from app.llm.prompts import SYSTEM_PROMPT


async def gemini_generate(user_prompt: str, max_tokens: int = 8000) -> tuple[str, str]:
    rt = await get_runtime()
    key = rt.get("GEMINI_API_KEY") or ""
    model = rt.get("GEMINI_MODEL") or "gemini-2.5-pro"
    if not key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    client = genai.Client(api_key=key)
    resp = await client.aio.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=max_tokens,
            temperature=0.3,
        ),
    )
    text = resp.text or ""
    return text, model
