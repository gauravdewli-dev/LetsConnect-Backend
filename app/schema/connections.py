from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.configs.database.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class GmailConnection(Base):
    __tablename__ = "gmail_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    gmail_email = Column(String, nullable=False)
    gmail_display_name = Column(String, nullable=True)
    granted_scopes = Column(String, nullable=True)
    refresh_token_enc = Column(String, nullable=False)
    access_token_enc = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    user = relationship("User", backref="gmail_connection")


class SlackConnection(Base):
    __tablename__ = "slack_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    slack_team_id = Column(String, nullable=False)
    slack_user_id = Column(String, unique=True, index=True, nullable=False)
    slack_display_name = Column(String, nullable=True)
    slack_team_name = Column(String, nullable=True)
    bot_token_enc = Column(String, nullable=False)
    user_token_enc = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    user = relationship("User", backref="slack_connection")


class JiraConnection(Base):
    __tablename__ = "jira_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    cloud_id = Column(String, nullable=False)
    site_url = Column(String, nullable=False)
    site_name = Column(String, nullable=False, default="")
    jira_account_id = Column(String, nullable=True)
    jira_display_name = Column(String, nullable=True)
    jira_email = Column(String, nullable=True)
    access_token_enc = Column(String, nullable=False)
    refresh_token_enc = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    user = relationship("User", backref="jira_connection")


class GithubConnection(Base):
    __tablename__ = "github_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    github_user_id = Column(String, nullable=False)
    github_login = Column(String, nullable=False)
    github_display_name = Column(String, nullable=True)
    github_avatar_url = Column(String, nullable=True)
    access_token_enc = Column(String, nullable=False)
    refresh_token_enc = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    granted_scopes = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    user = relationship("User", backref="github_connection")
