from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import GITHUB_API_URL, GITHUB_AUTH_URL, GITHUB_SCOPES
from app.schema.connections import GithubConnection
from app.security import decrypt_token, encrypt_token
from app.service.github_tools import GithubTools


def github_authorize_url(state: str) -> str:
    settings = get_settings()
    params = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_oauth_callback_uri,
            "scope": GITHUB_SCOPES,
            "state": state,
            "allow_signup": "false",
        }
    )
    return f"{GITHUB_AUTH_URL}/authorize?{params}"


async def exchange_github_code(code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GITHUB_AUTH_URL}/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_oauth_callback_uri,
            },
        )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error = data.get("error_description") or data.get("error") or "oauth_failed"
            raise ValueError(str(error))
        return data


async def _fetch_github_user(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_URL}/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if response.status_code >= 400:
            raise ValueError("Could not fetch GitHub user profile")
        return response.json()


def _expires_at_from_token_data(data: dict[str, Any]) -> datetime | None:
    expires_in = data.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def _sync_github_user_profile(db: Session, conn: GithubConnection) -> None:
    access_token = decrypt_token(conn.access_token_enc)
    tools = GithubTools(access_token=access_token)
    try:
        profile = tools.get_me()
    except (RuntimeError, ValueError):
        return
    if profile.get("id"):
        conn.github_user_id = str(profile["id"])
    if profile.get("login"):
        conn.github_login = str(profile["login"])
    conn.github_display_name = profile.get("name") or profile.get("login")
    conn.github_avatar_url = profile.get("avatar_url")
    db.commit()
    db.refresh(conn)


sync_github_user_profile = _sync_github_user_profile


def save_github_connection(
    db: Session,
    user_id: int,
    token_data: dict[str, Any],
    profile: dict[str, Any],
) -> GithubConnection:
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    scope = token_data.get("scope") or GITHUB_SCOPES

    github_user_id = str(profile.get("id") or "")
    github_login = str(profile.get("login") or "")
    if not github_user_id or not github_login:
        raise ValueError("GitHub profile missing id or login")

    existing = db.query(GithubConnection).filter(GithubConnection.user_id == user_id).first()
    if existing:
        conn = existing
    else:
        conn = GithubConnection(
            user_id=user_id,
            github_user_id=github_user_id,
            github_login=github_login,
            access_token_enc="",
        )
        db.add(conn)

    conn.github_user_id = github_user_id
    conn.github_login = github_login
    conn.github_display_name = profile.get("name") or github_login
    conn.github_avatar_url = profile.get("avatar_url")
    conn.access_token_enc = encrypt_token(access_token)
    conn.refresh_token_enc = encrypt_token(refresh_token) if refresh_token else None
    conn.expires_at = _expires_at_from_token_data(token_data)
    conn.granted_scopes = scope if isinstance(scope, str) else " ".join(scope)
    db.commit()
    db.refresh(conn)
    return conn


async def connect_github_from_code(db: Session, user_id: int, code: str) -> GithubConnection:
    token_data = await exchange_github_code(code)
    access_token = token_data["access_token"]
    profile = await _fetch_github_user(access_token)
    return save_github_connection(db, user_id, token_data, profile)


def get_github_connection(db: Session, user_id: int) -> GithubConnection | None:
    return db.query(GithubConnection).filter(GithubConnection.user_id == user_id).first()


def delete_github_connection(db: Session, user_id: int) -> bool:
    conn = get_github_connection(db, user_id)
    if not conn:
        return False
    db.delete(conn)
    db.commit()
    return True


def _refresh_github_token(db: Session, conn: GithubConnection) -> None:
    if not conn.refresh_token_enc:
        raise ValueError("GitHub token expired — please reconnect")

    settings = get_settings()
    refresh_token = decrypt_token(conn.refresh_token_enc)
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{GITHUB_AUTH_URL}/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error = data.get("error_description") or data.get("error") or "token_refresh_failed"
            raise ValueError(f"GitHub token expired — please reconnect ({error})")

    conn.access_token_enc = encrypt_token(data["access_token"])
    if data.get("refresh_token"):
        conn.refresh_token_enc = encrypt_token(data["refresh_token"])
    conn.expires_at = _expires_at_from_token_data(data)
    if data.get("scope"):
        conn.granted_scopes = data["scope"]
    db.commit()
    db.refresh(conn)


def _token_needs_refresh(conn: GithubConnection) -> bool:
    if not conn.expires_at or not conn.refresh_token_enc:
        return False
    expires_at = conn.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2)


def get_github_tools_for_user(db: Session, user_id: int) -> GithubTools:
    conn = get_github_connection(db, user_id)
    if not conn:
        raise ValueError("GitHub not connected")

    if _token_needs_refresh(conn):
        _refresh_github_token(db, conn)

    if not conn.github_display_name:
        _sync_github_user_profile(db, conn)

    access_token = decrypt_token(conn.access_token_enc)
    return GithubTools(access_token=access_token, login=conn.github_login)
