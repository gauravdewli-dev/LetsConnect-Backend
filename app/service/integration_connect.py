import secrets
from urllib.parse import urlencode

from app.config import get_settings
from app.constants import SLACK_BOT_SCOPES, SLACK_USER_SCOPES
from app.security import create_oauth_state_token
from app.service.gmail_tokens import create_gmail_flow
from app.service.github_tokens import github_authorize_url
from app.service.jira_tokens import jira_authorize_url


def build_gmail_connect_url(user_id: int) -> str:
    flow = create_gmail_flow()
    flow.code_verifier = secrets.token_urlsafe(64)
    state = create_oauth_state_token(user_id, "gmail", code_verifier=flow.code_verifier)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return auth_url


def build_slack_connect_url(user_id: int) -> str:
    settings = get_settings()
    if not settings.slack_client_id:
        raise ValueError("Slack not configured")
    state = create_oauth_state_token(user_id, "slack")
    params = urlencode({
        "client_id": settings.slack_client_id,
        "scope": SLACK_BOT_SCOPES,
        "user_scope": SLACK_USER_SCOPES,
        "redirect_uri": settings.slack_oauth_callback_uri,
        "state": state,
    })
    return f"https://slack.com/oauth/v2/authorize?{params}"


def build_jira_connect_url(user_id: int) -> str:
    settings = get_settings()
    if not settings.jira_client_id:
        raise ValueError("Jira not configured")
    state = create_oauth_state_token(user_id, "jira")
    return jira_authorize_url(state)


def build_github_connect_url(user_id: int) -> str:
    settings = get_settings()
    if not settings.github_client_id:
        raise ValueError("GitHub not configured")
    state = create_oauth_state_token(user_id, "github")
    return github_authorize_url(state)
