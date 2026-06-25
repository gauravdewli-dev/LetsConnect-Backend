# Gmail MCP Server — Google Cloud Setup

Follow these steps once before using the Gmail MCP server.

## 1. Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project or select an existing one.

## 2. Enable the Gmail API

1. Navigate to **APIs & Services → Library**.
2. Search for **Gmail API**.
3. Click **Enable**.

Or via CLI:

```bash
gcloud services enable gmail.googleapis.com --project=YOUR_PROJECT_ID
```

## 3. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose **External** (or **Internal** if you use Google Workspace).
3. Fill in the app name (e.g. `Gmail MCP Server`) and your support email.
4. Under **Scopes**, add:

   ```
   https://www.googleapis.com/auth/gmail.modify
   ```

5. If using **External**, add yourself under **Test users**.
6. Save.

## 4. Create OAuth credentials (Web application)

1. Go to **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. Application type: **Web application**.
4. Name: e.g. `Gmail MCP Local`.
5. Under **Authorised redirect URIs**, add:

   ```
   http://localhost:8080/
   http://127.0.0.1:8080/
   ```

6. Download the JSON file and save it as `credentials.json` in the project root.

> **Note:** Desktop app credentials also work as a fallback. Web application is the recommended type for local testing with a fixed redirect URI.

## 5. Configure environment

Copy `.env.example` to `.env` and set paths if needed:

```
GMAIL_CREDENTIALS_PATH=./credentials.json
GMAIL_TOKEN_PATH=./token.json
GMAIL_OAUTH_REDIRECT_URI=http://localhost:8080/
GMAIL_OAUTH_PORT=8080
```

The redirect URI and port must match what you registered in Google Cloud Console.

## 6. Authenticate

```bash
uv sync
uv run python -m gmail_mcp.auth
```

A browser window opens on `localhost:8080`. Sign in with your Google account and grant access. A `token.json` file is saved for future use.

## 7. Connect to Cursor

Reload MCP servers in Cursor (or restart). The project `.cursor/mcp.json` already points to this server.

## 8. Web UI (optional)

Run the local web interface on the same port as your OAuth redirect URI:

```bash
uv run uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080

The web UI uses the same redirect URI as CLI auth: `http://localhost:8080/`

Ensure that URI is listed under **Authorised redirect URIs** in Google Cloud Console, then click **Connect Gmail** in the UI.

### Gmail chat agent

Add your OpenAI API key to `.env`:

```
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini
```

After connecting Gmail, use the chat UI to ask questions like:
- "How many unread emails do I have?"
- "Summarize emails from John this week"
- "Show me the full content of my latest unread email"

## Troubleshooting

- **Access blocked / app not verified**: Add your account as a test user on the OAuth consent screen.
- **redirect_uri_mismatch**: Ensure Google Console redirect URIs exactly match `GMAIL_OAUTH_REDIRECT_URI` (including trailing slash).
- **Port already in use**: Change `GMAIL_OAUTH_PORT` and update the matching redirect URI in Google Console.
- **Token expired**: Re-run `uv run python -m gmail_mcp.auth`.
- **credentials.json not found**: Check `GMAIL_CREDENTIALS_PATH` in `.env`.
