from app.service.calendar.constants import CALENDAR_SCOPES

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

GOOGLE_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES
