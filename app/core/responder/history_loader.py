from __future__ import annotations

import logging
import uuid

from sqlalchemy import text as sa_text

from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

CHANNEL_TO_PROVIDER = {
    "messenger": "messenger",
    "instagram": "instagram",
    "gmail": "gmail",
    "whatsapp": "whatsapp",
    "sms": "sms",
    "web": "web",
}


async def load_conversation_history(
    company_id: uuid.UUID,
    customer_id: str | None,
    channel: str | None,
    limit: int = 10,
) -> list[dict]:
    """Load last N messages (24h window) for a customer/channel pair.

    Returns chronological list of {"role": "user"|"assistant", "content": str}.
    Returns [] on any failure (missing table, bad UUID, etc.) so the responder
    can continue without conversation memory.
    """
    if not customer_id or not channel:
        return []

    provider = CHANNEL_TO_PROVIDER.get(channel)
    if provider is None:
        return []

    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    sa_text(
                        """
                        SELECT role, content, created_at
                        FROM messages
                        WHERE company_id = :cid
                          AND customer_id = :customer_id
                          AND provider = :provider
                          AND created_at > now() - interval '24 hours'
                        ORDER BY created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {
                        "cid": str(company_id),
                        "customer_id": customer_id,
                        "provider": provider,
                        "lim": limit,
                    },
                )
            ).all()
    except Exception as exc:
        logger.warning(
            "load_conversation_history failed for company=%s customer=%s: %s",
            company_id,
            customer_id,
            exc,
        )
        return []

    history: list[dict] = []
    for r in reversed(rows):
        role = (r[0] or "").strip().lower()
        content = (r[1] or "").strip()
        if not content:
            continue
        if role not in ("user", "assistant"):
            continue
        history.append({"role": role, "content": content})

    return history
