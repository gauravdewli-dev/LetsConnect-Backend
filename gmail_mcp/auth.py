import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gmail_mcp.constants import DEFAULT_OAUTH_PORT, DEFAULT_REDIRECT_URI, SCOPES

load_dotenv()


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_credentials_path() -> Path:
    path = os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials.json")
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = _project_root() / resolved
    return resolved


def get_token_path() -> Path:
    path = os.getenv("GMAIL_TOKEN_PATH", "./token.json")
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = _project_root() / resolved
    return resolved


def get_redirect_uri() -> str:
    return os.getenv("GMAIL_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI)


def get_oauth_port() -> int:
    return int(os.getenv("GMAIL_OAUTH_PORT", str(DEFAULT_OAUTH_PORT)))


def has_valid_token() -> bool:
    token_path = get_token_path()
    if not token_path.exists():
        return False

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.valid:
        return True

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds, token_path)
            return True
        except Exception:
            return False

    return False


def _credentials_client_type(credentials_path: Path) -> str:
    config = json.loads(credentials_path.read_text())
    if "web" in config:
        return "web"
    if "installed" in config:
        return "installed"
    raise ValueError(
        f"Unrecognized credentials format in {credentials_path}. "
        "Expected a Web application or Desktop app OAuth client JSON."
    )


def _create_flow(credentials_path: Path) -> InstalledAppFlow:
    client_type = _credentials_client_type(credentials_path)
    if client_type == "web":
        config = json.loads(credentials_path.read_text())
        web = config["web"]
        client_config = {
            "installed": {
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "auth_uri": web["auth_uri"],
                "token_uri": web["token_uri"],
                "redirect_uris": [get_redirect_uri()],
            }
        }
        return InstalledAppFlow.from_client_config(client_config, SCOPES)
    return InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)


def _run_oauth_flow(credentials_path: Path) -> Credentials:
    client_type = _credentials_client_type(credentials_path)
    flow = _create_flow(credentials_path)
    if client_type == "web":
        redirect_uri = get_redirect_uri()
        parsed = urlparse(redirect_uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or get_oauth_port()
        print(
            f"OAuth redirect URI: {redirect_uri}\n"
            "Add this exact URI in Google Cloud Console → Credentials → "
            "your OAuth client → Authorised redirect URIs",
            file=sys.stderr,
        )
        return flow.run_local_server(
            port=port,
            host=host,
            redirect_uri_trailing_slash=redirect_uri.endswith("/"),
        )
    return flow.run_local_server(port=0)


def save_credentials(creds: Credentials, token_path: Path | None = None) -> None:
    path = token_path or get_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())


def load_credentials() -> Credentials:
    token_path = get_token_path()
    credentials_path = get_credentials_path()

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds, token_path)
        return creds

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Gmail credentials not found at {credentials_path}. "
            "Download OAuth Web application credentials from Google Cloud Console. "
            "See gmail_mcp/SETUP.md for instructions."
        )

    creds = _run_oauth_flow(credentials_path)
    save_credentials(creds, token_path)
    return creds


def run_auth_flow() -> None:
    """Run the OAuth flow and save token.json."""
    credentials_path = get_credentials_path()
    token_path = get_token_path()

    if not credentials_path.exists():
        print(
            f"Error: credentials file not found at {credentials_path}",
            file=sys.stderr,
        )
        print("See gmail_mcp/SETUP.md for Google Cloud setup.", file=sys.stderr)
        sys.exit(1)

    try:
        creds = _run_oauth_flow(credentials_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    save_credentials(creds, token_path)
    print(f"Authentication successful. Token saved to {token_path}", file=sys.stderr)


if __name__ == "__main__":
    run_auth_flow()
