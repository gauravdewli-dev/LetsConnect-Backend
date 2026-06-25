import hashlib
import hmac
import logging
import re
import time

import httpx

from app.config import get_settings
from app.constants import SLACK_SIGNATURE_MAX_AGE_SECONDS

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*")


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    settings = get_settings()
    if not settings.slack_signing_secret:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > SLACK_SIGNATURE_MAX_AGE_SECONDS:
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def strip_bot_mention(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


async def post_to_slack(
    bot_token: str,
    channel: str,
    text: str,
    *,
    thread_ts: str | None = None,
) -> None:
    payload: dict[str, str] = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token}"},
            json=payload,
            timeout=15.0,
        )
        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "Slack API error")
            logger.error("chat.postMessage failed: %s", error)
            raise RuntimeError(error)
