import secrets

from google_auth_oauthlib.flow import Flow

from gmail_mcp.auth import get_credentials_path, get_token_path, has_valid_token, save_credentials
from gmail_mcp.constants import DEFAULT_WEB_REDIRECT_URI, SCOPES

_oauth_states: dict[str, bool] = {}


def get_web_redirect_uri() -> str:
    import os

    return os.getenv("GMAIL_WEB_REDIRECT_URI", DEFAULT_WEB_REDIRECT_URI)


def create_web_flow() -> Flow:
    return Flow.from_client_secrets_file(
        str(get_credentials_path()),
        scopes=SCOPES,
        redirect_uri=get_web_redirect_uri(),
    )


def start_web_auth() -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = True
    flow = create_web_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url, state


def complete_web_auth(authorization_response: str, state: str | None) -> None:
    if not state or state not in _oauth_states:
        raise ValueError("Invalid OAuth state")
    del _oauth_states[state]

    flow = create_web_flow()
    flow.fetch_token(
        authorization_response=authorization_response.replace("http://", "https://", 1)
    )
    save_credentials(flow.credentials)


def is_gmail_connected() -> bool:
    return has_valid_token()
