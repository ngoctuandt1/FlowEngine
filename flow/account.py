"""Account tier and credit verification from passively captured network data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flow.client import FlowClient

logger = logging.getLogger(__name__)


async def check_account(client: FlowClient) -> dict:
    """Inspect passively captured network data for account tier and credits.

    Returns a dict with keys:
      - ``tier``      (str)  -- account tier label, e.g. ``"ultra"``, ``"free"``
      - ``credits``   (int)  -- remaining credits (``-1`` if unknown)
      - ``has_ultra`` (bool) -- True when the account has Ultra/paid tier

    This function does **not** make any network requests of its own.  It reads
    ``client._account_info`` (populated by the passive response hook) and falls
    back to scanning ``client._calls`` for ``/v1/credits`` responses.
    """
    info = getattr(client, "_account_info", None) or {}

    tier = str(info.get("tier", "")).strip().lower()
    credits_raw = info.get("credits", info.get("remainingCredits", -1))

    try:
        credits = int(credits_raw)
    except (TypeError, ValueError):
        credits = -1

    # Fallback: scan captured API calls for credit-related responses.
    if not tier:
        for call in reversed(getattr(client, "_calls", [])):
            url = str(call.get("url", "")).lower()
            if "/v1/credits" not in url and "credits" not in url:
                continue
            body = call.get("body", "")
            if isinstance(body, dict):
                tier = str(body.get("tier", "")).strip().lower()
                if credits < 0:
                    credits = int(body.get("remainingCredits", body.get("credits", -1)))
                break
            if isinstance(body, str):
                import json
                try:
                    data = json.loads(body)
                    tier = str(data.get("tier", "")).strip().lower()
                    if credits < 0:
                        credits = int(data.get("remainingCredits", data.get("credits", -1)))
                except Exception:
                    pass
                break

    has_ultra = tier in ("ultra", "ai_ultra", "premium", "paid")

    result = {"tier": tier or "unknown", "credits": credits, "has_ultra": has_ultra}
    logger.debug("check_account -> %s", result)
    return result


async def verify_credits(client: FlowClient, required: int = 0) -> bool:
    """Verify that the account has at least *required* credits.

    Lower-priority (LP) models consume 0 credits, so ``required=0`` always
    passes when an account is detected at all.

    Returns ``True`` when credit balance is sufficient **or** unknown (we
    optimistically allow the operation and let the server reject if needed).
    """
    account = await check_account(client)
    credits = account["credits"]

    if credits < 0:
        # Unknown balance -- let the operation proceed.
        logger.info("Credit balance unknown; allowing operation optimistically")
        return True

    if credits >= required:
        return True

    logger.warning(
        "Insufficient credits: have %d, need %d (tier=%s)",
        credits,
        required,
        account["tier"],
    )
    return False
