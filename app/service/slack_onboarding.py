import logging

import httpx

from app.constants import SLACK_API, SLACK_HOME_BLOCKS, SLACK_WELCOME_DM

logger = logging.getLogger(__name__)


def _post(bot_token: str, method: str, payload: dict) -> dict:
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{SLACK_API}/{method}",
            headers={"Authorization": f"Bearer {bot_token}"},
            json=payload,
        )
        return response.json()


def open_dm_channel(bot_token: str, slack_user_id: str) -> str | None:
    data = _post(bot_token, "conversations.open", {"users": slack_user_id})
    if not data.get("ok"):
        logger.warning("conversations.open failed: %s", data.get("error"))
        return None
    return data.get("channel", {}).get("id")


def send_welcome_dm(bot_token: str, slack_user_id: str) -> None:
    channel_id = open_dm_channel(bot_token, slack_user_id)
    if not channel_id:
        return
    data = _post(
        bot_token,
        "chat.postMessage",
        {"channel": channel_id, "text": SLACK_WELCOME_DM},
    )
    if not data.get("ok"):
        logger.warning("welcome DM failed: %s", data.get("error"))


def publish_app_home(bot_token: str, slack_user_id: str) -> None:
    data = _post(
        bot_token,
        "views.publish",
        {
            "user_id": slack_user_id,
            "view": {"type": "home", "blocks": SLACK_HOME_BLOCKS},
        },
    )
    if not data.get("ok"):
        logger.warning("views.publish failed: %s", data.get("error"))


def onboard_slack_user(bot_token: str, slack_user_id: str) -> None:
    """Open a DM with the LetsConnect app and send a welcome message."""
    try:
        send_welcome_dm(bot_token, slack_user_id)
    except Exception:
        logger.exception("Slack onboarding failed for user=%s", slack_user_id)
