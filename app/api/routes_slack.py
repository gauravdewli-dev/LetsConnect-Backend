import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.config import get_settings
from app.configs.database.db import SessionLocal
from app.service.letsconnect_agent import run_agent
from app.service.gmail_tokens import (
    get_slack_bot_token,
    get_slack_bot_token_for_team,
    get_slack_connection_by_slack_user,
)
from app.service.slack_client import post_to_slack, strip_bot_mention, verify_slack_signature
from app.service.slack_onboarding import publish_app_home
from app.service.slack_session import append_exchange, get_history, session_key

router = APIRouter(prefix="/slack", tags=["slack"])
logger = logging.getLogger(__name__)

_EVENT_TTL_SECONDS = 300
_processed_events: dict[str, float] = {}


def _is_duplicate_event(event_id: str) -> bool:
    now = time.time()
    expired = [key for key, seen_at in _processed_events.items() if now - seen_at > _EVENT_TTL_SECONDS]
    for key in expired:
        del _processed_events[key]
    if event_id in _processed_events:
        return True
    _processed_events[event_id] = now
    return False


async def _process_message(
    slack_user_id: str,
    team_id: str,
    channel: str,
    text: str,
    *,
    thread_ts: str | None = None,
) -> None:
    settings = get_settings()
    db = SessionLocal()
    reply_thread = thread_ts

    try:
        conn = get_slack_connection_by_slack_user(db, slack_user_id)
        bot_token = get_slack_bot_token(conn) if conn else get_slack_bot_token_for_team(db, team_id)

        if not bot_token:
            logger.warning("No Slack bot token for user=%s team=%s", slack_user_id, team_id)
            return

        if not conn:
            await post_to_slack(
                bot_token,
                channel,
                (
                    f"Your Slack account isn't linked to LetsConnect yet. "
                    f"Sign in at {settings.frontend_url}, open *Connected accounts*, "
                    "and click *Add Slack*."
                ),
                thread_ts=reply_thread,
            )
            return

        history_key = session_key(slack_user_id, channel)
        history = get_history(history_key)

        try:
            result = run_agent(db, conn.user_id, text, history=history or None)
            reply = result["reply"]
        except ValueError as exc:
            reply = str(exc)
        except Exception:
            logger.exception("Slack agent failed for user=%s", slack_user_id)
            reply = "Sorry, something went wrong. Please try again."

        append_exchange(history_key, text, reply)
        await post_to_slack(bot_token, channel, reply, thread_ts=reply_thread)
    except Exception:
        logger.exception("Failed to process Slack message from user=%s", slack_user_id)
    finally:
        db.close()


def _queue_message(
    background_tasks: BackgroundTasks,
    *,
    event_id: str,
    slack_user_id: str,
    team_id: str,
    channel: str,
    text: str,
    thread_ts: str | None,
) -> None:
    if not slack_user_id or not channel or not text:
        return
    if event_id and _is_duplicate_event(event_id):
        return
    background_tasks.add_task(
        _process_message,
        slack_user_id,
        team_id,
        channel,
        text,
        thread_ts=thread_ts,
    )


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    if payload.get("type") != "event_callback":
        return {"ok": True}

    event = payload.get("event", {})
    team_id = payload.get("team_id", "")
    event_id = payload.get("event_id", "")

    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}

    event_type = event.get("type", "")
    channel = event.get("channel", "")
    slack_user_id = event.get("user", "")
    thread_ts = event.get("thread_ts") or event.get("ts")

    if event_type == "app_home_opened":
        db = SessionLocal()
        try:
            bot_token = get_slack_bot_token_for_team(db, team_id)
            if bot_token and slack_user_id:
                background_tasks.add_task(publish_app_home, bot_token, slack_user_id)
        finally:
            db.close()
        return {"ok": True}

    if event_type == "message":
        channel_type = event.get("channel_type", "")
        if channel_type and channel_type != "im":
            return {"ok": True}
        text = event.get("text", "").strip()
        _queue_message(
            background_tasks,
            event_id=event_id,
            slack_user_id=slack_user_id,
            team_id=team_id,
            channel=channel,
            text=text,
            thread_ts=thread_ts,
        )
    elif event_type == "app_mention":
        text = strip_bot_mention(event.get("text", ""))
        _queue_message(
            background_tasks,
            event_id=event_id,
            slack_user_id=slack_user_id,
            team_id=team_id,
            channel=channel,
            text=text,
            thread_ts=thread_ts,
        )

    return {"ok": True}


@router.post("/interactions")
async def slack_interactions(request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return {"ok": True}
