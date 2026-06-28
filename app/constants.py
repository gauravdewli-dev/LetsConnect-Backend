JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
OAUTH_STATE_EXPIRE_MINUTES = 10
PASSWORD_RESET_OTP_EXPIRE_MINUTES = 10
PASSWORD_RESET_OTP_LENGTH = 6
OTP_MAX_VERIFY_ATTEMPTS = 5
OTP_LOCKOUT_MINUTES = 15
AUTH_RATE_LIMIT_REQUESTS = 30
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_REQUESTS = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300

GMAIL_OAUTH_CALLBACK_PATH = "/oauth/callback"
SLACK_OAUTH_CALLBACK_PATH = "/slack/oauth/callback"
JIRA_OAUTH_CALLBACK_PATH = "/jira/oauth/callback"

JIRA_SCOPES = (
    "read:jira-work "
    "write:jira-work "
    "read:jira-user "
    "offline_access"
)

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
