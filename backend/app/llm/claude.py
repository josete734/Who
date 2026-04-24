"""Anthropic Claude client (messages API with prompt caching for the system prompt)."""
from __future__ import annotations

import anthropic

from app.config import get_settings
from app.llm.prompts import SYSTEM_PROMPT


async def claude_generate(user_prompt: str, max_tokens: int = 8000) -> tuple[str, str]:
    """Returns (markdown_text, model_used)."""
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    resp = await client.messages.create(
        model=s.anthropic_model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts), s.anthropic_model
