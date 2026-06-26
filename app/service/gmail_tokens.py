import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.config import get_settings
from app.schema.connections import GmailConnection, SlackConnection
from app.security import decrypt_token, encrypt_token
from gmail_mcp.constants import SCOPES
from gmail_mcp.gmail_client import GmailClient


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
        scopes=SCOPES,
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


def save_gmail_connection(db: Session, user_id: int, creds: Credentials) -> GmailConnection:
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
    conn.refresh_token_enc = encrypt_token(creds.refresh_token or "")
    conn.access_token_enc = encrypt_token(creds.token) if creds.token else None
    conn.expires_at = creds.expiry
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
        scopes=SCOPES,
    )
    if conn.expires_at:
        creds.expiry = conn.expires_at
    return creds


def get_gmail_connection(db: Session, user_id: int) -> GmailConnection | None:
    return db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()


def get_gmail_client_for_user(db: Session, user_id: int) -> GmailClient:
    conn = get_gmail_connection(db, user_id)
    if not conn:
        raise ValueError("Gmail not connected")

    creds = _credentials_from_connection(conn)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            conn.access_token_enc = encrypt_token(creds.token) if creds.token else None
            conn.expires_at = creds.expiry
            db.commit()
        else:
            raise ValueError("Gmail token expired — please reconnect")

    return GmailClient(credentials=creds)


def save_slack_connection(
    db: Session,
    user_id: int,
    team_id: str,
    slack_user_id: str,
    bot_token: str,
    user_token: str | None = None,
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
    db.commit()
    db.refresh(conn)
    return conn


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
