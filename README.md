# prj3bot

Personal AI assistant framework with a local browser app, CLI, tool-using agent loop, and multi-channel gateway support (Telegram, Email, WhatsApp, Discord, Slack).

![prj3bot architecture](./prj3bot_arch.png)

## What This Project Does

- Runs as a local browser app with natural-language chat and automatic intent routing.
- Runs an AI agent locally from CLI (`prj3bot-cli agent`).
- Runs as a gateway bot for chat channels (`prj3bot-cli gateway`).
- Supports email workflows, Google Workspace workflows, and web search through normal conversation.
- Uses configurable LLM providers (Gemini, OpenAI, Anthropic, OpenRouter, DeepSeek, etc.).
- Includes tools: filesystem, shell, web search/fetch, cron scheduling, outbound messaging, spawn/subagents, MCP servers.

## Current Channel Support

Enabled in codebase:

- Telegram
- Email
- WhatsApp
- Discord
- Slack


## High-Level Architecture

```text
Inbound Channel Message
  (Telegram / Email / etc.)
          |
          v
      MessageBus  <----->  ChannelManager (outbound dispatcher)
          |
          v
       AgentLoop
     - ContextBuilder
     - SessionManager
     - MemoryStore
     - ToolRegistry
          |
          +--> LLM Provider (LiteLLM / Custom / OAuth providers)
          |
          +--> Tools
               - read/write/edit/list files
               - shell exec
               - web search/fetch
               - message send
               - cron
               - spawn/subagents
               - MCP tools
          |
          v
    OutboundMessage -> ChannelManager -> Destination Channel

Background Services:
- CronService (scheduled jobs)
- HeartbeatService (periodic proactive tasks)
```

## Prerequisites

- Python `>= 3.11`
- `pip`
- Optional: Node.js `>= 18` (only needed for WhatsApp bridge login flow)

## Installation

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

## First-Time Setup

```powershell
prj3bot-cli onboard
```

This creates:

- Config: `~/.prj3bot/config.json`
- Workspace: `~/.prj3bot/workspace`
- Starter templates (SOUL, USER, TOOLS, AGENTS, HEARTBEAT, memory files)

Check setup:

```powershell
prj3bot-cli status
```

## Core Config You Must Know

Primary config file:

- `~/.prj3bot/config.json`

Most important paths:

- `agents.defaults.model`: default model
- `providers.<provider>.apiKey`: provider key
- `channels.<channel>.enabled`: enable/disable channel
- `channels.<channel>.allowFrom`: access control list
- `gateway.port`: gateway port
- `gateway.heartbeat`: periodic autonomous tasks

Important behavior:

- `allowFrom` empty means deny all for that channel.
- Use `["*"]` for open access (not recommended for production).

## Gemini Setup (Recommended)

Set these in `~/.prj3bot/config.json`:

```json
{
  "agents": {
    "defaults": {
      "model": "gemini/gemini-2.5-flash-lite"
    }
  },
  "providers": {
    "gemini": {
      "apiKey": "YOUR_GEMINI_API_KEY"
    }
  }
}
```

Where to change model in future:

- `~/.prj3bot/config.json` -> `agents.defaults.model`

## Run Modes

### 1. Local App

Launch the browser UI and backend together:

```powershell
prj3bot
```

The app opens automatically and you can ask things naturally:

- "Email Sarah and tell her the meeting moved to 3 PM"
- "Create a Google Doc titled Project Notes"
- "Search the web for local Flask packaging"

### 2. Legacy CLI Agent

```powershell
prj3bot-cli agent
prj3bot-cli agent -m "Plan my next 7 days for exam prep."
```

### 3. Gateway (Channels + Agent + Cron + Heartbeat)

```powershell
prj3bot-cli gateway
```

## Telegram Setup

Set in `~/.prj3bot/config.json`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN",
      "allowFrom": ["*"]
    }
  }
}
```

Then run:

```powershell
prj3bot gateway
```

## Telegram Mail Command Mode (Important)

To execute mail commands from Telegram, use this prefix:

- `prj3bot mail <command>`

Examples:

- `prj3bot mail read3`
- `prj3bot mail unread 5`
- `prj3bot mail show 1`
- `prj3bot mail reply 1 Thanks, I got it.`
- `prj3bot mail send abc@gmail.com | Subject | Body`
- `prj3bot mail send to abc@gmail.com (ai do a detailed research on recent news and send top 5 points with proper subject)`

If you send only `Read3` without prefix, the bot will prompt you to use the prefixed format.

## Google Workspace Setup (`gog`)

### 1. Enable Google integration in config

Set in `~/.prj3bot/config.json`:

```json
{
  "googleWorkspace": {
    "enabled": true,
    "credentialsJson": "{\"installed\":{\"client_id\":\"...\",\"project_id\":\"...\",\"auth_uri\":\"https://accounts.google.com/o/oauth2/auth\",\"token_uri\":\"https://oauth2.googleapis.com/token\",\"auth_provider_x509_cert_url\":\"https://www.googleapis.com/oauth2/v1/certs\",\"client_secret\":\"...\",\"redirect_uris\":[\"http://localhost\"]}}",
    "credentialsPath": "~/.prj3bot/google/credentials.json",
    "tokenPath": "~/.prj3bot/google/token.json"
  }
}
```

`credentialsJson` or `credentialsPath` is required.

### 2. Install Google dependencies

```powershell
pip install google-api-python-client google-auth google-auth-oauthlib
```

### 3. First auth run

Run a real Google API command (this triggers OAuth):

```powershell
prj3bot gog -m "drive list 5"
```

On first real Google API call, OAuth login opens and token is saved to:

- `~/.prj3bot/google/token.json`

If you later enable new Google features such as Calendar or Meet, rerun OAuth once so the token gets the new scopes.

## GitHub MCP Setup

Add this under `tools.mcpServers` in `~/.prj3bot/config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "github": {
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {
          "Authorization": "Bearer <your-github-pat>"
        },
        "toolTimeout": 30
      }
    }
  }
}
```

What you can do after GitHub MCP is connected depends on the tools exposed by the GitHub MCP server, but common actions include repository browsing, issue lookup, pull request lookup, and file/content retrieval through MCP tools inside the agent loop.

## Google CLI Command Mode

One-shot:

```powershell
prj3bot gog -m "doc do create a 200 words article on transformer and give me that link"
prj3bot gog -m "sheet do create a weekly study tracker"
prj3bot gog -m "calendar list 5"
prj3bot gog -m "calendar events primary 10"
prj3bot gog -m "calendar create primary | Deep Work | 2026-03-15 18:00 | 2026-03-15 19:30 | Research block"
prj3bot gog -m "calendar meet primary | Viva Prep | 2026-03-16 20:00 | 2026-03-16 21:00 | With Meet link"
prj3bot gog -m "drive list 10"
prj3bot gog -m "meet create"
prj3bot gog -m "classroom courses 10"
prj3bot gog -m "slides do create a beginner deck on transformers"
```

Interactive:

```powershell
prj3bot gog
```

Supported commands:

- `doc <instruction>`
- `doc list [N]`
- `doc read <index|id|url>`
- `sheet <instruction>`
- `sheet list [N]`
- `sheet read <index|id|url> [A1:Z50]`
- `sheet append <index|id|url> | value1 | value2 ...`
- `calendar list [N]`
- `calendar events [calendar|index|primary] [N]`
- `calendar create <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `calendar meet <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `drive list [N]`
- `drive search <query>`
- `drive show <index|id|url>`
- `meet create`
- `meet show <spaceId|meetingCode|url>`
- `slides <instruction>`
- `slides list [N]`
- `slides read <index|id|url>`
- `classroom courses [N]`
- `classroom coursework <courseId|index> [N]`
- `classroom announcements <courseId|index> [N]`
- `help`, `exit`

## Telegram Google Workspace Mode

Use these prefixes directly in Telegram:

- `prj3bot doc do create a 200 words article on transformer and give me that link`
- `prj3bot sheet do create a weekly study tracker`
- `prj3bot calendar list 5`
- `prj3bot calendar events primary 10`
- `prj3bot calendar meet primary | Group Study | 2026-03-18 19:00 | 2026-03-18 20:00 | Meet link included`
- `prj3bot drive list 5`
- `prj3bot meet create`
- `prj3bot classroom courses 10`
- `prj3bot slides do create a deck on attention mechanism`
- `prj3bot gog help`

Normal Telegram / agent requests can also use Google and GitHub after restart. Examples:

- `Create a Google Calendar event tomorrow at 6 PM for revision and include a Meet link.`
- `List my next 10 Google Calendar events.`
- `Create a new Google Meet link for a study group.`
- `List my recent Drive files.`
- `Show my Google Classroom courses.`
- `Create a new GitHub repository named prj3bot-demo.`
- `Create a new file README.md in my repo and add starter content.`
- `Open a pull request from branch feature-x into main with a short summary.`

## Gmail as Email Gateway

### 1. Gmail account prep

- Enable 2-step verification in Google account.
- Generate an App Password (16 chars).
- Use the app password for IMAP/SMTP auth (not your normal Gmail password).

### 2. Email config

Set in `~/.prj3bot/config.json`:

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUseSsl": true,
      "imapUsername": "your@gmail.com",
      "imapPassword": "YOUR_APP_PASSWORD",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUseTls": true,
      "smtpUsername": "your@gmail.com",
      "smtpPassword": "YOUR_APP_PASSWORD",
      "fromAddress": "your@gmail.com",
      "pollIntervalSeconds": 30,
      "autoReplyEnabled": true,
      "allowFrom": ["*"]
    }
  }
}
```

### 3. Start gateway

```powershell
prj3bot gateway
```

Now you can:

- Use CLI mail mode (`prj3bot mail`)
- Use Telegram mail prefix commands (`prj3bot mail ...`)
- Receive inbound email into agent flow (if channel enabled + allowFrom permits sender)

## All CLI Commands

Top-level:

- `prj3bot --help`
- `prj3bot -v` or `prj3bot --version`
- `prj3bot onboard`
- `prj3bot status`
- `prj3bot gateway`
- `prj3bot gateway -p <port>`
- `prj3bot gateway --verbose`
- `prj3bot agent`
- `prj3bot agent -m "<message>"`
- `prj3bot agent -s "<session_id>"`
- `prj3bot agent --markdown` or `--no-markdown`
- `prj3bot agent --logs` or `--no-logs`
- `prj3bot mail`
- `prj3bot mail -m "<mail command>"`
- `prj3bot mail -n <default_read_limit>`
- `prj3bot gog`
- `prj3bot gog -m "<google command>"`
- `prj3bot gog -n <default_list_limit>`
- `prj3bot channels status`
- `prj3bot channels login`
- `prj3bot provider login openai-codex`
- `prj3bot provider login github-copilot`

Useful help commands:

- `prj3bot agent --help`
- `prj3bot mail --help`
- `prj3bot gog --help`
- `prj3bot channels --help`
- `prj3bot provider --help`

All mail subcommands:

- `read [N]`
- `unread [N]`
- `show <index>`
- `reply <index> <text>`
- `send <to> | <subject> | <body>`
- `send <to> <body>`
- `send to <email> (<instruction>)`
- `help`
- `exit`

## All Telegram Commands

Telegram slash commands:

- `/start`
- `/new`
- `/stop`
- `/help`

Telegram mail command prefixes:

- `prj3bot mail read3`
- `prj3bot mail unread 5`
- `prj3bot mail show 1`
- `prj3bot mail reply 1 <text>`
- `prj3bot mail send <to> | <subject> | <body>`
- `prj3bot mail send <to> <body>`
- `prj3bot mail send to <email> (<instruction>)`
- `prj3bot mail help`
- `prj3bot mail exit`

Telegram Google Workspace prefixes:

- `prj3bot doc <instruction>`
- `prj3bot doc list [N]`
- `prj3bot doc read <index|id|url>`
- `prj3bot sheet <instruction>`
- `prj3bot sheet list [N]`
- `prj3bot sheet read <index|id|url> [A1:Z50]`
- `prj3bot sheet append <index|id|url> | value1 | value2 ...`
- `prj3bot calendar list [N]`
- `prj3bot calendar events [calendar|index|primary] [N]`
- `prj3bot calendar create <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `prj3bot calendar meet <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `prj3bot drive list [N]`
- `prj3bot drive search <query>`
- `prj3bot drive show <index|id|url>`
- `prj3bot meet create`
- `prj3bot meet show <spaceId|meetingCode|url>`
- `prj3bot slides <instruction>`
- `prj3bot slides list [N]`
- `prj3bot slides read <index|id|url>`
- `prj3bot classroom courses [N]`
- `prj3bot classroom coursework <courseId|index> [N]`
- `prj3bot classroom announcements <courseId|index> [N]`
- `prj3bot gog help`
- `prj3bot gog exit`

All gog subcommands:

- `doc <instruction>`
- `doc list [N]`
- `doc read <index|id|url>`
- `sheet <instruction>`
- `sheet list [N]`
- `sheet read <index|id|url> [A1:Z50]`
- `sheet append <index|id|url> | value1 | value2 ...`
- `calendar list [N]`
- `calendar events [calendar|index|primary] [N]`
- `calendar create <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `calendar meet <calendar|index|primary> | <title> | <start> | <end> [| description]`
- `drive list [N]`
- `drive search <query>`
- `drive show <index|id|url>`
- `meet create`
- `meet show <spaceId|meetingCode|url>`
- `slides <instruction>`
- `slides list [N]`
- `slides read <index|id|url>`
- `classroom courses [N]`
- `classroom coursework <courseId|index> [N]`
- `classroom announcements <courseId|index> [N]`
- `help`
- `exit`

## Student Use Cases

- Build a 3/7/30-day study plan from your syllabus.
- Summarize unread academic emails into action items.
- Draft formal emails to professors/TAs in seconds.
- Convert lecture notes into revision points and quiz questions.
- Generate assignment breakdown with deadlines and daily checkpoints.
- Use heartbeat tasks to run periodic "what should I study next" nudges.
- Ask Telegram bot to send AI-researched digest emails to classmates.

## Other Practical Use Cases

- Personal productivity assistant across Telegram + email.
- Team status summary drafting and auto-formatting.
- Scheduled reminders and recurring workflows (cron + heartbeat).
- Workspace-aware coding and debugging assistant.
- Research assistant with search + fetch + synthesis.

## Troubleshooting

- `allow_from is empty - all access denied`:
  - Set `allowFrom` for the channel (e.g., `["*"]` for testing).
- `Email config is incomplete`:
  - Fill all required email fields: IMAP + SMTP + `consentGranted`.
- SMTP invalid recipient error:
  - Use valid address format only (`user@example.com`).
- Telegram sends normal AI response instead of mail command:
  - Use prefix: `prj3bot mail ...`.
- Google OAuth error `403: access_denied` / "app not verified":
  - Open Google Cloud Console -> `APIs & Services` -> `OAuth consent screen`.
  - Keep publishing status as `Testing`.
  - Add your Gmail (`yufuy6618@gmail.com`) under `Test users`.
  - Save, wait 1-5 minutes, then retry login.
  - If needed, delete `~/.prj3bot/google/token.json` and retry `prj3bot gog -m "drive list 5"`.
- Google Calendar / Meet commands fail after working Google auth:
  - Delete `~/.prj3bot/google/token.json`.
  - Re-run `prj3bot gog -m "drive list 5"` once so the token gets the newer scopes.
- No Telegram response:
  - Verify bot token, `channels.telegram.enabled=true`, and `allowFrom`.

## Security Notes

- Never commit API keys or bot tokens to git.
- Use app passwords for Gmail, not your main account password.
- Restrict channel access using `allowFrom` to known users.
- Set `tools.restrictToWorkspace=true` if you need strict filesystem boundaries.

## Development and Tests

Run tests:

```powershell
python -m pytest
```

Run focused mail/telegram tests:

```powershell
python -m pytest tests/test_mail_command.py tests/test_telegram_mail_command.py
```

---

If you want, the next step can be adding a small `config.example.json` in the repo so setup becomes copy-paste in under 1 minute.
