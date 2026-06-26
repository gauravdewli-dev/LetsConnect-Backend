JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
OAUTH_STATE_EXPIRE_MINUTES = 10
PASSWORD_RESET_OTP_EXPIRE_MINUTES = 10
PASSWORD_RESET_OTP_LENGTH = 6
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300

GMAIL_OAUTH_CALLBACK_PATH = "/oauth/callback"
SLACK_OAUTH_CALLBACK_PATH = "/slack/oauth/callback"

SLACK_BOT_SCOPES = (
    "chat:write,"
    "im:history,im:read,im:write,"
    "channels:history,channels:read,"
    "groups:history,groups:read,"
    "app_mentions:read,"
    "users:read"
)

# User token — messages appear in your normal Slack DMs/channels as you.
SLACK_USER_SCOPES = (
    "chat:write,"
    "im:write,"
    "users:read,"
    "channels:history,channels:read,"
    "groups:history,groups:read"
)
