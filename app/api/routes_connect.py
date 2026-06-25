from urllib.parse import quote, urlencode

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.configs.database.db import get_db
from app.constants import SLACK_BOT_SCOPES
from app.schema.users import User
from app.security import (
    create_oauth_state_token,
    decode_token,
    get_current_user,
    get_user_from_query_token,
)
from app.service.gmail_agent import run_agent
from app.service.gmail_tokens import (
    create_gmail_flow,
    delete_slack_connection,
    exchange_gmail_code,
    get_gmail_connection,
    get_slack_connection_by_user,
    save_gmail_connection,
    save_slack_connection,
)
from app.types import ChatRequest, ChatResponse, ConnectionStatusResponse, MessageResponse

router = APIRouter(tags=["connect"])
logger = logging.getLogger(__name__)


@router.get("/api/status", response_model=ConnectionStatusResponse)
def connection_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    gmail = get_gmail_connection(db, user.id)
    slack = get_slack_connection_by_user(db, user.id)
    return ConnectionStatusResponse(
        gmail_connected=gmail is not None,
        gmail_email=gmail.gmail_email if gmail else None,
        slack_connected=slack is not None,
        slack_configured=bool(settings.slack_client_id and settings.slack_signing_secret),
    )


@router.delete("/api/slack", response_model=MessageResponse)
def disconnect_slack(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not delete_slack_connection(db, user.id):
        raise HTTPException(status_code=404, detail="Slack not connected")
    return MessageResponse(message="Slack disconnected")


@router.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    history = [{"role": m.role, "content": m.content} for m in payload.history]
    try:
        result = run_agent(db, user.id, payload.message, history=history or None)
    except ValueError as exc:
        detail = str(exc)
        status = 429 if "rate limit" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:
        logger.exception("Chat agent failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to process your message") from exc
    return ChatResponse(reply=result["reply"], tools_used=result.get("tools_used", []))


@router.get("/gmail/connect")
def gmail_connect(token: str = Query(...), db: Session = Depends(get_db)):
    user = get_user_from_query_token(token, db)
    flow = create_gmail_flow()
    flow.code_verifier = secrets.token_urlsafe(64)
    state = create_oauth_state_token(user.id, "gmail", code_verifier=flow.code_verifier)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(auth_url)


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
    settings = get_settings()
    if not settings.slack_client_id:
        raise HTTPException(status_code=503, detail="Slack not configured")
    user = get_user_from_query_token(token, db)
    state = create_oauth_state_token(user.id, "slack")
    params = urlencode({
        "client_id": settings.slack_client_id,
        "scope": SLACK_BOT_SCOPES,
        "redirect_uri": settings.slack_oauth_callback_uri,
        "state": state,
    })
    return RedirectResponse(f"https://slack.com/oauth/v2/authorize?{params}")


@router.get("/slack/oauth/callback")
async def slack_oauth_callback(
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
        slack_user_id = data.get("authed_user", {}).get("id", "")
        if not slack_user_id:
            raise ValueError("No slack user id")

        save_slack_connection(db, user_id, team_id, slack_user_id, bot_token)
        return RedirectResponse(f"{settings.frontend_url}/success?provider=slack&connected=1")
    except Exception as exc:
        logger.exception("Slack OAuth callback failed: %s", exc)
        return RedirectResponse(
            f"{settings.frontend_url}/success?provider=slack&error={quote(str(exc))}"
        )
