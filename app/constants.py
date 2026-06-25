JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24 * 7
OAUTH_STATE_EXPIRE_MINUTES = 10
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300

GMAIL_OAUTH_CALLBACK_PATH = "/oauth/callback"
SLACK_OAUTH_CALLBACK_PATH = "/slack/oauth/callback"

SLACK_BOT_SCOPES = (
    "chat:write,"
    "im:history,im:read,im:write,"
    "channels:history,"
    "app_mentions:read,"
    "users:read"
)
