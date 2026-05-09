from __future__ import annotations

import asyncio
import base64
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


async def call_sonnet_vision(
    system: str,
    user_text: str,
    image_bytes: bytes | None = None,
    image_media_type: str = "image/png",
    *,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | None = None,
    use_cache: bool = True,
    timeout_s: float = 90.0,
) -> Any:
    """Vision-capable Sonnet call s dlhsim timeoutom a optional prompt cachingom."""
    content_blocks: list[dict[str, Any]] = []
    if image_bytes is not None:
        content_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                },
            }
        )
    content_blocks.append({"type": "text", "text": user_text})

    messages: list[dict[str, Any]] = [{"role": "user", "content": content_blocks}]

    if use_cache:
        system_param: Any = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    else:
        system_param = system

    cached_tools: list[dict[str, Any]] | None = None
    if use_cache and tools is not None and len(tools) >= 1:
        cached_tools = list(tools[:-1]) + [
            {**tools[-1], "cache_control": {"type": "ephemeral"}}
        ]
    elif tools is not None:
        cached_tools = list(tools)

    kwargs: dict[str, Any] = {
        "model": SONNET_MODEL,
        "system": system_param,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if cached_tools is not None:
        kwargs["tools"] = cached_tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    # Dedicated klient s dlhsim timeoutom - vision je pomalsi nez text-only
    vision_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=timeout_s)

    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await vision_client.messages.create(**kwargs)
        except RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "anthropic vision: rate limit attempt %s/%s: %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            await asyncio.sleep(delay)
            delay *= 2
        except APIError as exc:
            last_exc = exc
            logger.warning(
                "anthropic vision: API error attempt %s/%s: %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            await asyncio.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc
