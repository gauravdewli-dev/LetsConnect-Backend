import logging

import httpx

from app.config import get_settings
from app.constants import SLACK_API
from app.schema.connections import SlackConnection
from app.service.gmail_tokens import get_slack_bot_token, get_slack_user_token

logger = logging.getLogger(__name__)


def uninstall_slack_from_workspace(conn: SlackConnection) -> None:
    """Remove LetsConnect from the Slack workspace and revoke user token (best-effort)."""
    settings = get_settings()
    if not settings.slack_client_id or not settings.slack_client_secret:
        logger.warning("Slack client credentials missing; skipping apps.uninstall")
        return

    bot_token = get_slack_bot_token(conn)
    user_token = get_slack_user_token(conn)

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{SLACK_API}/apps.uninstall",
                data={
                    "client_id": settings.slack_client_id,
                    "client_secret": settings.slack_client_secret,
                    "token": bot_token,
                },
            )
            data = response.json()
            if data.get("ok"):
                logger.info("Slack app uninstalled for team=%s", conn.slack_team_id)
            else:
                logger.warning("apps.uninstall failed: %s", data.get("error"))
    except Exception:
        logger.exception("Failed to uninstall Slack app for team=%s", conn.slack_team_id)

    if user_token:
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    f"{SLACK_API}/auth.revoke",
                    headers={"Authorization": f"Bearer {user_token}"},
                )
                data = response.json()
                if not data.get("ok"):
                    logger.warning("auth.revoke (user token) failed: %s", data.get("error"))
        except Exception:
            logger.exception("Failed to revoke Slack user token")
