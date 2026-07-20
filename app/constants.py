# Auth / JWT
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
OAUTH_STATE_EXPIRE_MINUTES = 10

# OTP
PASSWORD_RESET_OTP_EXPIRE_MINUTES = 10
PASSWORD_RESET_OTP_LENGTH = 6
OTP_MAX_VERIFY_ATTEMPTS = 5
OTP_LOCKOUT_MINUTES = 15
OTP_PURPOSE_PASSWORD_RESET = "password_reset"
OTP_PURPOSE_EMAIL_VERIFY = "email_verify"

# Rate limits
AUTH_RATE_LIMIT_REQUESTS = 30
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
LOGIN_RATE_LIMIT_REQUESTS = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900
SLACK_SIGNATURE_MAX_AGE_SECONDS = 300

# Chat / MongoDB
AGENT_HISTORY_LIMIT = 20
UI_MESSAGE_PAGE_LIMIT = 10
MESSAGES_COLLECTION = "messages"
# Cap Gemini↔tool loops per message. Write tools stay behind the approval gate;
# this limit mainly controls latency and runaway read loops. 8 is enough for
# typical multi-step asks without multi-minute chats.
MAX_TOOL_ROUNDS = 8
# Gemini SDK default is 5 attempts with long backoff — keep this low so 503/429
# fail fast instead of hanging the /api/chat request for a minute+.
GEMINI_HTTP_RETRY_ATTEMPTS = 2
GEMINI_HTTP_TIMEOUT_MS = 45_000

# App
APP_NAME = "LetsConnect"

# OAuth callback paths
GMAIL_OAUTH_CALLBACK_PATH = "/oauth/callback"
SLACK_OAUTH_CALLBACK_PATH = "/slack/oauth/callback"
JIRA_OAUTH_CALLBACK_PATH = "/jira/oauth/callback"
GITHUB_OAUTH_CALLBACK_PATH = "/github/oauth/callback"

# Google scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
# Full calendar scope covers events + calendar metadata (timezone).
# calendar.events alone can list/create events but cannot call calendars.get.
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]
GOOGLE_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES

# Jira / Atlassian
JIRA_SCOPES = (
    "read:jira-work "
    "write:jira-work "
    "read:jira-user "
    "offline_access"
)
ATLASSIAN_AUTH_URL = "https://auth.atlassian.com"
ATLASSIAN_API_URL = "https://api.atlassian.com"

# GitHub
# Space-separated for GitHub authorize URL (scope query param).
# repo = private + public repos user can access (owner/collaborator).
# read:org = org membership / private org repos visibility.
GITHUB_SCOPES = "repo read:user read:org workflow"
GITHUB_AUTH_URL = "https://github.com/login/oauth"
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# Slack
SLACK_API = "https://slack.com/api"
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

SLACK_WELCOME_DM = (
    f"Hi! I'm *{APP_NAME}*, your AI assistant.\n\n"
    "Chat with me here anytime — the same assistant as *Text chat* on the web dashboard. "
    "I can work with your connected Gmail and Slack.\n\n"
    "*Try asking:*\n"
    "• How many unread emails do I have?\n"
    "• Send a DM to Rohit saying hello\n"
    "• Read the latest messages from #general"
)

SLACK_HOME_BLOCKS = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{APP_NAME} AI Assistant"},
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"Welcome to *{APP_NAME}* — your AI assistant for Gmail, Slack, and more.\n\n"
                "Send me a direct message anytime. It's the same experience as "
                "*Text chat* on the LetsConnect web dashboard."
            ),
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Examples:*\n"
                "• How many unread emails do I have?\n"
                "• Send a Slack DM to a teammate\n"
                "• Read recent messages from a channel"
            ),
        },
    },
]

# Email providers
RESEND_API = "https://api.resend.com/emails"
BREVO_API = "https://api.brevo.com/v3/smtp/email"
EMAIL_INTRO_HTML = (
    "<p>Hi there!</p>"
    "<p>My name is <strong>Gaurav Dewli</strong>. LetsConnect is a personal project I'm building. "
    "This email is <em>not</em> from a registered company — it's sent from my personal account "
    "for account verification only.</p>"
)
EMAIL_INTRO_TEXT = (
    "Hi there!\n\n"
    "My name is Gaurav Dewli. LetsConnect is a personal project I'm building. "
    "This email is not from a registered company — it's sent from my personal account "
    "for account verification only.\n"
)
