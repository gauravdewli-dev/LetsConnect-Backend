import re

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

_PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,128}$")


def _validate_password_strength(value: str) -> str:
    if not _PASSWORD_RE.match(value):
        raise ValueError(
            "Password must be 8–128 characters and include uppercase, lowercase, and a number"
        )
    return value


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class UpdateUserRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    current_password: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_password_strength(value)


class DeleteUserRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class SignupResponse(BaseModel):
    requires_verification: bool = True
    message: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MessageResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: int
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConnectionStatusResponse(BaseModel):
    gmail_connected: bool
    gmail_email: str | None = None
    gmail_display_name: str | None = None
    slack_connected: bool
    slack_configured: bool = False
    slack_send_as_user: bool = False
    slack_team_id: str | None = None
    slack_team_name: str | None = None
    slack_display_name: str | None = None
    slack_open_url: str | None = None
    jira_connected: bool = False
    jira_site_url: str | None = None
    jira_site_name: str | None = None
    jira_display_name: str | None = None
    jira_configured: bool = False
    jira_oauth_callback_url: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=36)


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    conversation_id: str


class StoredChatMessage(BaseModel):
    id: str
    role: str
    content: str
    channel: str = "web"
    tools_used: list[str] = Field(default_factory=list)
    created_at: str


class ChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[StoredChatMessage]
    next_cursor: str | None = None


class ConnectUrlResponse(BaseModel):
    url: str
