from __future__ import annotations

import asyncio
import logging
from typing import Any

from anthropic import APIError, AsyncAnthropic, RateLimitError

from app.config import settings

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=DEFAULT_TIMEOUT)
    return _client


async def _call_with_retry(**kwargs: Any) -> Any:
    delay = 1.0
    last_exc: Exception | None = None
    client = get_client()
    for attempt in range(MAX_RETRIES):
        try:
            return await client.messages.create(**kwargs)
        except RateLimitError as exc:
            last_exc = exc
            logger.warning("anthropic: rate limit attempt %s/%s: %s", attempt + 1, MAX_RETRIES, exc)
            await asyncio.sleep(delay)
            delay *= 2
        except APIError as exc:
            last_exc = exc
            logger.warning("anthropic: API error attempt %s/%s: %s", attempt + 1, MAX_RETRIES, exc)
            await asyncio.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc


async def call_haiku(
    system: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 200,
    temperature: float = 0.0,
) -> str:
    resp = await _call_with_retry(
        model=HAIKU_MODEL,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


async def call_sonnet(
    system: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 800,
    temperature: float = 0.3,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | None = None,
) -> Any:
    """Returns the raw Message response so callers can inspect tool_use blocks."""
    kwargs: dict[str, Any] = {
        "model": SONNET_MODEL,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return await _call_with_retry(**kwargs)
