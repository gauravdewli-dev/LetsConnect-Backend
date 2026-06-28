from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.schema.connections import JiraConnection
from app.security import decrypt_token, encrypt_token
from app.service.jira_tools import JiraTools

ATLASSIAN_AUTH_URL = "https://auth.atlassian.com"
ATLASSIAN_API_URL = "https://api.atlassian.com"


def jira_authorize_url(state: str) -> str:
    settings = get_settings()

    params = urlencode(
        {
            "audience": "api.atlassian.com",
            "client_id": settings.jira_client_id,
            "scope": "read:jira-work write:jira-work read:jira-user offline_access",
            "redirect_uri": settings.jira_oauth_callback_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
    )
    return f"{ATLASSIAN_AUTH_URL}/authorize?{params}"


async def exchange_jira_code(code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{ATLASSIAN_AUTH_URL}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "code": code,
                "redirect_uri": settings.jira_oauth_callback_uri,
            },
        )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error = data.get("error_description") or data.get("error") or "oauth_failed"
            raise ValueError(str(error))
        return data


async def _fetch_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{ATLASSIAN_API_URL}/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ValueError("Could not fetch Jira sites for this account")
        resources = response.json()
        if not isinstance(resources, list) or not resources:
            raise ValueError("No Jira Cloud sites found for this Atlassian account")
        return resources


def _expires_at_from_token_data(data: dict[str, Any]) -> datetime | None:
    expires_in = data.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def _sync_jira_user_profile(db: Session, conn: JiraConnection) -> None:
    access_token = decrypt_token(conn.access_token_enc)
    jira = JiraTools(access_token=access_token, cloud_id=conn.cloud_id, site_url=conn.site_url)
    try:
        profile = jira.get_me()
    except (RuntimeError, ValueError):
        return
    conn.jira_account_id = profile.get("account_id")
    conn.jira_display_name = profile.get("display_name")
    conn.jira_email = profile.get("email")
    db.commit()
    db.refresh(conn)


sync_jira_user_profile = _sync_jira_user_profile


def save_jira_connection(db: Session, user_id: int, token_data: dict[str, Any], site: dict[str, Any]) -> JiraConnection:
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("Jira did not return a refresh token — reconnect with consent")

    cloud_id = site["id"]
    site_url = site.get("url", "").rstrip("/")
    site_name = site.get("name", site_url)

    existing = db.query(JiraConnection).filter(JiraConnection.user_id == user_id).first()
    if existing:
        conn = existing
    else:
        conn = JiraConnection(
            user_id=user_id,
            cloud_id=cloud_id,
            site_url=site_url,
            site_name=site_name,
            access_token_enc="",
            refresh_token_enc="",
        )
        db.add(conn)

    conn.cloud_id = cloud_id
    conn.site_url = site_url
    conn.site_name = site_name
    conn.access_token_enc = encrypt_token(access_token)
    conn.refresh_token_enc = encrypt_token(refresh_token)
    conn.expires_at = _expires_at_from_token_data(token_data)
    db.commit()
    db.refresh(conn)
    _sync_jira_user_profile(db, conn)
    return conn


async def connect_jira_from_code(db: Session, user_id: int, code: str) -> JiraConnection:
    token_data = await exchange_jira_code(code)
    access_token = token_data["access_token"]
    resources = await _fetch_accessible_resources(access_token)
    jira_sites = [r for r in resources if "jira" in r.get("scopes", [])]
    site = jira_sites[0] if jira_sites else resources[0]
    return save_jira_connection(db, user_id, token_data, site)


def get_jira_connection(db: Session, user_id: int) -> JiraConnection | None:
    return db.query(JiraConnection).filter(JiraConnection.user_id == user_id).first()


def delete_jira_connection(db: Session, user_id: int) -> bool:
    conn = get_jira_connection(db, user_id)
    if not conn:
        return False
    db.delete(conn)
    db.commit()
    return True


def _refresh_jira_token(db: Session, conn: JiraConnection) -> None:
    settings = get_settings()
    refresh_token = decrypt_token(conn.refresh_token_enc)
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{ATLASSIAN_AUTH_URL}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "refresh_token": refresh_token,
            },
        )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error = data.get("error_description") or data.get("error") or "token_refresh_failed"
            raise ValueError(f"Jira token expired — please reconnect ({error})")

    conn.access_token_enc = encrypt_token(data["access_token"])
    if data.get("refresh_token"):
        conn.refresh_token_enc = encrypt_token(data["refresh_token"])
    conn.expires_at = _expires_at_from_token_data(data)
    db.commit()
    db.refresh(conn)


def _token_needs_refresh(conn: JiraConnection) -> bool:
    if not conn.expires_at:
        return False
    expires_at = conn.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2)


def get_jira_tools_for_user(db: Session, user_id: int) -> JiraTools:
    conn = get_jira_connection(db, user_id)
    if not conn:
        raise ValueError("Jira not connected")

    if _token_needs_refresh(conn):
        _refresh_jira_token(db, conn)

    if not conn.jira_account_id or not conn.jira_display_name:
        _sync_jira_user_profile(db, conn)

    access_token = decrypt_token(conn.access_token_enc)
    return JiraTools(access_token=access_token, cloud_id=conn.cloud_id, site_url=conn.site_url)
