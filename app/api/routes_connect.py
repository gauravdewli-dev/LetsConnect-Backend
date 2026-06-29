from urllib.parse import quote

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.configs.database.db import get_db
from app.service.integration_connect import (
    build_gmail_connect_url,
    build_jira_connect_url,
    build_slack_connect_url,
)
from app.schema.users import User
from app.security import (
    decode_token,
    get_current_user,
    get_user_from_query_token,
)
from app.service.chat_service import clear_user_chat_history, get_messages_page, get_or_create_primary_conversation, handle_chat_message
from app.service.slack_disconnect import uninstall_slack_from_workspace
from app.service.slack_onboarding import onboard_slack_user
from app.service.gmail_tokens import (
    delete_gmail_connection,
    exchange_gmail_code,
    get_gmail_connection,
    get_slack_connection_by_user,
    save_gmail_connection,
    save_slack_connection,
    slack_has_user_token,
    sync_gmail_display_name,
    sync_slack_profile,
)
from app.service.jira_tokens import (
    connect_jira_from_code,
    delete_jira_connection,
    get_jira_connection,
    sync_jira_user_profile,
)
from app.types import (
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ConnectUrlResponse,
    ConnectionStatusResponse,
    MessageResponse,
    StoredChatMessage,
)

router = APIRouter(tags=["connect"])
logger = logging.getLogger(__name__)


def _build_connection_status(db: Session, user_id: int) -> ConnectionStatusResponse:
    settings = get_settings()
    gmail = get_gmail_connection(db, user_id)
    slack = get_slack_connection_by_user(db, user_id)
    jira = get_jira_connection(db, user_id)

    slack_open_url: str | None = None
    if slack and settings.slack_app_id:
        slack_open_url = (
            f"https://slack.com/app_redirect?app={settings.slack_app_id}"
            f"&team={slack.slack_team_id}"
        )

    return ConnectionStatusResponse(
        gmail_connected=gmail is not None,
        gmail_email=gmail.gmail_email if gmail else None,
        gmail_display_name=gmail.gmail_display_name if gmail else None,
        slack_connected=slack is not None,
        slack_configured=bool(settings.slack_client_id and settings.slack_signing_secret),
        slack_send_as_user=slack is not None and slack_has_user_token(slack),
        slack_team_id=slack.slack_team_id if slack else None,
        slack_team_name=slack.slack_team_name if slack else None,
        slack_display_name=slack.slack_display_name if slack else None,
        slack_open_url=slack_open_url,
        jira_connected=jira is not None,
        jira_site_url=jira.site_url if jira else None,
        jira_site_name=jira.site_name if jira else None,
        jira_display_name=jira.jira_display_name if jira else None,
        jira_configured=bool(settings.jira_client_id and settings.jira_client_secret),
        jira_oauth_callback_url=settings.jira_oauth_callback_uri if settings.jira_client_id else None,
    )


@router.get("/api/status", response_model=ConnectionStatusResponse)
def connection_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Fast DB-only read — no external API calls."""
    return _build_connection_status(db, user.id)


@router.post("/api/connections/backfill-profiles", response_model=ConnectionStatusResponse)
def backfill_connection_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sync missing display names from Gmail/Slack/Jira, then return fresh status."""
    gmail = get_gmail_connection(db, user.id)
    slack = get_slack_connection_by_user(db, user.id)
    jira = get_jira_connection(db, user.id)

    if gmail and not gmail.gmail_display_name:
        sync_gmail_display_name(db, gmail)
    if slack and not slack.slack_display_name:
        sync_slack_profile(db, slack)
    if jira and not jira.jira_display_name:
        sync_jira_user_profile(db, jira)

    return _build_connection_status(db, user.id)


@router.delete("/api/gmail", response_model=MessageResponse)
def disconnect_gmail(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not delete_gmail_connection(db, user.id):
        raise HTTPException(status_code=404, detail="Gmail not connected")
    return MessageResponse(message="Gmail disconnected")


@router.delete("/api/slack", response_model=ConnectionStatusResponse)
def disconnect_slack(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = get_slack_connection_by_user(db, user.id)
    if not conn:
        raise HTTPException(status_code=404, detail="Slack not connected")
    uninstall_slack_from_workspace(conn)
    db.delete(conn)
    db.commit()
    clear_user_chat_history(db, user.id)
    return _build_connection_status(db, user.id)


@router.delete("/api/jira", response_model=MessageResponse)
def disconnect_jira(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not delete_jira_connection(db, user.id):
        raise HTTPException(status_code=404, detail="Jira not connected")
    return MessageResponse(message="Jira disconnected")


@router.get("/api/chat/messages", response_model=ChatHistoryResponse)
def chat_messages(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
):
    conv = get_or_create_primary_conversation(db, user.id)
    before_dt = None
    if before:
        try:
            from datetime import datetime

            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid before cursor") from exc

    messages, next_cursor = get_messages_page(conv.id, user.id, limit=limit, before=before_dt)
    return ChatHistoryResponse(
        conversation_id=conv.id,
        messages=[StoredChatMessage(**m) for m in messages],
        next_cursor=next_cursor,
    )


@router.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = handle_chat_message(
            db,
            user.id,
            payload.message,
            channel="web",
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Conversation not found":
            raise HTTPException(status_code=404, detail=detail) from exc
        status = 429 if "rate limit" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:
        logger.exception("Chat agent failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to process your message") from exc
    return ChatResponse(
        reply=result["reply"],
        tools_used=result["tools_used"],
        conversation_id=result["conversation_id"],
    )


@router.get("/api/integrations/gmail/connect-url", response_model=ConnectUrlResponse)
def gmail_connect_url(user: User = Depends(get_current_user)):
    return ConnectUrlResponse(url=build_gmail_connect_url(user.id))


@router.get("/api/integrations/slack/connect-url", response_model=ConnectUrlResponse)
def slack_connect_url(user: User = Depends(get_current_user)):
    try:
        url = build_slack_connect_url(user.id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ConnectUrlResponse(url=url)


@router.get("/api/integrations/jira/connect-url", response_model=ConnectUrlResponse)
def jira_connect_url(user: User = Depends(get_current_user)):
    try:
        url = build_jira_connect_url(user.id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ConnectUrlResponse(url=url)


@router.get("/gmail/connect")
def gmail_connect(token: str = Query(...), db: Session = Depends(get_db)):
    user = get_user_from_query_token(token, db)
    return RedirectResponse(build_gmail_connect_url(user.id))


@router.get("/oauth/callback")
async def gmail_oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if error:
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=gmail&error={quote(error)}"
        )
    if not code or not state:
        return RedirectResponse(f"{settings.frontend_url}/success?provider=gmail&error=missing_params")

    try:
        payload = decode_token(state)
        if payload.get("type") != "oauth_state" or payload.get("provider") != "gmail":
            raise ValueError("Invalid state")
        user_id = int(payload["sub"])
        code_verifier = payload.get("cv")
        if not code_verifier or not isinstance(code_verifier, str):
            raise ValueError("Missing PKCE verifier — start Gmail connect again")
        creds = exchange_gmail_code(str(request.url), code_verifier=code_verifier)
        save_gmail_connection(db, user_id, creds)
        return RedirectResponse(f"{settings.frontend_url}/success?provider=gmail&connected=1")
    except Exception as exc:
        logger.exception("Gmail OAuth callback failed: %s", exc)
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=gmail&error={quote(str(exc))}"
        )


@router.get("/slack/install")
def slack_install(token: str = Query(...), db: Session = Depends(get_db)):
    user = get_user_from_query_token(token, db)
    try:
        url = build_slack_connect_url(user.id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url)


@router.get("/slack/oauth/callback")
async def slack_oauth_callback(
    background_tasks: BackgroundTasks,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if error:
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=slack&error={quote(error)}"
        )
    if not code or not state:
        return RedirectResponse(f"{settings.frontend_url}/success?provider=slack&error=missing_params")

    try:
        payload = decode_token(state)
        if payload.get("type") != "oauth_state" or payload.get("provider") != "slack":
            raise ValueError("Invalid state")
        user_id = int(payload["sub"])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": settings.slack_client_id,
                    "client_secret": settings.slack_client_secret,
                    "code": code,
                    "redirect_uri": settings.slack_oauth_callback_uri,
                },
                timeout=10.0,
            )
            data = response.json()
            if not data.get("ok"):
                raise ValueError(data.get("error", "oauth_failed"))

        bot_token = data["access_token"]
        team_id = data["team"]["id"]
        authed_user = data.get("authed_user", {})
        slack_user_id = authed_user.get("id", "")
        user_token = authed_user.get("access_token")
        if not slack_user_id:
            raise ValueError("No slack user id")
        if not user_token:
            raise ValueError(
                "Slack did not grant user permissions. Add User Token Scopes in your Slack app "
                "(chat:write, im:write) and reconnect."
            )

        save_slack_connection(
            db,
            user_id,
            team_id,
            slack_user_id,
            bot_token,
            user_token=user_token,
            team_name=data.get("team", {}).get("name"),
        )
        background_tasks.add_task(onboard_slack_user, bot_token, slack_user_id)
        return RedirectResponse(f"{settings.frontend_url}/success?provider=slack&connected=1")
    except Exception as exc:
        logger.exception("Slack OAuth callback failed: %s", exc)
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=slack&error={quote(str(exc))}"
        )


@router.get("/jira/connect")
def jira_connect(token: str = Query(...), db: Session = Depends(get_db)):
    user = get_user_from_query_token(token, db)
    settings = get_settings()
    if not settings.jira_client_id:
        raise HTTPException(status_code=503, detail="Jira not configured")
    logger.info("Jira OAuth redirect_uri=%s", settings.jira_oauth_callback_uri)
    try:
        url = build_jira_connect_url(user.id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url)


@router.get("/jira/oauth/callback")
async def jira_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if error:
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=jira&error={quote(error)}"
        )
    if not code or not state:
        return RedirectResponse(f"{settings.frontend_url}/success?provider=jira&error=missing_params")

    try:
        payload = decode_token(state)
        if payload.get("type") != "oauth_state" or payload.get("provider") != "jira":
            raise ValueError("Invalid state")
        user_id = int(payload["sub"])
        await connect_jira_from_code(db, user_id, code)
        return RedirectResponse(f"{settings.frontend_url}/success?provider=jira&connected=1")
    except Exception as exc:
        logger.exception("Jira OAuth callback failed: %s", exc)
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=jira&error={quote(str(exc))}"
        )
