import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode

import httpx
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import CALENDAR_SCOPES, GMAIL_SCOPES, GOOGLE_SCOPES
from app.schema.connections import GmailConnection, SlackConnection
from app.security import decrypt_token, encrypt_token
from app.service.calendar import CalendarClient
from app.service.calendar.constants import scope_grants_calendar_access
from app.service.gmail import GmailClient


def _scopes_from_connection(conn: GmailConnection) -> list[str]:
    if conn.granted_scopes:
        return [scope for scope in conn.granted_scopes.split(" ") if scope]
    return list(GMAIL_SCOPES)


def _fetch_token_scopes(access_token: str) -> list[str]:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": access_token},
            )
        if response.status_code >= 400:
            return []
        scope_str = response.json().get("scope", "")
        return [scope for scope in scope_str.split(" ") if scope]
    except httpx.HTTPError:
        return []


def _resolve_granted_scopes(creds: Credentials) -> list[str]:
    if creds.token:
        live_scopes = _fetch_token_scopes(creds.token)
        if live_scopes:
            return live_scopes
    if creds.scopes:
        return list(creds.scopes)
    return list(GMAIL_SCOPES)


def _sync_granted_scopes(db: Session, conn: GmailConnection, creds: Credentials) -> list[str]:
    scopes = _resolve_granted_scopes(creds)
    persisted = " ".join(scopes)
    if conn.granted_scopes != persisted:
        conn.granted_scopes = persisted
        db.commit()
    return scopes


def has_calendar_access(conn: GmailConnection, *, creds: Credentials | None = None) -> bool:
    scopes = _resolve_granted_scopes(creds) if creds else _scopes_from_connection(conn)
    return scope_grants_calendar_access(scopes)


def sync_google_scopes(db: Session, conn: GmailConnection) -> None:
    try:
        creds = _credentials_from_connection(conn)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds = _refresh_credentials(db, conn, creds)
        if creds.token:
            _sync_granted_scopes(db, conn, creds)
    except (ValueError, RefreshError):
        return


def get_google_credentials(db: Session, user_id: int) -> tuple[GmailConnection, Credentials]:
    conn = get_gmail_connection(db, user_id)
    if not conn:
        raise ValueError("Gmail not connected")

    creds = _credentials_from_connection(conn)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds = _refresh_credentials(db, conn, creds)
        else:
            raise ValueError("Gmail token expired — please reconnect")

    _sync_granted_scopes(db, conn, creds)
    return conn, creds


def _refresh_credentials(db: Session, conn: GmailConnection, creds: Credentials) -> Credentials:
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise ValueError(
            "Google token refresh failed — disconnect Gmail and reconnect to refresh permissions."
        ) from exc
    conn.access_token_enc = encrypt_token(creds.token) if creds.token else None
    conn.expires_at = creds.expiry
    db.commit()
    return creds


def _enable_local_oauth_http() -> None:
    """Allow OAuth over http://localhost during local development."""
    settings = get_settings()
    if settings.backend_url.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _credentials_path() -> Path:
    settings = get_settings()
    path = Path(settings.gmail_credentials_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


def _oauth_client_config() -> tuple[str, str]:
    config = json.loads(_credentials_path().read_text())
    web = config.get("web") or config.get("installed", {})
    return web["client_id"], web["client_secret"]


def create_gmail_flow() -> Flow:
    _enable_local_oauth_http()
    settings = get_settings()
    return Flow.from_client_secrets_file(
        str(_credentials_path()),
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.gmail_oauth_callback_uri,
    )


def _canonical_callback_url(authorization_response: str) -> str:
    """Build callback URL using the registered redirect_uri (not 127.0.0.1)."""
    settings = get_settings()
    query_params = parse_qs(urlparse(authorization_response).query)
    flat_params = {key: values[0] for key, values in query_params.items() if values}

    code = flat_params.get("code")
    if not code:
        raise ValueError("Missing authorization code in Gmail callback")

    params: dict[str, str] = {"code": code}
    if scope := flat_params.get("scope"):
        params["scope"] = scope

    return f"{settings.gmail_oauth_callback_uri}?{urlencode(params)}"


def _scopes_from_oauth_response(authorization_response: str) -> list[str]:
    query_params = parse_qs(urlparse(authorization_response).query)
    scope_values = query_params.get("scope", [])
    if not scope_values or not scope_values[0]:
        return []
    return [scope for scope in scope_values[0].split(" ") if scope]


def exchange_gmail_code(
    authorization_response: str,
    code_verifier: str | None = None,
) -> Credentials:
    _enable_local_oauth_http()
    flow = create_gmail_flow()
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=_canonical_callback_url(authorization_response))
    if not flow.credentials.refresh_token:
        raise ValueError(
            "Gmail did not return a refresh token. Revoke app access in Google Account "
            "settings and reconnect with consent."
        )
    return flow.credentials


def _fetch_google_display_name(access_token: str) -> str | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            return None
        return response.json().get("name") or None
    except (httpx.HTTPError, ValueError):
        return None


def save_gmail_connection(
    db: Session,
    user_id: int,
    creds: Credentials,
    *,
    oauth_scopes: list[str] | None = None,
) -> GmailConnection:
    gmail = GmailClient(credentials=creds)
    profile = gmail.get_profile()
    email = profile.get("email", "")

    existing = db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()
    if existing:
        conn = existing
    else:
        conn = GmailConnection(user_id=user_id, gmail_email=email, refresh_token_enc="")
        db.add(conn)

    conn.gmail_email = email
    if creds.token:
        display_name = _fetch_google_display_name(creds.token)
        if display_name:
            conn.gmail_display_name = display_name
    conn.refresh_token_enc = encrypt_token(creds.refresh_token or "")
    conn.access_token_enc = encrypt_token(creds.token) if creds.token else None
    conn.expires_at = creds.expiry
    if oauth_scopes:
        conn.granted_scopes = " ".join(oauth_scopes)
    else:
        conn.granted_scopes = " ".join(_resolve_granted_scopes(creds))
    db.commit()
    db.refresh(conn)
    return conn


def _credentials_from_connection(conn: GmailConnection) -> Credentials:
    client_id, client_secret = _oauth_client_config()
    creds = Credentials(
        token=decrypt_token(conn.access_token_enc) if conn.access_token_enc else None,
        refresh_token=decrypt_token(conn.refresh_token_enc),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_scopes_from_connection(conn),
    )
    if conn.expires_at:
        creds.expiry = conn.expires_at
    return creds


def get_gmail_connection(db: Session, user_id: int) -> GmailConnection | None:
    return db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()


def _revoke_google_token(token: str) -> None:
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError:
        return


def delete_gmail_connection(db: Session, user_id: int) -> bool:
    conn = get_gmail_connection(db, user_id)
    if not conn:
        return False
    try:
        _revoke_google_token(decrypt_token(conn.refresh_token_enc))
    except ValueError:
        pass
    db.delete(conn)
    db.commit()
    return True


def get_gmail_client_for_user(db: Session, user_id: int) -> GmailClient:
    _, creds = get_google_credentials(db, user_id)
    return GmailClient(credentials=creds)


def get_calendar_client_for_user(db: Session, user_id: int) -> CalendarClient:
    conn, creds = get_google_credentials(db, user_id)
    if not has_calendar_access(conn, creds=creds):
        raise ValueError(
            "Google Calendar not connected — disconnect Gmail, revoke LetsConnect at "
            "https://myaccount.google.com/permissions, then reconnect and allow Calendar "
            "on the Google consent screen."
        )

    return CalendarClient(credentials=creds)


def sync_gmail_display_name(db: Session, conn: GmailConnection) -> None:
    if conn.gmail_display_name:
        return
    try:
        creds = _credentials_from_connection(conn)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds = _refresh_credentials(db, conn, creds)
        if creds.token:
            display_name = _fetch_google_display_name(creds.token)
            if display_name:
                conn.gmail_display_name = display_name
                db.commit()
                db.refresh(conn)
    except (ValueError, httpx.HTTPError):
        return


def _fetch_slack_user_display_name(token: str, slack_user_id: str) -> str | None:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://slack.com/api/users.info",
                headers={"Authorization": f"Bearer {token}"},
                params={"user": slack_user_id},
            )
        data = response.json()
        if not data.get("ok"):
            return None
        user = data.get("user") or {}
        profile = user.get("profile") or {}
        return (
            profile.get("real_name")
            or profile.get("display_name")
            or user.get("real_name")
            or user.get("name")
        )
    except httpx.HTTPError:
        return None


def save_slack_connection(
    db: Session,
    user_id: int,
    team_id: str,
    slack_user_id: str,
    bot_token: str,
    user_token: str | None = None,
    *,
    team_name: str | None = None,
) -> SlackConnection:
    existing = db.query(SlackConnection).filter(SlackConnection.user_id == user_id).first()
    if existing:
        conn = existing
    else:
        conn = SlackConnection(
            user_id=user_id,
            slack_team_id=team_id,
            slack_user_id=slack_user_id,
            bot_token_enc="",
        )
        db.add(conn)

    conn.slack_team_id = team_id
    conn.slack_user_id = slack_user_id
    conn.bot_token_enc = encrypt_token(bot_token)
    if user_token:
        conn.user_token_enc = encrypt_token(user_token)
    if team_name:
        conn.slack_team_name = team_name
    token_for_profile = user_token or bot_token
    display_name = _fetch_slack_user_display_name(token_for_profile, slack_user_id)
    if display_name:
        conn.slack_display_name = display_name
    db.commit()
    db.refresh(conn)
    return conn


def sync_slack_profile(db: Session, conn: SlackConnection) -> None:
    if conn.slack_display_name and conn.slack_team_name:
        return
    token = get_slack_user_token(conn) or get_slack_bot_token(conn)
    if not conn.slack_display_name:
        display_name = _fetch_slack_user_display_name(token, conn.slack_user_id)
        if display_name:
            conn.slack_display_name = display_name
    db.commit()
    db.refresh(conn)


def get_slack_connection_by_user(db: Session, user_id: int) -> SlackConnection | None:
    return db.query(SlackConnection).filter(SlackConnection.user_id == user_id).first()


def get_slack_connection_by_slack_user(db: Session, slack_user_id: str) -> SlackConnection | None:
    return db.query(SlackConnection).filter(SlackConnection.slack_user_id == slack_user_id).first()


def get_slack_bot_token_for_team(db: Session, team_id: str) -> str | None:
    conn = (
        db.query(SlackConnection)
        .filter(SlackConnection.slack_team_id == team_id)
        .first()
    )
    if not conn:
        return None
    return get_slack_bot_token(conn)


def delete_slack_connection(db: Session, user_id: int) -> bool:
    conn = get_slack_connection_by_user(db, user_id)
    if not conn:
        return False
    db.delete(conn)
    db.commit()
    return True


def get_slack_bot_token(conn: SlackConnection) -> str:
    return decrypt_token(conn.bot_token_enc)


def get_slack_user_token(conn: SlackConnection) -> str | None:
    if not conn.user_token_enc:
        return None
    return decrypt_token(conn.user_token_enc)


def slack_has_user_token(conn: SlackConnection) -> bool:
    return bool(conn.user_token_enc)
