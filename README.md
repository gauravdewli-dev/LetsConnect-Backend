# LetsConnect — Backend

FastAPI service for authentication, OAuth integrations (Gmail, Slack, Jira), Gemini agent tools, and persistent chat (PostgreSQL + MongoDB).

## Requirements

- Python **3.13+**
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- **PostgreSQL** (e.g. Neon)
- **MongoDB Atlas** (free M0 tier is fine for development)
- Google Cloud OAuth credentials (`credentials.json`) for Gmail
- Optional: Slack app, Jira OAuth app, Gemini API key, email provider (Brevo/SMTP)

## Setup

```bash
cd LC-Backend
cp .env.example .env
# Edit .env — see Environment variables below
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

API base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

### Verify MongoDB

```bash
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
import certifi; from pymongo import MongoClient; import os
c = MongoClient(os.environ['MONGODB_URI'], tlsCAFile=certifi.where())
print(c.admin.command('ping'))
"
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `MONGODB_URI` | Yes | MongoDB Atlas URI |
| `MONGODB_DB_NAME` | No | Database name (default: `letsconnect`) |
| `JWT_SECRET` | Yes | Signs access/refresh tokens and OAuth state |
| `ENCRYPTION_KEY` | No | Fernet key for OAuth token encryption; derived from `JWT_SECRET` if empty |
| `BACKEND_URL` | No | Default `http://localhost:8000` — used in OAuth redirect URIs |
| `FRONTEND_URL` | No | Default `http://localhost:5173` — CORS + post-OAuth redirects |
| `GEMINI_API_KEY` | Yes* | Google AI Studio key for the agent |
| `GEMINI_MODEL` | No | Default `gemini-2.5-flash` |
| `GMAIL_CREDENTIALS_PATH` | Yes* | Path to Google OAuth `credentials.json` |
| `SLACK_CLIENT_ID` / `SECRET` / `SIGNING_SECRET` | For Slack | From [api.slack.com/apps](https://api.slack.com/apps) |
| `SLACK_APP_ID` | No | For “Open in Slack” links in status API |
| `JIRA_CLIENT_ID` / `JIRA_CLIENT_SECRET` | For Jira | Atlassian developer console |
| `EMAIL_PROVIDER`, `BREVO_API_KEY`, etc. | For signup OTP | See `.env.example` |

\*Required for chat/agent features that use Gmail or Gemini.

Copy `.env.example` and fill values. Never commit `.env` or `credentials.json`.

## Project structure

```
app/
├── main.py                 # FastAPI app, CORS, startup (schema + Mongo indexes)
├── config.py               # Settings from env
├── security.py             # JWT, password hashing, token encryption
├── api/
│   ├── routes_auth.py      # Signup, login, refresh, /auth/me
│   ├── routes_connect.py   # Integrations, chat, OAuth callbacks
│   └── routes_slack.py     # Slack Events API
├── service/
│   ├── letsconnect_agent.py   # Gemini agent + tools
│   ├── chat_service.py        # Mongo messages + Postgres conversations
│   ├── integration_connect.py # OAuth URL builders
│   ├── gmail/                   # Gmail API client + OAuth scopes
│   └── slack_disconnect.py    # Uninstall app + revoke on disconnect
├── schema/                 # SQLAlchemy models (users, connections, conversations)
├── configs/
│   ├── database/           # Postgres engine + migrations
│   └── mongodb/            # Mongo client + indexes
└── middleware/
    ├── security.py         # Rate limits, security headers
    └── access_log.py       # Redacts OAuth codes from uvicorn access logs
```

## Data model

### PostgreSQL

- **users** — email, password, verification
- **gmail_connections** / **slack_connections** / **jira_connections** — encrypted OAuth tokens
- **conversations** — `conversation_id` (UUID), `user_id`, `is_primary`, optional `slack_channel_id`

### MongoDB

- **Collection:** `messages` (in `MONGODB_DB_NAME`)
- **Fields:** `conversation_id`, `user_id`, `role`, `content`, `channel` (`web` | `slack`), `tools_used`, `created_at`
- **Indexes:** `(conversation_id, created_at)`, `(user_id, created_at)`

Web chat and Slack DMs for the same user share the **primary conversation** and message history.

## API overview

All `/api/*` routes (except OAuth browser redirects) expect:

```http
Authorization: Bearer <access_token>
```

### Auth (`/auth`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/signup` | Register (email verification OTP) |
| POST | `/auth/verify-email` | Verify OTP → tokens |
| POST | `/auth/login` | Login → tokens |
| POST | `/auth/refresh` | New access + refresh token |
| POST | `/auth/logout` | Revoke refresh session |
| GET | `/auth/me` | Current user |

### Connections & chat

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Integration status (DB only, fast) |
| POST | `/api/connections/backfill-profiles` | Sync missing display names |
| GET | `/api/integrations/{gmail\|slack\|jira}/connect-url` | OAuth start URL (authenticated) |
| DELETE | `/api/gmail` | Disconnect Gmail |
| DELETE | `/api/slack` | Disconnect Slack, uninstall app, **clear chat history** |
| DELETE | `/api/jira` | Disconnect Jira |
| GET | `/api/chat/messages` | Paginated history (`limit`, `before` cursor) |
| POST | `/api/chat` | Send message → agent reply (history from Mongo) |

### OAuth callbacks (browser redirects)

| Path | Provider |
|------|----------|
| `/oauth/callback` | Gmail |
| `/slack/oauth/callback` | Slack |
| `/jira/oauth/callback` | Jira |

Legacy token-in-query routes (`/gmail/connect?token=`, etc.) still work; the frontend prefers `/api/integrations/*/connect-url`.

### Slack

| Method | Path | Description |
|--------|------|-------------|
| POST | `/slack/events` | Event Subscriptions (DMs, app mention) |
| POST | `/slack/interactions` | Interactive components |

## Chat flow

1. Client `POST /api/chat` with `{ message, conversation_id? }`.
2. Server resolves primary `conversation_id` for user (Postgres).
3. Loads last 20 messages from Mongo for agent context.
4. Runs Gemini agent with Gmail/Slack/Jira tools.
5. Saves user + assistant messages to Mongo.
6. Returns `{ reply, tools_used, conversation_id }`.

Slack DMs use the same `handle_chat_message(..., channel="slack")` path.

## OAuth setup (summary)

### Gmail

1. Google Cloud Console → OAuth client → download `credentials.json`.
2. Redirect URI: `{BACKEND_URL}/oauth/callback`
3. Scopes: Gmail read/send (see agent / MCP setup).

### Slack

1. Create app at [api.slack.com/apps](https://api.slack.com/apps).
2. OAuth redirect: `{BACKEND_URL}/slack/oauth/callback`
3. Event Subscriptions URL: `{BACKEND_URL}/slack/events` (needs public HTTPS in prod; use ngrok locally).
4. Subscribe to `message.im`, `app_mention`, `app_home_opened`.
5. Bot + User scopes — see `app/constants.py` (`SLACK_BOT_SCOPES`, `SLACK_USER_SCOPES`).

### Jira

1. [Atlassian Developer Console](https://developer.atlassian.com/console/myapps) → OAuth 2.0 app.
2. Callback: `{BACKEND_URL}/jira/oauth/callback`
3. Scopes: `read:jira-work`, `write:jira-work`, `read:jira-user`, `offline_access`.

## Security

- Integration tokens encrypted at rest (Fernet).
- JWT access tokens (~60 min) + refresh tokens (~7 days).
- OAuth `state` JWT includes PKCE verifier for Gmail.
- **Access logs** redact OAuth query strings (`code`, `state`, `token`) — see `middleware/access_log.py`.
- Disconnecting Slack calls `apps.uninstall` and clears that user’s chat data.

## Development commands

```bash
uv sync                          # Install dependencies
uv run uvicorn app.main:app --reload --port 8000
uv add <package>                 # Add dependency
```

Schema migrations run automatically on startup via `configs/database/migrate.py`.

## Troubleshooting

| Issue | Check |
|-------|--------|
| `Invalid token` on connect | Log in again; use `/api/integrations/*/connect-url` (not stale JWT in URL) |
| MongoDB SSL error on macOS | `certifi` is included; ensure `MONGODB_URI` password is URL-encoded |
| Slack bot not replying | Event URL reachable, `SLACK_SIGNING_SECRET` correct, user linked via OAuth |
| Gmail connect fails | `credentials.json` path, redirect URI matches Google Console |
| CORS errors | `FRONTEND_URL` matches Vite origin exactly |
