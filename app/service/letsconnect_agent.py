import json
from datetime import datetime, timezone
from typing import Any

from google.genai import types
from sqlalchemy.orm import Session

from app.config import get_settings
from app.service.gemini_keys import GeminiKeyPool, generate_content as gemini_generate_content
from app.service.gmail_tokens import (
    get_calendar_client_for_user,
    get_gmail_client_for_user,
    get_gmail_connection,
    get_google_credentials,
    get_slack_bot_token,
    get_slack_connection_by_user,
    get_slack_user_token,
    has_calendar_access,
)
from app.service.jira_tokens import get_jira_connection, get_jira_tools_for_user
from app.service.jira_tools import JiraTools
from app.service.github_tokens import get_github_connection, get_github_tools_for_user
from app.service.github_tools import GithubTools
from app.service.slack_tools import SlackTools
from app.service.calendar import CalendarClient
from app.service.gmail import GmailClient

GMAIL_TOOL_DEFINITIONS = [
    {
        "name": "get_profile",
        "description": "Get Gmail account email and message/thread counts.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_labels",
        "description": "List all Gmail labels with IDs.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_unread",
        "description": "List unread emails in the inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max threads (1-50, default 10)"},
            },
        },
    },
    {
        "name": "search_messages",
        "description": "Search email threads using Gmail query syntax (from:, subject:, is:unread, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Max threads (1-50, default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_thread",
        "description": "Get a full email thread with all messages and bodies.",
        "parameters": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_message",
        "description": "Get a single email message by ID with full body.",
        "parameters": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "create_draft",
        "description": (
            "Save an email as a draft in the user's Gmail Drafts folder WITHOUT sending it. "
            "Use this when the user wants to draft/compose an email to review or send later in Gmail."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_email",
        "description": "Send a new email to a recipient.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]

CALENDAR_TOOL_DEFINITIONS = [
    {
        "name": "calendar_list_events",
        "description": (
            "List Google Calendar events. Prefer on_date (YYYY-MM-DD) for 'today', 'tomorrow', "
            "or a specific day. Resolve relative dates yourself from the system clock — never ask "
            "the user for a calendar date when they already said today/tomorrow/this week."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "description": "ISO 8601 start of range (default: now)",
                },
                "time_max": {
                    "type": "string",
                    "description": "ISO 8601 end of range (optional)",
                },
                "on_date": {
                    "type": "string",
                    "description": (
                        "Calendar day YYYY-MM-DD. Use for 'meetings today/tomorrow' or any single day."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA timezone for on_date day bounds (optional). "
                        "Omit to use the user's primary calendar timezone."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search (title, attendees, etc.)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max events (1-50, default 10)",
                },
            },
        },
    },
    {
        "name": "calendar_get_event",
        "description": "Get full details for a calendar event by ID.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Create a Google Calendar event and optionally invite attendees. "
            "Sends calendar invites when attendees are included. "
            "Set add_google_meet=true to add a Google Meet video link."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time ISO 8601 or YYYY-MM-DD for all-day"},
                "end": {"type": "string", "description": "End time ISO 8601 or YYYY-MM-DD for all-day"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendee email addresses",
                },
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone, e.g. America/New_York (default: user's calendar timezone)",
                },
                "all_day": {"type": "boolean", "description": "All-day event (default false)"},
                "add_google_meet": {
                    "type": "boolean",
                    "description": "Add a Google Meet link (default false)",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Update an existing calendar event. Sends updates to attendees when changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replace attendee list with these emails",
                },
                "timezone": {"type": "string"},
                "all_day": {"type": "boolean"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": (
            "Delete a calendar event permanently. ALWAYS confirm with the user before calling."
        ),
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
]

SLACK_TOOL_DEFINITIONS = [
    {
        "name": "slack_list_users",
        "description": "List Slack workspace users (id, name, real_name) to find who to message.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max users (1-200, default 50)"},
            },
        },
    },
    {
        "name": "slack_list_channels",
        "description": "List Slack channels the bot can access (public and private).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max channels (1-100, default 50)"},
            },
        },
    },
    {
        "name": "slack_get_workspace",
        "description": "Get the connected Slack workspace name and team ID.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "slack_read_channel",
        "description": (
            "Read recent messages from a Slack channel or DM by name (e.g. 'general', 'eng') or ID. "
            "Use immediately when the user asks for status, updates, or what's new in a channel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name (with or without #) or channel ID"},
                "limit": {"type": "integer", "description": "Number of messages (1-50, default 15)"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "slack_send_dm",
        "description": "Send a direct message to a Slack user. MUST be called before claiming a DM was sent.",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Slack username, display name, or user ID"},
                "text": {"type": "string", "description": "Message text to send"},
            },
            "required": ["user", "text"],
        },
    },
    {
        "name": "slack_send_channel_message",
        "description": "Post a message to a Slack channel by name (e.g. 'general') or channel ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name or ID"},
                "text": {"type": "string", "description": "Message text to post"},
            },
            "required": ["channel", "text"],
        },
    },
]

JIRA_TOOL_DEFINITIONS = [
    {
        "name": "jira_get_site",
        "description": "Get the connected Jira Cloud site URL and cloud ID.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "jira_list_projects",
        "description": "List Jira projects the user can access (key, name).",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max projects (1-100, default 50)"},
            },
        },
    },
    {
        "name": "jira_get_me",
        "description": (
            "Get the connected Jira user's identity (display name, account id). "
            "Use when you need to confirm who 'my tickets' refers to."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "jira_list_my_issues",
        "description": (
            "List Jira issues assigned to the connected user (assignee = currentUser()). "
            "PREFER this when the user asks about their tickets, my issues, my tasks, "
            "what is assigned to me, or open work on their board — unless they explicitly "
            "ask for all team tickets or another person's tickets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter, e.g. 'In Progress', 'Open', 'Done'",
                },
                "project_key": {
                    "type": "string",
                    "description": "Optional project key filter, e.g. PROJ",
                },
                "additional_jql": {
                    "type": "string",
                    "description": "Optional extra JQL AND clause, e.g. priority = High",
                },
                "max_results": {"type": "integer", "description": "Max issues (1-50, default 20)"},
            },
        },
    },
    {
        "name": "jira_search_issues",
        "description": (
            "Search all Jira issues with JQL (team-wide or custom filters). "
            "Use when the user asks for all tickets, project board, unassigned issues, "
            "or tickets assigned to someone else — NOT for 'my tickets' (use jira_list_my_issues)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jql": {"type": "string", "description": "JQL query"},
                "max_results": {"type": "integer", "description": "Max issues (1-50, default 20)"},
            },
            "required": ["jql"],
        },
    },
    {
        "name": "jira_get_issue",
        "description": "Get full details for a Jira issue by key (e.g. PROJ-123).",
        "parameters": {
            "type": "object",
            "properties": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
        },
    },
    {
        "name": "jira_create_issue",
        "description": (
            "Create a new Jira issue. Show the draft (project, type, summary, description, "
            "priority, due date, estimate) and confirm before calling unless the user asked to "
            "create immediately. Resolve relative due dates (Friday, in 2 days) from the system clock."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_key": {"type": "string", "description": "Project key, e.g. PROJ"},
                "summary": {"type": "string"},
                "issue_type": {"type": "string", "description": "Task, Bug, Story, etc."},
                "description": {"type": "string"},
                "priority": {"type": "string", "description": "Optional priority name"},
                "due_date": {
                    "type": "string",
                    "description": "Due date as YYYY-MM-DD (resolve relative phrases yourself)",
                },
                "original_estimate": {
                    "type": "string",
                    "description": "Time estimate in Jira format, e.g. 2h, 1d, 30m",
                },
            },
            "required": ["project_key", "summary", "issue_type"],
        },
    },
    {
        "name": "jira_update_issue",
        "description": (
            "Update an existing Jira issue (summary, description, priority, due date, or estimate). "
            "Confirm changes with the user before calling unless they asked to update immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "issue_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "due_date": {
                    "type": "string",
                    "description": "Due date as YYYY-MM-DD",
                },
                "original_estimate": {
                    "type": "string",
                    "description": "Time estimate in Jira format, e.g. 2h, 1d, 30m",
                },
            },
            "required": ["issue_key"],
        },
    },
    {
        "name": "jira_delete_issue",
        "description": (
            "Permanently delete a Jira issue. ALWAYS confirm with the user before calling — "
            "deletion cannot be undone."
        ),
        "parameters": {
            "type": "object",
            "properties": {"issue_key": {"type": "string"}},
            "required": ["issue_key"],
        },
    },
]

GITHUB_TOOL_DEFINITIONS = [
    {
        "name": "github_get_me",
        "description": "Get the connected GitHub user (login, name, profile URL).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "github_list_repos",
        "description": (
            "List ALL repositories the connected user can access — owned, collaborator, AND "
            "organization member repos, including PRIVATE ones where they are not the owner. "
            "Use this (with no query) when they say 'all my repos', 'repos assigned to me', "
            "'repos I have access to'. Each result includes access role (owner/collaborator/etc). "
            "Do NOT use a name query unless they asked to search by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional name search only — omit to list all accessible repos",
                },
                "affiliation": {
                    "type": "string",
                    "description": (
                        "Optional filter: owner, collaborator, organization_member "
                        "(comma-separated). Default = all three."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max repos (1-200, default 100)",
                },
            },
        },
    },
    {
        "name": "github_list_branches",
        "description": (
            "List branch names in a repository. Use when creating or filtering PRs and the user "
            "gave a partial branch name, or when head/base are unknown. Do NOT assume main/dev — "
            "use the exact branch names the user provides (or list first)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "query": {
                    "type": "string",
                    "description": "Optional substring to filter branch names (e.g. feature, release)",
                },
                "max_results": {"type": "integer", "description": "Max branches (1-100, default 30)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "github_search_my_pull_requests",
        "description": (
            "Search PRs involving the CONNECTED GitHub user across all accessible repos. "
            "PREFER this when they ask about 'my PRs', 'PRs I raised/opened/created', "
            "'PRs waiting for my review', 'PRs assigned to me', or 'PRs for me' without naming a repo. "
            "role=authored → PRs they opened; role=review_requested → PRs asking them to review; "
            "role=assigned → PRs assigned to them; role=involves → broader involvement."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "authored | review_requested | assigned | involves (default authored)",
                },
                "state": {
                    "type": "string",
                    "description": "open, closed, merged, or all (default open)",
                },
                "repo": {
                    "type": "string",
                    "description": "Optional owner/repo filter, e.g. acme/api",
                },
                "query": {
                    "type": "string",
                    "description": "Optional extra GitHub search terms (title keywords, label:bug, etc.)",
                },
                "max_results": {"type": "integer", "description": "Max PRs (1-50, default 20)"},
            },
        },
    },
    {
        "name": "github_list_pull_requests",
        "description": (
            "List pull requests for ONE repository (owner/repo). Use when the repo is known. "
            "Optional head/base filters for branch-to-branch PRs (any branch names — never hardcode)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {
                    "type": "string",
                    "description": "open, closed, or all (default open)",
                },
                "head": {
                    "type": "string",
                    "description": "Source branch name filter (exact branch the user named)",
                },
                "base": {
                    "type": "string",
                    "description": "Target branch name filter (exact branch the user named)",
                },
                "max_results": {"type": "integer", "description": "Max PRs (1-50, default 20)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "github_get_pull_request",
        "description": (
            "Get full details for one PR: title, description/body, author, head→base branches, "
            "labels, reviewers, mergeable state, html_url link, and recent review comments. "
            "ALWAYS use this when the user asks to review, open, or summarize a specific PR."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
    {
        "name": "github_create_pull_request",
        "description": (
            "Create a pull request between ANY two branches the user names (head → base). "
            "Never default to main/master/dev unless the user said so or the repo default_branch "
            "is confirmed. Show draft (repo, title, head, base, body) and confirm before calling "
            "unless they asked to create immediately. If a branch name is ambiguous, call "
            "github_list_branches first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "head": {
                    "type": "string",
                    "description": "Source branch exactly as named by the user (or user:branch for forks)",
                },
                "base": {
                    "type": "string",
                    "description": "Target branch exactly as named by the user",
                },
                "body": {"type": "string", "description": "PR description/markdown body"},
                "draft": {"type": "boolean"},
            },
            "required": ["owner", "repo", "title", "head", "base"],
        },
    },
    {
        "name": "github_merge_pull_request",
        "description": (
            "Merge a pull request. ALWAYS confirm with the user before calling — "
            "merging cannot be undone easily."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "pull_number": {"type": "integer"},
                "merge_method": {
                    "type": "string",
                    "description": "merge, squash, or rebase (default merge)",
                },
                "commit_title": {"type": "string"},
                "commit_message": {"type": "string"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
    },
    {
        "name": "github_list_workflow_runs",
        "description": (
            "List recent GitHub Actions workflow runs for a repository. "
            "Use when the user asks about CI builds, Actions status, or failing pipelines. "
            "Pass branch only when they named a specific branch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {
                    "type": "string",
                    "description": "Optional branch filter — use the exact branch name they gave",
                },
                "status": {
                    "type": "string",
                    "description": "queued, in_progress, completed, etc.",
                },
                "max_results": {"type": "integer", "description": "Max runs (1-30, default 10)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "github_get_workflow_run",
        "description": "Get status and conclusion for a specific GitHub Actions workflow run.",
        "parameters": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "run_id": {"type": "integer"},
            },
            "required": ["owner", "repo", "run_id"],
        },
    },
]

MAX_TOOL_ROUNDS = 8

GMAIL_TOOL_NAMES = {t["name"] for t in GMAIL_TOOL_DEFINITIONS}
CALENDAR_TOOL_NAMES = {t["name"] for t in CALENDAR_TOOL_DEFINITIONS}
SLACK_TOOL_NAMES = {t["name"] for t in SLACK_TOOL_DEFINITIONS}
JIRA_TOOL_NAMES = {t["name"] for t in JIRA_TOOL_DEFINITIONS}
GITHUB_TOOL_NAMES = {t["name"] for t in GITHUB_TOOL_DEFINITIONS}


def _clock_context() -> str:
    now = datetime.now(timezone.utc)
    return (
        f"Current date/time (UTC): {now.strftime('%Y-%m-%dT%H:%M:%SZ')}. "
        f"Today's calendar date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}). "
        "Resolve relative dates yourself (today, tomorrow, this week, next Monday, Friday, in 2 days) "
        "from this clock. Never ask the user for a calendar date when they already used relative "
        "language. When you state a plan, always show the resolved absolute date "
        "(e.g. 'tomorrow → 2026-07-13 (Monday)')."
    )


def _system_prompt(
    has_gmail: bool,
    has_calendar: bool,
    has_slack: bool,
    has_jira: bool,
    has_github: bool,
) -> str:
    connected = []
    if has_gmail:
        connected.append("Gmail")
    if has_calendar:
        connected.append("Google Calendar")
    if has_slack:
        connected.append("Slack")
    if has_jira:
        connected.append("Jira")
    if has_github:
        connected.append("GitHub")
    connected_label = ", ".join(connected) if connected else "none yet"

    parts = [
        "You are LetsConnect — a focused work assistant that helps the user with their connected "
        "tools (email, calendar, Slack, Jira, GitHub). You are not a general-purpose chatbot, tutor, "
        "search engine, or entertainment bot.",
        f"Currently connected for this user: {connected_label}.",
        "SCOPE — IN SCOPE: reading/searching/drafting/sending email; listing or scheduling calendar "
        "events; Slack DMs/channels; Jira tickets; GitHub repos/PRs/Actions; short help on how to use "
        "LetsConnect or connect an integration. "
        "OUT OF SCOPE (politely decline): trivia, homework, coding help unrelated to these tools, "
        "medical/legal/financial advice, politics, creative writing for fun, roleplay, jokes/riddles "
        "as the main ask, philosophy debates, or anything that does not need "
        "Gmail/Calendar/Slack/Jira/GitHub. "
        "If mixed (small chitchat + a real work ask), answer the work ask and lightly skip the rest.",
        "OFF-TOPIC REPLY STYLE: Be warm and brief — never rude or preachy. Acknowledge in one short "
        "line, explain you stay focused on their connected work apps, optionally give 1-2 example "
        "asks that match what they have connected, and invite them to try again. Do NOT call tools "
        "for out-of-scope requests. Do NOT invent work data. Do NOT lecture.",
        "Example off-topic reply: \"I'm tailored for your work stack (email, calendar, Slack, Jira, "
        "GitHub), so I can't help with that. Try something like 'What's on my calendar today?' or "
        "'Summarize unread email' — happy to help with those.\"",
        "TONE & UX: Friendly, clear, and confident. Prefer short paragraphs and bullets. "
        "Lead with the answer, then details. If something fails, say what went wrong and the next "
        "step (e.g. reconnect on Connected accounts). Never invent emails, meetings, messages, "
        "tickets, or PRs — use tools or say you don't have that data yet.",
        "Be proactive: call tools when you already have enough; only ask for details that are "
        "truly missing. If no useful integrations are connected, briefly say so and point them to "
        "Connected accounts on the dashboard.",
        _clock_context(),
        "DRAFTING: When the user asks you to write, draft, compose, or reply to a message or email, "
        "FIRST produce the full draft as text and show it to them — do NOT send yet. "
        "Render it clearly (for email, include a 'Subject:' line and the body; for chat, the message text). "
        "Then ask the user to confirm, edit, or send. "
        "Only call a send tool (send_email, slack_send_dm, slack_send_channel_message) AFTER the user "
        "explicitly approves (e.g. 'send it', 'yes', 'go ahead'). "
        "EXCEPTION: if the user clearly asks to send immediately in one step "
        "(e.g. 'email Alex saying I'll be late'), draft and send in the same turn. "
        "Match the tone the user requests (formal, friendly, brief); when unspecified, keep it professional and warm. "
        "If you lack details needed for a good draft (recipient, key facts), ask before drafting.",
    ]
    if has_gmail:
        parts.append(
            "Gmail tools: fetch real email data before answering email questions. "
            "Use list_unread, search_messages, get_thread, get_message. "
            "To send mail, use send_email — search first to find the right recipient address if you "
            "only have a name. When replying to an existing thread, read it first with get_thread so "
            "your draft quotes the right context and keeps the subject line. "
            "Always show the drafted subject and body for approval before calling send_email "
            "(unless the user asked to send in one step). "
            "If the user wants to keep editing in Gmail or save for later instead of sending, use "
            "create_draft to save it in their Gmail Drafts folder, then tell them it's saved as a draft."
        )
    if has_calendar:
        parts.append(
            "Google Calendar tools: schedule and manage meetings on the user's primary calendar. "
            "DATE RESOLUTION: For 'today', 'tomorrow', 'this week', or similar, compute YYYY-MM-DD "
            "from the system clock and call calendar_list_events immediately (prefer on_date for a "
            "single day, or time_min/time_max for a range). Do NOT ask which date they mean. "
            "SCHEDULING FLOW: When the user wants to book a meeting and gives a relative day "
            "(e.g. 'schedule a meeting tomorrow'): "
            "(1) Resolve the absolute date and say it clearly in your reply. "
            "(2) Ask only for missing essentials — agenda/title, start time (or propose one), "
            "teammates to invite (emails or names), and whether to add Google Meet. "
            "(3) If time is missing, propose a sensible default (e.g. 30 minutes mid-morning or "
            "early afternoon in their calendar timezone) and confirm once. "
            "(4) Show the full proposal (title, resolved date/time, attendees, Meet yes/no), then "
            "ask for confirmation before calendar_create_event — unless they asked to book in one step. "
            "Use ISO 8601 datetimes; use the timezone from calendar_list_events when available. "
            "Check conflicts with calendar_list_events before booking when helpful. "
            "When the user wants a video call, set add_google_meet=true. "
            "After creating, include the resolved date/time, html_link, and meet_link. "
            "For calendar_update_event, confirm changes first unless they asked to update immediately. "
            "For calendar_delete_event, ALWAYS get explicit confirmation — deletion is permanent."
        )
    if has_slack:
        parts.append(
            "Slack rules: DMs and channel posts are sent AS THE LOGGED-IN USER (their own Slack account), "
            "not as a bot. A DM to Rohit appears in the user's normal 1:1 DM with Rohit. "
            "STATUS / UPDATES: When the user asks what's new, any updates, status in a channel, or "
            "recent messages — if a channel is named, call slack_read_channel immediately and "
            "summarize (who said what, key points). If no channel is named, call slack_list_channels "
            "and ask which one, or read an obvious match if the name is clear from context. "
            "Prefer a short human summary over dumping raw message lists. "
            "ALWAYS call slack_send_dm before claiming a message was sent. "
            "Use slack_get_workspace, slack_list_users, slack_read_channel, slack_send_channel_message. "
            "Show the drafted message text for approval before sending (unless the user asked to send in one step). "
            "If slack_send_dm returns an error, report it — never claim success."
        )
    if has_jira:
        parts.append(
            "Jira rules: DEFAULT to the connected user's own tickets. When they ask about "
            "'my tickets', 'my issues', 'my tasks', 'assigned to me', or open work without "
            "naming someone else, call jira_list_my_issues (assignee = currentUser()). "
            "Only use jira_search_issues for all team tickets, project-wide boards, "
            "unassigned issues, or another person's tickets when explicitly requested. "
            "Use jira_get_me if you need the user's Jira display name. "
            "Use jira_get_issue for a single ticket by key. Use jira_list_projects when the user "
            "doesn't know project keys. "
            "CREATE FLOW: When creating a ticket, gather what's useful — project, type, summary, "
            "description, priority, due date / timeline, and estimate. Resolve relative due dates "
            "(Friday, next week, in 2 days) from the system clock and state the YYYY-MM-DD. "
            "Pass due_date and original_estimate (e.g. 2h, 1d) when the user gives them. "
            "If project or type is missing, ask or list projects first. "
            "Show the draft fields and ask for confirmation before jira_create_issue or "
            "jira_update_issue (unless the user asked to do it in one step). "
            "For jira_delete_issue, ALWAYS get explicit confirmation — deletion is permanent. "
            "Include browse_url links when sharing issue keys. Mention assignee, due date, and "
            "estimate when listing or confirming tickets."
        )
    if has_github:
        parts.append(
            "GitHub rules — be dynamic, never invent branch or PR data. "
            "IDENTITY: The connected GitHub login is who 'I/me/my' refers to. "
            "REPOS: For 'all my repos', 'repos assigned to me', or 'repos I have access to', "
            "call github_list_repos with NO query (max_results high enough). Results include "
            "private collaborator/org repos where they are NOT the owner — show access role "
            "(owner/collaborator/admin/read), private flag, owner, and html_url for each. "
            "Never claim they only own repos if collaborator entries are present. "
            "MY PRs: If they ask about PRs they raised/opened/created → "
            "github_search_my_pull_requests(role=authored). "
            "If they ask about PRs for them to review / waiting on their review → "
            "role=review_requested. If assigned to them → role=assigned. "
            "If vague ('my PRs', 'PRs for me') and unclear, prefer authored first, or ask once "
            "whether they mean opened-by-me vs review-requested. "
            "REPO PRs: When owner/repo is known, use github_list_pull_requests "
            "(optional head/base for branch→branch filters). "
            "REVIEW / DETAILS: For a specific PR (number or URL), call github_get_pull_request and "
            "ALWAYS reply with: title, description (summarize if long but keep key points), "
            "author, head → base branches, state, and the exact html_url link. "
            "When listing PRs, include title, #number, head→base, and html_url for each. "
            "BRANCHES: Never hardcode main/master/dev/staging. Use exactly the branch names the "
            "user gives. If missing or ambiguous, call github_list_branches (or use "
            "default_branch from github_list_repos only as a confirmed fact). "
            "CREATE PR FLOW: Need owner, repo, title, head (source), base (target), optional body. "
            "Example asks: 'PR from feature/login to release/2.1', 'raise PR feature-x into develop'. "
            "Show the draft fields and confirm before github_create_pull_request unless they asked "
            "to create in one step. After create, return title + html_url. "
            "MERGE: ALWAYS confirm before github_merge_pull_request. "
            "ACTIONS: github_list_workflow_runs / github_get_workflow_run; pass branch only when named. "
            "If repo is unclear, github_list_repos first. Always include clickable html_url links. "
            "If collaborator/org private repos seem missing after listing, tell them to disconnect "
            "and reconnect GitHub so repo + read:org scopes are granted."
        )
    if not has_gmail:
        parts.append(
            "Gmail is not connected — if they ask about email, politely ask them to connect Gmail "
            "from Connected accounts."
        )
    if not has_calendar:
        parts.append(
            "Google Calendar is not available — if they ask about meetings or scheduling, explain "
            "they need Calendar access (usually via reconnecting Gmail with Calendar permission)."
        )
    if not has_slack:
        parts.append(
            "Slack is not connected — if they ask to message teammates or read channels, politely "
            "ask them to connect Slack from Connected accounts."
        )
    if not has_jira:
        parts.append(
            "Jira is not connected — if they ask about tickets, politely ask them to connect Jira "
            "from Connected accounts; do not invent issue data."
        )
    if not has_github:
        parts.append(
            "GitHub is not connected — if they ask about repos, PRs, or Actions, politely ask them "
            "to connect GitHub from Connected accounts; do not invent PR or build data."
        )
    if not has_gmail and not has_calendar and not has_slack and not has_jira and not has_github:
        parts.append(
            "No integrations are connected. Welcome them briefly, explain LetsConnect helps with "
            "Gmail, Calendar, Slack, Jira, and GitHub once linked, and point them to Connected accounts. "
            "Politely decline unrelated questions the same way."
        )
    return " ".join(parts)


def _run_tool(
    name: str,
    args: dict[str, Any],
    *,
    gmail: GmailClient | None,
    calendar: CalendarClient | None,
    slack: SlackTools | None,
    jira: JiraTools | None,
    github: GithubTools | None,
) -> Any:
    if name in GMAIL_TOOL_NAMES:
        if not gmail:
            raise ValueError("Gmail not connected")
        if name == "get_profile":
            return gmail.get_profile()
        if name == "list_labels":
            return gmail.list_labels()
        if name == "list_unread":
            return gmail.list_unread(max_results=args.get("max_results", 10))
        if name == "search_messages":
            return gmail.search_threads(args["query"], max_results=args.get("max_results", 10))
        if name == "get_thread":
            return gmail.get_thread(args["thread_id"])
        if name == "get_message":
            return gmail.get_message(args["message_id"])
        if name == "create_draft":
            return gmail.create_draft(args["to"], args["subject"], args["body"])
        if name == "send_email":
            return gmail.send_email(args["to"], args["subject"], args["body"])

    if name in CALENDAR_TOOL_NAMES:
        if not calendar:
            raise ValueError("Google Calendar not connected — reconnect Gmail to grant calendar access")
        if name == "calendar_list_events":
            return calendar.list_events(
                time_min=args.get("time_min"),
                time_max=args.get("time_max"),
                on_date=args.get("on_date"),
                max_results=args.get("max_results", 10),
                query=args.get("query"),
                timezone_name=args.get("timezone"),
            )
        if name == "calendar_get_event":
            return calendar.get_event(args["event_id"])
        if name == "calendar_create_event":
            return calendar.create_event(
                args["summary"],
                args["start"],
                args["end"],
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
                timezone_name=args.get("timezone"),
                all_day=args.get("all_day", False),
                add_google_meet=args.get("add_google_meet", False),
            )
        if name == "calendar_update_event":
            return calendar.update_event(
                args["event_id"],
                summary=args.get("summary"),
                start=args.get("start"),
                end=args.get("end"),
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
                timezone_name=args.get("timezone"),
                all_day=args.get("all_day", False),
            )
        if name == "calendar_delete_event":
            return calendar.delete_event(args["event_id"])

    if name in SLACK_TOOL_NAMES:
        if not slack:
            raise ValueError("Slack not connected")
        if name == "slack_list_users":
            return slack.list_users(limit=args.get("limit", 50))
        if name == "slack_list_channels":
            return slack.list_channels(limit=args.get("limit", 50))
        if name == "slack_get_workspace":
            return slack.get_workspace()
        if name == "slack_read_channel":
            return slack.read_channel(args["channel"], limit=args.get("limit", 15))
        if name == "slack_send_dm":
            return slack.send_dm(args["user"], args["text"])
        if name == "slack_send_channel_message":
            return slack.send_channel_message(args["channel"], args["text"])

    if name in JIRA_TOOL_NAMES:
        if not jira:
            raise ValueError("Jira not connected")
        if name == "jira_get_site":
            return jira.get_site()
        if name == "jira_get_me":
            return jira.get_me()
        if name == "jira_list_projects":
            return jira.list_projects(max_results=args.get("max_results", 50))
        if name == "jira_list_my_issues":
            return jira.list_my_issues(
                status=args.get("status"),
                project_key=args.get("project_key"),
                additional_jql=args.get("additional_jql"),
                max_results=args.get("max_results", 20),
            )
        if name == "jira_search_issues":
            return jira.search_issues(args["jql"], max_results=args.get("max_results", 20))
        if name == "jira_get_issue":
            return jira.get_issue(args["issue_key"])
        if name == "jira_create_issue":
            return jira.create_issue(
                args["project_key"],
                args["summary"],
                args["issue_type"],
                description=args.get("description"),
                priority=args.get("priority"),
                due_date=args.get("due_date"),
                original_estimate=args.get("original_estimate"),
            )
        if name == "jira_update_issue":
            return jira.update_issue(
                args["issue_key"],
                summary=args.get("summary"),
                description=args.get("description"),
                priority=args.get("priority"),
                due_date=args.get("due_date"),
                original_estimate=args.get("original_estimate"),
            )
        if name == "jira_delete_issue":
            return jira.delete_issue(args["issue_key"])

    if name in GITHUB_TOOL_NAMES:
        if not github:
            raise ValueError("GitHub not connected")
        if name == "github_get_me":
            return github.get_me()
        if name == "github_list_repos":
            return github.list_repos(
                query=args.get("query"),
                affiliation=args.get("affiliation"),
                max_results=args.get("max_results", 100),
            )
        if name == "github_list_branches":
            return github.list_branches(
                args["owner"],
                args["repo"],
                query=args.get("query"),
                max_results=args.get("max_results", 30),
            )
        if name == "github_search_my_pull_requests":
            return github.search_my_pull_requests(
                role=args.get("role", "authored"),
                state=args.get("state", "open"),
                repo=args.get("repo"),
                query=args.get("query"),
                max_results=args.get("max_results", 20),
            )
        if name == "github_list_pull_requests":
            return github.list_pull_requests(
                args["owner"],
                args["repo"],
                state=args.get("state", "open"),
                head=args.get("head"),
                base=args.get("base"),
                max_results=args.get("max_results", 20),
            )
        if name == "github_get_pull_request":
            return github.get_pull_request(args["owner"], args["repo"], args["pull_number"])
        if name == "github_create_pull_request":
            return github.create_pull_request(
                args["owner"],
                args["repo"],
                title=args["title"],
                head=args["head"],
                base=args["base"],
                body=args.get("body"),
                draft=args.get("draft", False),
            )
        if name == "github_merge_pull_request":
            return github.merge_pull_request(
                args["owner"],
                args["repo"],
                args["pull_number"],
                merge_method=args.get("merge_method", "merge"),
                commit_title=args.get("commit_title"),
                commit_message=args.get("commit_message"),
            )
        if name == "github_list_workflow_runs":
            return github.list_workflow_runs(
                args["owner"],
                args["repo"],
                branch=args.get("branch"),
                status=args.get("status"),
                max_results=args.get("max_results", 10),
            )
        if name == "github_get_workflow_run":
            return github.get_workflow_run(args["owner"], args["repo"], args["run_id"])

    raise ValueError(f"Unknown tool: {name}")


def _gemini_config(tool_definitions: list[dict[str, Any]], system_instruction: str) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=tool_definitions)],
        system_instruction=system_instruction,
    )


def _extract_reply(parts: list[types.Part]) -> str:
    return "".join(part.text for part in parts if part.text)


def _gemini_finish_reason(candidate: Any) -> str | None:
    reason = getattr(candidate, "finish_reason", None)
    if reason is None:
        return None
    return str(getattr(reason, "name", reason))


def _empty_candidate_message(candidate: Any) -> str:
    reason = _gemini_finish_reason(candidate)
    if reason and reason not in {"STOP", "FinishReason.STOP"}:
        return f"Gemini blocked the response ({reason}). Try rephrasing your question."
    return "Gemini returned an empty response. Please try again."


def _build_contents(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> list[types.Content]:
    contents: list[types.Content] = []
    for item in history or []:
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=item["content"])])
        )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    return contents


def _finalize_reply(
    reply: str,
    tools_used: list[str],
    *,
    slack_dm_result: dict[str, Any] | None,
    slack_dm_error: str | None,
) -> str:
    reply_lower = reply.lower()
    claims_slack_sent = any(
        phrase in reply_lower
        for phrase in ("sent a dm", "i sent", "message sent", "saying '", "saying \"")
    )

    if claims_slack_sent and "slack_send_dm" not in tools_used:
        return (
            "I could not confirm that Slack message was sent — the send API was not called. "
            "Please try again."
        )

    if "slack_send_dm" in tools_used and slack_dm_error and not slack_dm_result:
        return f"Failed to send Slack DM: {slack_dm_error}"

    if slack_dm_result and slack_dm_result.get("ts"):
        recipient = slack_dm_result.get("user_name", "the recipient")
        if slack_dm_result.get("sent_as") == "user":
            note = (
                f"\n\n**Delivered:** Sent as you in your normal DM with {recipient}. "
                "Open that conversation in Slack to see it."
            )
        else:
            note = (
                f"\n\n**Where to find it:** Sent by the **LetsConnect bot** to {recipient}. "
                "They see it under **Apps → LetsConnect** in Slack."
            )
        if note.strip() not in reply:
            reply += note

    return reply


def run_agent(
    db: Session,
    user_id: int,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_keys:
        raise ValueError("GEMINI_API_KEY is not set")

    gmail_conn = get_gmail_connection(db, user_id)
    slack_conn = get_slack_connection_by_user(db, user_id)
    jira_conn = get_jira_connection(db, user_id)
    github_conn = get_github_connection(db, user_id)
    if not gmail_conn and not slack_conn and not jira_conn and not github_conn:
        raise ValueError("Connect Gmail, Slack, Jira, or GitHub in the dashboard first")

    google_creds = None
    gmail = None
    calendar = None
    if gmail_conn:
        try:
            gmail_conn, google_creds = get_google_credentials(db, user_id)
            gmail = GmailClient(credentials=google_creds)
            if has_calendar_access(gmail_conn, creds=google_creds):
                calendar = CalendarClient(credentials=google_creds)
        except ValueError:
            gmail = None
            calendar = None
    slack = (
        SlackTools(
            get_slack_bot_token(slack_conn),
            user_token=get_slack_user_token(slack_conn),
            team_id=slack_conn.slack_team_id,
        )
        if slack_conn
        else None
    )
    jira = get_jira_tools_for_user(db, user_id) if jira_conn else None
    github = get_github_tools_for_user(db, user_id) if github_conn else None

    tool_definitions: list[dict[str, Any]] = []
    if gmail:
        tool_definitions.extend(GMAIL_TOOL_DEFINITIONS)
    if calendar:
        tool_definitions.extend(CALENDAR_TOOL_DEFINITIONS)
    if slack:
        tool_definitions.extend(SLACK_TOOL_DEFINITIONS)
    if jira:
        tool_definitions.extend(JIRA_TOOL_DEFINITIONS)
    if github:
        tool_definitions.extend(GITHUB_TOOL_DEFINITIONS)

    system_instruction = _system_prompt(
        bool(gmail), bool(calendar), bool(slack), bool(jira), bool(github)
    )
    if gmail_conn and google_creds and not has_calendar_access(gmail_conn, creds=google_creds):
        system_instruction += (
            " Google Calendar is NOT connected yet — if the user asks about meetings or scheduling, "
            "tell them to click Gmail on the dashboard integration graph (or 'Enable Google Calendar') "
            "to reconnect, then allow the Calendar permission on the Google consent screen. "
            "Do NOT call calendar tools."
        )
    if jira_conn and jira_conn.jira_display_name:
        system_instruction += (
            f" The connected Jira user is {jira_conn.jira_display_name!r} — "
            "'my tickets' means issues assigned to this user."
        )
    if github_conn and github_conn.github_login:
        system_instruction += (
            f" The connected GitHub user is {github_conn.github_login!r} — "
            "'my PRs' / 'PRs I raised' means author=that login; "
            "'PRs for my review' means review-requested for that login."
        )

    gemini_pool = GeminiKeyPool(settings.gemini_api_keys)
    tools_used: list[str] = []
    slack_dm_result: dict[str, Any] | None = None
    slack_dm_error: str | None = None
    config = _gemini_config(
        tool_definitions,
        system_instruction,
    )

    contents: list[types.Content] = _build_contents(message, history)
    empty_retries = 0

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = gemini_generate_content(
                gemini_pool,
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except ValueError:
            raise

        if not response.candidates:
            raise ValueError("Gemini returned no response")

        candidate = response.candidates[0]
        model_content = candidate.content
        if not model_content or not model_content.parts:
            if empty_retries < 1:
                empty_retries += 1
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="Please answer in plain text.")],
                    )
                )
                continue
            raise ValueError(_empty_candidate_message(candidate))

        function_calls = [part for part in model_content.parts if part.function_call]
        if function_calls:
            contents.append(model_content)
            tool_response_parts: list[types.Part] = []

            for part in function_calls:
                function_call = part.function_call
                if not function_call or not function_call.name:
                    continue

                name = function_call.name
                args = dict(function_call.args) if function_call.args else {}
                tools_used.append(name)
                try:
                    result = _run_tool(
                        name,
                        args,
                        gmail=gmail,
                        calendar=calendar,
                        slack=slack,
                        jira=jira,
                        github=github,
                    )
                except Exception as exc:
                    result = {"error": str(exc)}

                if name == "slack_send_dm":
                    if isinstance(result, dict) and result.get("error"):
                        slack_dm_error = str(result["error"])
                    elif isinstance(result, dict) and result.get("ok") and result.get("ts"):
                        slack_dm_result = result
                        slack_dm_error = None

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=name,
                        response={"result": json.loads(json.dumps(result, default=str))},
                    )
                )

            if not tool_response_parts:
                raise ValueError("Gemini issued invalid tool calls — please try again.")

            contents.append(types.Content(role="user", parts=tool_response_parts))
            continue

        reply = _finalize_reply(
            _extract_reply(model_content.parts),
            tools_used,
            slack_dm_result=slack_dm_result,
            slack_dm_error=slack_dm_error,
        )
        return {"reply": reply, "tools_used": tools_used}

    return {
        "reply": "I couldn't finish answering — too many steps. Try a simpler question.",
        "tools_used": tools_used,
    }
