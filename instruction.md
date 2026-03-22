# Prj3bot: What You Can Do

This project is a lightweight personal AI assistant you can run from CLI or connect to chat apps.

## 1. Chat With Your AI Assistant
- Start interactive chat: `prj3bot agent`
- Ask one-shot questions: `prj3bot agent -m "Explain transformers in simple terms"`
- Use it for coding, writing, debugging, planning, and Q&A.

## 2. Use Different LLM Providers
- Switch between providers like Gemini, OpenRouter, OpenAI, Anthropic, DeepSeek, Groq, etc.
- Configure provider keys in `~/.prj3bot/config.json`.
- Set default model/provider in config for daily use.

## 3. Connect Chat Platforms
- Run as a gateway bot in apps like Telegram, Discord, WhatsApp, Slack, Matrix, Mochat, and Email.
- Start gateway: `prj3bot gateway`
- Manage channel auth/status: `prj3bot channels login`, `prj3bot channels status`

## 4. Automate Periodic Tasks
- Add recurring tasks in `~/.prj3bot/workspace/HEARTBEAT.md`.
- Example task: daily weather summary
- Example task: inbox scan for urgent mails
- Example task: market/watchlist update

## 5. Use Built-In Agent Tools
- Shell execution
- File read/write/edit and workspace operations
- Web search and browsing support
- Cron/task tools
- Message tools for channel replies

## 6. Extend With MCP Servers
- Add MCP servers in config under `tools.mcpServers`.
- Use local `command + args` servers or remote `url` servers.
- Let the agent call MCP tools as native capabilities.

## 7. Keep Long-Term Context
- Store persistent notes/memory in workspace memory files.
- Build personal/project context over time for better responses.

## 8. Run Securely
- Restrict tool access to workspace: set `"tools": { "restrictToWorkspace": true }` in config.
- Allow only specific users in channels with `allowFrom`.

## 9. Check Health and Setup
- Initialize files once: `prj3bot onboard`
- Check runtime/provider status: `prj3bot status`
- Confirm model and key wiring before production use.

## 10. Typical Real-World Uses
- Personal coding copilot
- Daily planner and reminder assistant
- Multi-platform personal bot
- Research helper with tool use
- Always-on assistant via gateway + heartbeat tasks

