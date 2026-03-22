"""CLI commands for prj3bot."""

import asyncio
import os
import re
import select
import signal
import sys
from typing import Any
from email.utils import parseaddr
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from prj3bot import __logo__, __version__
from prj3bot.config.schema import Config
from prj3bot.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="prj3bot",
    help=f"{__logo__} prj3bot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".prj3bot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} prj3bot[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} prj3bot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """prj3bot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize prj3bot configuration and workspace."""
    from prj3bot.config.loader import get_config_path, load_config, save_config
    from prj3bot.config.schema import Config
    from prj3bot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    console.print(f"\n{__logo__} prj3bot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.prj3bot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]prj3bot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/prj3bot#-chat-apps[/dim]")





def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from prj3bot.providers.custom_provider import CustomProvider
    from prj3bot.providers.litellm_provider import LiteLLMProvider
    from prj3bot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    from prj3bot.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.prj3bot/config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the prj3bot gateway."""
    from prj3bot.agent.loop import AgentLoop
    from prj3bot.bus.queue import MessageBus
    from prj3bot.channels.manager import ChannelManager
    from prj3bot.config.loader import get_data_dir, load_config
    from prj3bot.cron.service import CronService
    from prj3bot.cron.types import CronJob
    from prj3bot.heartbeat.service import HeartbeatService
    from prj3bot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting prj3bot gateway on port {port}...")

    config = load_config()
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        google_workspace_config=config.google_workspace,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from prj3bot.agent.tools.cron import CronTool
        from prj3bot.agent.tools.message import MessageTool
        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            from prj3bot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response
            ))
        return response
    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from prj3bot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show prj3bot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from prj3bot.agent.loop import AgentLoop
    from prj3bot.bus.queue import MessageBus
    from prj3bot.config.loader import get_data_dir, load_config
    from prj3bot.cron.service import CronService

    config = load_config()
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("prj3bot")
    else:
        logger.disable("prj3bot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        google_workspace_config=config.google_workspace,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]prj3bot is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from prj3bot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                console.print(f"  [dim]↳ {msg.content}[/dim]")
                        elif not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            console.print()
                            _print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Mail Commands
# ============================================================================


def _mail_body_preview(content: str, max_len: int = 120) -> str:
    """Extract a short body preview from channel-normalized email content."""
    if "\n\n" in content:
        content = content.split("\n\n", 1)[1]
    compact = " ".join(content.strip().split())
    return compact if len(compact) <= max_len else compact[: max_len - 3] + "..."


def _mail_parse_send(raw: str) -> tuple[str, str, str] | None:
    """Parse send command.

    Supports:
    - send to@example.com | Subject | Body
    - send to@example.com Body text (subject defaults)
    """
    if not raw.lower().startswith("send "):
        return None
    payload = raw[5:].strip()
    if not payload:
        return None
    # Reserved for AI drafting command: "send to <email> (...)"
    if payload.lower().startswith("to "):
        return None

    if "|" in payload:
        parts = [p.strip() for p in payload.split("|", 2)]
        if len(parts) == 3 and all(parts):
            return parts[0], parts[1], parts[2]
        return None

    match = re.match(r"^(\S+)\s+(.+)$", payload)
    if not match:
        return None
    return match.group(1), "prj3bot message", match.group(2).strip()


def _mail_parse_ai_send(raw: str) -> tuple[str, str] | None:
    """Parse natural-language AI send command.

    Supports:
    - send to someone@example.com (instruction...)
    - send to someone@example.com: instruction...
    """
    cmd = raw.strip()
    m = re.match(r"^send\s+to\s+(\S+)\s*\((.+)\)\s*$", cmd, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^send\s+to\s+(\S+)\s*:\s*(.+)$", cmd, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Also allow: send to user@example.com instruction...
    m = re.match(r"^send\s+to\s+(\S+)\s+(.+)$", cmd, flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def _mail_is_valid_address(addr: str) -> bool:
    """Basic recipient validation to prevent accidental malformed SMTP targets."""
    parsed = parseaddr(addr)[1].strip()
    if not parsed:
        return False
    if parsed != addr.strip():
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", parsed))


def _mail_parse_ai_json(content: str) -> tuple[str, str] | None:
    """Parse AI output into (subject, body). Expects JSON with subject/body keys."""
    import json_repair

    text = (content or "").strip()
    if not text:
        return None

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    obj: Any | None = None
    try:
        obj = json_repair.loads(text)
    except Exception:
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                obj = json_repair.loads(text[first:last + 1])
            except Exception:
                obj = None

    if not isinstance(obj, dict):
        return None

    subject = str(obj.get("subject", "")).strip()
    body = str(obj.get("body", "")).strip()
    if not subject or not body:
        return None
    return subject, body


async def _mail_generate_ai_draft(config: Config, instruction: str) -> tuple[str, str] | str:
    """Generate email subject/body with configured LLM provider."""
    from prj3bot.providers.base import LLMResponse

    try:
        provider = _make_provider(config)
    except typer.Exit:
        return "AI drafting unavailable: LLM provider is not configured."

    response: LLMResponse = await provider.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate email drafts. Return strict JSON only with keys "
                    "\"subject\" and \"body\". No markdown fences. "
                    "Respect the user's formatting constraints exactly."
                ),
            },
            {"role": "user", "content": instruction},
        ],
        model=config.agents.defaults.model,
        max_tokens=min(max(config.agents.defaults.max_tokens, 512), 4096),
        temperature=0.2,
        reasoning_effort=config.agents.defaults.reasoning_effort,
    )

    if response.content and response.content.startswith("Error calling LLM:"):
        return f"AI drafting failed: {response.content}"

    parsed = _mail_parse_ai_json(response.content or "")
    if parsed:
        return parsed

    fallback = (response.content or "").strip()
    if not fallback:
        return "AI drafting failed: model returned empty content."
    return "prj3bot drafted email", fallback


def _validate_mail_config() -> tuple[Config, list[str]]:
    """Load config and return missing required email settings."""
    from prj3bot.config.loader import load_config

    config = load_config()
    em = config.channels.email

    missing: list[str] = []
    if not em.consent_granted:
        missing.append("channels.email.consentGranted=true")
    if not em.imap_host:
        missing.append("channels.email.imapHost")
    if not em.imap_username:
        missing.append("channels.email.imapUsername")
    if not em.imap_password:
        missing.append("channels.email.imapPassword")
    if not em.smtp_host:
        missing.append("channels.email.smtpHost")
    if not em.smtp_username:
        missing.append("channels.email.smtpUsername")
    if not em.smtp_password:
        missing.append("channels.email.smtpPassword")

    return config, missing


async def _mail_execute_command(
    email_channel,
    config: Config,
    raw_command: str,
    state: dict[str, list[dict]],
    default_limit: int,
) -> str:
    """Execute a single mail command and return a user-facing string."""
    from prj3bot.bus.events import OutboundMessage

    cmd = raw_command.strip()
    if not cmd:
        return ""

    low = cmd.lower()
    if low in {"help", "/help"}:
        return (
            "Mail commands:\n"
            "  read [N]         Read latest N emails (default from --limit)\n"
            "  unread [N]       Read latest unread emails\n"
            "  show <index>     Show full content of an email from latest read list\n"
            "  reply <index> <text>  Reply to an email from latest read list\n"
            "  send <to> | <subject> | <body>  Send a new email\n"
            "  send <to> <body>  Send with default subject\n"
            "  send to <email> (<instruction>)  AI drafts subject/body and sends\n"
            "  exit             Quit mail mode"
        )

    if low in {"exit", "quit", "/exit", "/quit"}:
        return "__EXIT__"

    if low.startswith(("read", "inbox", "list", "unread")):
        unread_only = low.startswith("unread")
        count = default_limit
        match = re.search(r"\b(\d+)\b", cmd)
        if match:
            count = max(1, int(match.group(1)))
        else:
            compact_match = re.match(r"^(?:read|inbox|list|unread)\s*(\d+)$", low)
            if compact_match:
                count = max(1, int(compact_match.group(1)))

        items = email_channel.fetch_recent_messages(limit=count, unread_only=unread_only, mark_seen=False)
        items = list(reversed(items))  # latest first
        state["last_messages"] = items

        if not items:
            return "No emails found."

        lines = [f"Found {len(items)} email(s):"]
        for idx, item in enumerate(items, 1):
            sender = item.get("sender", "(unknown sender)")
            subject = item.get("subject") or "(no subject)"
            date_value = item.get("metadata", {}).get("date", "")
            preview = _mail_body_preview(item.get("content", ""))
            lines.append(f"{idx}. From: {sender} | Subject: {subject} | Date: {date_value}")
            lines.append(f"   {preview}")
        return "\n".join(lines)

    show_match = re.match(r"^(show|open)\s+(\d+)$", low)
    if show_match:
        index = int(show_match.group(2))
        items = state.get("last_messages", [])
        if not items:
            return "No email list in memory. Run `read` first."
        if index < 1 or index > len(items):
            return f"Invalid index: {index}. Run `read` to see valid indices."
        item = items[index - 1]
        sender = item.get("sender", "(unknown sender)")
        subject = item.get("subject") or "(no subject)"
        return (
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"{item.get('content', '')}"
        )

    reply_match = re.match(r"^reply\s+(\d+)\s+(.+)$", cmd, flags=re.IGNORECASE | re.DOTALL)
    if reply_match:
        index = int(reply_match.group(1))
        reply_text = reply_match.group(2).strip()
        items = state.get("last_messages", [])
        if not items:
            return "No email list in memory. Run `read` first."
        if index < 1 or index > len(items):
            return f"Invalid index: {index}. Run `read` to see valid indices."
        if not reply_text:
            return "Reply text is empty."

        target = items[index - 1]
        to_addr = target.get("sender", "").strip()
        if not to_addr:
            return "Target email address missing."
        if not _mail_is_valid_address(to_addr):
            return f"Invalid recipient address in selected email: {to_addr}"

        subject = target.get("subject", "")
        message_id = target.get("message_id", "")
        if subject:
            email_channel._last_subject_by_chat[to_addr] = subject
        if message_id:
            email_channel._last_message_id_by_chat[to_addr] = message_id

        try:
            await email_channel.send(
                OutboundMessage(
                    channel="email",
                    chat_id=to_addr,
                    content=reply_text,
                    metadata={"force_send": True},
                )
            )
        except Exception as e:
            return f"Failed to send email to {to_addr}: {e}"
        return f"Reply sent to {to_addr}"

    if low.startswith("send to "):
        ai_send = _mail_parse_ai_send(cmd)
        if not ai_send:
            return "Invalid format. Use: send to <email> (<instruction>)"
        to_addr, instruction = ai_send
        if not _mail_is_valid_address(to_addr):
            return f"Invalid recipient email address: {to_addr}"
        if not instruction:
            return "Instruction is empty. Example: send to user@example.com (summarize today's top 5 AI news)"
        draft = await _mail_generate_ai_draft(config, instruction)
        if isinstance(draft, str):
            return draft
        subject, body = draft
        try:
            await email_channel.send(
                OutboundMessage(
                    channel="email",
                    chat_id=to_addr,
                    content=body,
                    metadata={"subject": subject, "force_send": True},
                )
            )
        except Exception as e:
            return f"Failed to send email to {to_addr}: {e}"
        return f"AI email sent to {to_addr}\nSubject: {subject}"

    send_parts = _mail_parse_send(cmd)
    if send_parts:
        to_addr, subject, body = send_parts
        if not _mail_is_valid_address(to_addr):
            return f"Invalid recipient email address: {to_addr}"
        try:
            await email_channel.send(
                OutboundMessage(
                    channel="email",
                    chat_id=to_addr,
                    content=body,
                    metadata={"subject": subject, "force_send": True},
                )
            )
        except Exception as e:
            return f"Failed to send email to {to_addr}: {e}"
        return f"Email sent to {to_addr}"

    return "Unknown command. Type `help` for supported mail commands."


@app.command()
def mail(
    message: str = typer.Option(None, "--message", "-m", help="Mail command to run once"),
    limit: int = typer.Option(10, "--limit", "-n", help="Default read count"),
):
    """Email-focused CLI mode for reading and replying to mail."""
    from prj3bot.bus.queue import MessageBus
    from prj3bot.channels.email import EmailChannel

    config, missing = _validate_mail_config()
    if missing:
        console.print("[red]Email config is incomplete:[/red]")
        for item in missing:
            console.print(f"  - {item}")
        raise typer.Exit(1)

    email_channel = EmailChannel(config.channels.email, MessageBus())
    state: dict[str, list[dict]] = {"last_messages": []}

    if message:
        try:
            result = asyncio.run(_mail_execute_command(email_channel, config, message, state, max(1, limit)))
        except Exception as e:
            console.print(f"[red]Mail command failed:[/red] {e}")
            raise typer.Exit(1)
        if result and result != "__EXIT__":
            console.print(result)
        return

    console.print(f"{__logo__} Mail mode (type [bold]help[/bold] for commands, [bold]exit[/bold] to quit)\n")
    while True:
        try:
            raw = typer.prompt("Mail")
        except (KeyboardInterrupt, EOFError):
            console.print("\nGoodbye!")
            break

        try:
            result = asyncio.run(_mail_execute_command(email_channel, config, raw, state, max(1, limit)))
        except Exception as e:
            console.print(f"[red]Mail command failed:[/red] {e}")
            continue
        if result == "__EXIT__":
            console.print("Goodbye!")
            break
        if result:
            console.print(result)


# ============================================================================
# Google Workspace Commands (gog)
# ============================================================================


def _gog_extract_count(raw: str, default_limit: int) -> int:
    """Extract numeric count from command text, supporting compact forms like list5."""
    count = default_limit
    match = re.search(r"\b(\d+)\b", raw)
    if match:
        count = max(1, int(match.group(1)))
    else:
        compact = re.match(r"^[a-zA-Z]+(\d+)$", raw.strip())
        if compact:
            count = max(1, int(compact.group(1)))
    return count


def _gog_preview_table(values: list[list[str]], max_rows: int = 8, max_cols: int = 6) -> str:
    """Render sheet values into a compact plain-text preview."""
    if not values:
        return "(empty)"
    lines: list[str] = []
    for row in values[:max_rows]:
        cells = [str(c) for c in row[:max_cols]]
        lines.append(" | ".join(cells))
    if len(values) > max_rows:
        lines.append("...")
    return "\n".join(lines)


def _gog_parse_json_object(content: str) -> dict[str, Any] | None:
    """Best-effort parse for LLM JSON object responses."""
    import json_repair

    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json_repair.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            obj = json_repair.loads(text[first:last + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
    return None


async def _gog_generate_doc_draft(config: Config, instruction: str) -> tuple[str, str] | str:
    """Use configured LLM to generate document title and body."""
    from prj3bot.providers.base import LLMResponse

    try:
        provider = _make_provider(config)
    except typer.Exit:
        return "Google doc drafting unavailable: LLM provider is not configured."

    response: LLMResponse = await provider.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate long-form document drafts. Return strict JSON only with keys "
                    "\"title\" and \"body\". No markdown fences."
                ),
            },
            {"role": "user", "content": instruction},
        ],
        model=config.agents.defaults.model,
        max_tokens=min(max(config.agents.defaults.max_tokens, 1024), 8192),
        temperature=0.2,
        reasoning_effort=config.agents.defaults.reasoning_effort,
    )
    if response.content and response.content.startswith("Error calling LLM:"):
        return f"Draft generation failed: {response.content}"

    obj = _gog_parse_json_object(response.content or "")
    if obj:
        title = str(obj.get("title", "")).strip()
        body = str(obj.get("body", "")).strip()
        if title and body:
            return title, body

    fallback = (response.content or "").strip()
    if not fallback:
        return "Draft generation failed: model returned empty content."
    return "prj3bot document", fallback


async def _gog_generate_sheet_draft(config: Config, instruction: str) -> tuple[str, list[list[str]]] | str:
    """Use configured LLM to generate a sheet title and tabular values."""
    from prj3bot.providers.base import LLMResponse

    try:
        provider = _make_provider(config)
    except typer.Exit:
        return "Google Sheets drafting unavailable: LLM provider is not configured."

    response: LLMResponse = await provider.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate spreadsheet content. Return strict JSON only with keys: "
                    "\"title\" (string), \"headers\" (array of strings), \"rows\" (array of array of strings). "
                    "No markdown fences."
                ),
            },
            {"role": "user", "content": instruction},
        ],
        model=config.agents.defaults.model,
        max_tokens=min(max(config.agents.defaults.max_tokens, 768), 4096),
        temperature=0.2,
        reasoning_effort=config.agents.defaults.reasoning_effort,
    )
    if response.content and response.content.startswith("Error calling LLM:"):
        return f"Sheet drafting failed: {response.content}"

    obj = _gog_parse_json_object(response.content or "")
    if obj:
        title = str(obj.get("title", "")).strip() or "prj3bot sheet"
        headers = [str(x).strip() for x in (obj.get("headers") or []) if str(x).strip()]
        rows_raw = obj.get("rows") or []
        rows: list[list[str]] = []
        if isinstance(rows_raw, list):
            for row in rows_raw:
                if isinstance(row, list):
                    rows.append([str(cell) for cell in row])
        values: list[list[str]] = []
        if headers:
            values.append(headers)
        values.extend(rows)
        if values:
            return title, values

    fallback = (response.content or "").strip()
    if not fallback:
        return "Sheet drafting failed: model returned empty content."
    lines = [line.strip("- ").strip() for line in fallback.splitlines() if line.strip()]
    values = [["Content"]]
    values.extend([[line] for line in lines[:100]])
    return "prj3bot sheet", values


async def _gog_generate_slides_draft(
    config: Config,
    instruction: str,
) -> tuple[str, list[dict[str, str]]] | str:
    """Use configured LLM to generate a slide deck title and slide content."""
    from prj3bot.providers.base import LLMResponse

    try:
        provider = _make_provider(config)
    except typer.Exit:
        return "Google Slides drafting unavailable: LLM provider is not configured."

    response: LLMResponse = await provider.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate Google Slides deck outlines. Return strict JSON only with keys: "
                    "\"title\" (string) and \"slides\" (array of objects). Each slide object must have "
                    "\"title\" (string) and \"body\" (string). The body should contain meaningful content, "
                    "usually 3 to 6 concise bullet points separated by newlines and starting with '- '. "
                    "Create between 3 and 10 slides unless the user clearly asks otherwise. No markdown fences."
                ),
            },
            {"role": "user", "content": instruction},
        ],
        model=config.agents.defaults.model,
        max_tokens=min(max(config.agents.defaults.max_tokens, 1200), 4096),
        temperature=0.2,
        reasoning_effort=config.agents.defaults.reasoning_effort,
    )
    if response.content and response.content.startswith("Error calling LLM:"):
        return f"Slides drafting failed: {response.content}"

    obj = _gog_parse_json_object(response.content or "")
    if obj:
        title = str(obj.get("title", "")).strip() or "prj3bot slides"
        slides_raw = obj.get("slides") or []
        slides_content: list[dict[str, str]] = []
        if isinstance(slides_raw, list):
            for slide in slides_raw[:20]:
                if not isinstance(slide, dict):
                    continue
                slide_title = str(slide.get("title", "")).strip()
                slide_body = str(slide.get("body", "")).strip()
                if slide_title and slide_body:
                    slides_content.append({"title": slide_title, "body": slide_body})
        if slides_content:
            return title, slides_content

    fallback = (response.content or "").strip()
    if not fallback:
        return "Slides drafting failed: model returned empty content."
    fallback_title = instruction[:80].strip() or "prj3bot slides"
    return fallback_title, [
        {
            "title": fallback_title,
            "body": fallback[:3000],
        }
    ]


def _validate_gog_config() -> tuple[Config, list[str]]:
    """Load config and return missing required Google Workspace settings."""
    from prj3bot.config.loader import load_config

    config = load_config()
    gw = config.google_workspace
    missing: list[str] = []

    if not gw.enabled:
        missing.append("googleWorkspace.enabled=true")

    env_json = os.getenv("PRJ3BOT_GOOGLE_CREDENTIALS_JSON", "").strip()
    credentials_path = Path(gw.credentials_path).expanduser() if gw.credentials_path else None
    has_credential_source = bool(
        gw.credentials_json.strip()
        or env_json
        or (credentials_path and credentials_path.exists())
    )
    if not has_credential_source:
        missing.append(
            "googleWorkspace.credentialsJson or a file at googleWorkspace.credentialsPath"
        )
    if not gw.token_path:
        missing.append("googleWorkspace.tokenPath")

    return config, missing


def _gog_resolve_ref(raw: str, items: list[dict[str, Any]]) -> str:
    """Resolve an argument to Google resource ID/URL, supporting list index references."""
    arg = raw.strip()
    if not arg:
        return ""
    if arg.isdigit() and items:
        index = int(arg)
        if 1 <= index <= len(items):
            item = items[index - 1]
            return str(item.get("id") or item.get("url") or arg)
    return arg


def _gog_resolve_course_id(raw: str, courses: list[dict[str, Any]]) -> str:
    """Resolve course ID from raw argument (direct ID or index from latest list)."""
    arg = raw.strip()
    if not arg:
        return ""
    if arg.isdigit() and courses:
        index = int(arg)
        if 1 <= index <= len(courses):
            return str(courses[index - 1].get("id", "")).strip()
    return arg


async def _gog_execute_command(
    config: Config,
    raw_command: str,
    state: dict[str, Any],
    default_limit: int,
) -> str:
    """Execute one Google Workspace command and return user-facing output."""
    from prj3bot.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError

    cmd = raw_command.strip()
    if not cmd:
        return ""

    low = cmd.lower()
    if low in {"help", "/help"}:
        return (
            "Google Workspace commands:\n"
            "  doc <instruction>                     Create a Google Doc from AI instruction\n"
            "  doc list [N]                          List latest docs\n"
            "  doc read <index|id|url>               Read doc content\n"
            "  sheet <instruction>                   Create a Google Sheet from AI instruction\n"
            "  sheet list [N]                        List latest sheets\n"
            "  sheet read <index|id|url> [A1:Z50]    Read cells\n"
            "  sheet append <index|id|url> | v1 | v2 Append one row\n"
            "  calendar list [N]                     List Google Calendars\n"
            "  calendar events [calendar|idx] [N]    List upcoming events\n"
            "  calendar create <cal> | <title> | <start> | <end> [| description]\n"
            "  calendar meet <cal> | <title> | <start> | <end> [| description]\n"
            "  drive list [N]                        List recent drive files\n"
            "  drive search <query>                  Search drive files\n"
            "  drive show <index|id|url>             Show one file metadata\n"
            "  meet create                           Create a Google Meet space\n"
            "  meet show <spaceId|meetingCode|url>   Show one Meet space\n"
            "  slides <instruction>                  Create Google Slides deck\n"
            "  slides list [N]                       List latest slide decks\n"
            "  slides read <index|id|url>            Show deck metadata\n"
            "  classroom courses [N]                 List classroom courses\n"
            "  classroom coursework <courseId|idx> [N]\n"
            "  classroom announcements <courseId|idx> [N]\n"
            "  exit                                  Quit gog mode"
        )

    if low in {"exit", "quit", "/exit", "/quit"}:
        return "__EXIT__"

    parts = cmd.split(None, 1)
    head = parts[0].lower()
    tail = parts[1].strip() if len(parts) > 1 else ""
    aliases = {"docs": "doc", "sheets": "sheet", "calendars": "calendar"}
    section = aliases.get(head, head)

    if section not in {"doc", "sheet", "calendar", "drive", "meet", "slides", "classroom"}:
        return "Unknown gog command. Type `help`."

    client = GoogleWorkspaceClient.from_config(config.google_workspace)

    try:
        if section == "doc":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Doc commands:\n"
                    "  doc <instruction>\n"
                    "  doc create <instruction>\n"
                    "  doc list [N]\n"
                    "  doc read <index|id|url>"
                )

            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("list"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.docs_list, count)
                state["doc_last"] = items
                if not items:
                    return "No Google Docs found."
                lines = [f"Found {len(items)} doc(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action in {"read", "show"}:
                if not rest:
                    return "Usage: doc read <index|id|url>"
                ref = _gog_resolve_ref(rest, state.get("doc_last", []))
                item = await asyncio.to_thread(client.docs_read, ref)
                text = item.get("text", "")
                if len(text) > 3000:
                    text = text[:2997] + "..."
                return f"Title: {item.get('title')}\nURL: {item.get('url')}\n\n{text}"

            instruction = rest if action in {"create", "do", "make"} else tail
            if not instruction:
                return "Instruction is empty. Example: doc do create a 200 word article on transformers."
            if "|" in instruction:
                p = [x.strip() for x in instruction.split("|", 1)]
                title = p[0] or "prj3bot document"
                body = p[1] if len(p) > 1 else ""
            else:
                draft = await _gog_generate_doc_draft(config, instruction)
                if isinstance(draft, str):
                    return draft
                title, body = draft
            created = await asyncio.to_thread(client.docs_create, title, body)
            return f"Google Doc created: {created['title']}\n{created['url']}"

        if section == "sheet":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Sheet commands:\n"
                    "  sheet <instruction>\n"
                    "  sheet create <instruction>\n"
                    "  sheet list [N]\n"
                    "  sheet read <index|id|url> [A1:Z50]\n"
                    "  sheet append <index|id|url> | v1 | v2 ..."
                )

            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("list"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.sheets_list, count)
                state["sheet_last"] = items
                if not items:
                    return "No Google Sheets found."
                lines = [f"Found {len(items)} sheet(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action in {"read", "show"}:
                if not rest:
                    return "Usage: sheet read <index|id|url> [A1:Z50]"
                m = re.match(r"^(\S+)(?:\s+(\S+))?$", rest)
                if not m:
                    return "Usage: sheet read <index|id|url> [A1:Z50]"
                ref = _gog_resolve_ref(m.group(1), state.get("sheet_last", []))
                cell_range = m.group(2) or "A1:Z50"
                item = await asyncio.to_thread(client.sheets_read, ref, cell_range)
                table = _gog_preview_table(item.get("values", []))
                return (
                    f"Title: {item.get('title')}\n"
                    f"URL: {item.get('url')}\n"
                    f"Range: {item.get('range')}\n\n"
                    f"{table}"
                )

            if action == "append":
                if "|" not in rest:
                    return "Usage: sheet append <index|id|url> | value1 | value2 ..."
                segs = [s.strip() for s in rest.split("|")]
                left = segs[0]
                values = [s for s in segs[1:] if s]
                if not left or not values:
                    return "Usage: sheet append <index|id|url> | value1 | value2 ..."
                ref = _gog_resolve_ref(left, state.get("sheet_last", []))
                await asyncio.to_thread(client.sheets_append, ref, values)
                return "Row appended to sheet."

            instruction = rest if action in {"create", "do", "make"} else tail
            if not instruction:
                return "Instruction is empty. Example: sheet do create a weekly study tracker."
            draft = await _gog_generate_sheet_draft(config, instruction)
            if isinstance(draft, str):
                return draft
            title, values = draft
            created = await asyncio.to_thread(client.sheets_create, title, values)
            preview = _gog_preview_table(values)
            return (
                f"Google Sheet created: {created['title']}\n"
                f"{created['url']}\n\n"
                f"Preview:\n{preview}"
            )

        if section == "drive":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Drive commands:\n"
                    "  drive list [N]\n"
                    "  drive search <query>\n"
                    "  drive show <index|id|url>"
                )
            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("list"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.drive_list, count)
                state["drive_last"] = items
                if not items:
                    return "No Drive files found."
                lines = [f"Found {len(items)} file(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('mimeType', '')}\n"
                        f"   {item.get('webViewLink', '')}"
                    )
                return "\n".join(lines)

            if action == "search":
                if not rest:
                    return "Usage: drive search <query>"
                items = await asyncio.to_thread(client.drive_search, rest, default_limit)
                state["drive_last"] = items
                if not items:
                    return "No matching Drive files found."
                lines = [f"Found {len(items)} matching file(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('mimeType', '')}\n"
                        f"   {item.get('webViewLink', '')}"
                    )
                return "\n".join(lines)

            if action in {"show", "read"}:
                if not rest:
                    return "Usage: drive show <index|id|url>"
                ref = _gog_resolve_ref(rest, state.get("drive_last", []))
                item = await asyncio.to_thread(client.drive_get, ref)
                return (
                    f"Name: {item.get('name')}\n"
                    f"Type: {item.get('mimeType')}\n"
                    f"Modified: {item.get('modifiedTime')}\n"
                    f"URL: {item.get('webViewLink')}"
                )

            return "Unknown drive command. Type `help`."

        if section == "calendar":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Calendar commands:\n"
                    "  calendar list [N]\n"
                    "  calendar events [calendar|index|primary] [N]\n"
                    "  calendar create <calendar|index|primary> | <title> | <start> | <end> [| description]\n"
                    "  calendar meet <calendar|index|primary> | <title> | <start> | <end> [| description]"
                )
            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("list"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.calendar_list, count)
                state["calendar_last"] = items
                if not items:
                    return "No Google Calendars found."
                lines = [f"Found {len(items)} calendar(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('summary', '(untitled)')} | id: {item.get('id', '')} | "
                        f"primary: {item.get('primary', 'no')}"
                    )
                return "\n".join(lines)

            if action in {"events", "event", "upcoming"}:
                tokens = rest.split()
                calendar_ref = "primary"
                count = default_limit
                if len(tokens) == 1:
                    if tokens[0].isdigit() and state.get("calendar_last"):
                        calendar_ref = _gog_resolve_ref(tokens[0], state.get("calendar_last", []))
                    elif tokens[0].isdigit():
                        count = int(tokens[0])
                    else:
                        calendar_ref = _gog_resolve_ref(tokens[0], state.get("calendar_last", []))
                elif len(tokens) >= 2:
                    calendar_ref = _gog_resolve_ref(tokens[0], state.get("calendar_last", []))
                    if tokens[1].isdigit():
                        count = int(tokens[1])
                items = await asyncio.to_thread(client.calendar_events, calendar_ref, count)
                state["calendar_events_last"] = items
                if not items:
                    return "No upcoming events found."
                lines = [f"Found {len(items)} event(s):"]
                for idx, item in enumerate(items, 1):
                    start = (item.get("start", {}) or {}).get("dateTime") or (item.get("start", {}) or {}).get("date", "")
                    lines.append(
                        f"{idx}. {item.get('summary', '(untitled)')} | {start}\n"
                        f"   {item.get('htmlLink', '')}"
                    )
                return "\n".join(lines)

            if action in {"create", "add", "schedule", "meet", "create-meet"}:
                segs = [seg.strip() for seg in rest.split("|")]
                if len(segs) < 4:
                    return (
                        "Usage: calendar create <calendar|index|primary> | <title> | <start> | <end> [| description]"
                    )
                calendar_ref = _gog_resolve_ref(segs[0], state.get("calendar_last", [])) or "primary"
                title = segs[1]
                start_at = segs[2]
                end_at = segs[3]
                description = segs[4] if len(segs) > 4 else ""
                create_meet_link = action in {"meet", "create-meet"}
                created = await asyncio.to_thread(
                    client.calendar_create_event,
                    calendar_ref,
                    title,
                    start_at,
                    end_at,
                    description,
                    create_meet_link,
                )
                lines = [
                    f"Calendar event created: {created['summary']}",
                    created["url"],
                    f"Start: {created['start']}",
                    f"End: {created['end']}",
                ]
                if created.get("meetLink"):
                    lines.append(f"Meet: {created['meetLink']}")
                return "\n".join(lines)

            return "Unknown calendar command. Type `help`."

        if section == "meet":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Meet commands:\n"
                    "  meet create\n"
                    "  meet show <spaceId|meetingCode|url>"
                )
            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action in {"create", "new"}:
                item = await asyncio.to_thread(client.meet_create)
                state["meet_last"] = [item]
                return (
                    f"Google Meet created:\n"
                    f"Space: {item.get('name', '')}\n"
                    f"Code: {item.get('meetingCode', '')}\n"
                    f"URL: {item.get('meetingUri', '')}"
                )

            if action in {"show", "read", "get"}:
                if not rest:
                    return "Usage: meet show <spaceId|meetingCode|url>"
                ref = _gog_resolve_ref(rest, state.get("meet_last", []))
                item = await asyncio.to_thread(client.meet_get, ref)
                return (
                    f"Space: {item.get('name', '')}\n"
                    f"Code: {item.get('meetingCode', '')}\n"
                    f"URL: {item.get('meetingUri', '')}"
                )

            return "Unknown meet command. Type `help`."

        if section == "slides":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Slides commands:\n"
                    "  slides <instruction>\n"
                    "  slides create <instruction>\n"
                    "  slides list [N]\n"
                    "  slides read <index|id|url>"
                )
            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("list"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.slides_list, count)
                state["slides_last"] = items
                if not items:
                    return "No Slides decks found."
                lines = [f"Found {len(items)} deck(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(untitled)')} | {item.get('modified', '')}\n"
                        f"   {item.get('url', '')}"
                    )
                return "\n".join(lines)

            if action in {"read", "show"}:
                if not rest:
                    return "Usage: slides read <index|id|url>"
                ref = _gog_resolve_ref(rest, state.get("slides_last", []))
                item = await asyncio.to_thread(client.slides_get, ref)
                return (
                    f"Title: {item.get('title')}\n"
                    f"Slides: {item.get('slides')}\n"
                    f"URL: {item.get('url')}"
                )

            instruction = rest if action in {"create", "do", "make"} else tail
            if not instruction:
                return "Instruction is empty. Example: slides do make a 5-slide deck on transformers."
            draft = await _gog_generate_slides_draft(config, instruction)
            if isinstance(draft, str):
                return draft
            title, slides_content = draft
            created = await asyncio.to_thread(
                client.slides_create,
                title,
                max(1, len(slides_content)),
                slides_content,
            )
            return (
                f"Google Slides created: {created['title']}\n"
                f"{created['url']}\n"
                f"Slides created: {created.get('slides', len(slides_content))}"
            )

        if section == "classroom":
            if not tail or tail.lower() in {"help", "/help"}:
                return (
                    "Classroom commands:\n"
                    "  classroom courses [N]\n"
                    "  classroom coursework <courseId|index> [N]\n"
                    "  classroom announcements <courseId|index> [N]"
                )

            sub_parts = tail.split(None, 1)
            action = sub_parts[0].lower()
            rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if action.startswith("courses"):
                count = _gog_extract_count(action + " " + rest, default_limit)
                items = await asyncio.to_thread(client.classroom_courses, count)
                state["classroom_courses"] = items
                if not items:
                    return "No Classroom courses found."
                lines = [f"Found {len(items)} course(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('name', '(unnamed)')} | id: {item.get('id', '')}"
                    )
                return "\n".join(lines)

            if action in {"coursework", "assignments"}:
                m = re.match(r"^(\S+)(?:\s+(\d+))?$", rest)
                if not m:
                    return "Usage: classroom coursework <courseId|index> [N]"
                course_id = _gog_resolve_course_id(m.group(1), state.get("classroom_courses", []))
                if not course_id:
                    return "Course ID is missing. Run `classroom courses` first."
                count = int(m.group(2)) if m.group(2) else default_limit
                items = await asyncio.to_thread(client.classroom_coursework, course_id, count)
                state["classroom_coursework"] = items
                if not items:
                    return "No coursework found for this course."
                lines = [f"Found {len(items)} coursework item(s):"]
                for idx, item in enumerate(items, 1):
                    lines.append(
                        f"{idx}. {item.get('title', '(untitled)')} | due: {item.get('dueDate', '')}"
                    )
                return "\n".join(lines)

            if action in {"announcements", "announce"}:
                m = re.match(r"^(\S+)(?:\s+(\d+))?$", rest)
                if not m:
                    return "Usage: classroom announcements <courseId|index> [N]"
                course_id = _gog_resolve_course_id(m.group(1), state.get("classroom_courses", []))
                if not course_id:
                    return "Course ID is missing. Run `classroom courses` first."
                count = int(m.group(2)) if m.group(2) else default_limit
                items = await asyncio.to_thread(client.classroom_announcements, course_id, count)
                state["classroom_announcements"] = items
                if not items:
                    return "No announcements found for this course."
                lines = [f"Found {len(items)} announcement(s):"]
                for idx, item in enumerate(items, 1):
                    txt = (item.get("text", "") or "").replace("\n", " ").strip()
                    if len(txt) > 100:
                        txt = txt[:97] + "..."
                    lines.append(f"{idx}. {txt or '(no text)'}")
                return "\n".join(lines)

            return "Unknown classroom command. Type `help`."

    except GoogleWorkspaceError as e:
        return f"Google Workspace error: {e}"
    except Exception as e:
        return f"Google command failed: {e}"

    return "Unknown gog command. Type `help`."


@app.command()
def gog(
    message: str = typer.Option(None, "--message", "-m", help="Google command to run once"),
    limit: int = typer.Option(10, "--limit", "-n", help="Default list count"),
):
    """Google Workspace command mode (Docs, Sheets, Calendar, Drive, Meet, Classroom, Slides)."""
    config, missing = _validate_gog_config()
    if missing:
        console.print("[red]Google Workspace config is incomplete:[/red]")
        for item in missing:
            console.print(f"  - {item}")
        raise typer.Exit(1)

    state: dict[str, Any] = {
        "doc_last": [],
        "sheet_last": [],
        "calendar_last": [],
        "calendar_events_last": [],
        "drive_last": [],
        "meet_last": [],
        "slides_last": [],
        "classroom_courses": [],
        "classroom_coursework": [],
        "classroom_announcements": [],
    }

    if message:
        try:
            result = asyncio.run(_gog_execute_command(config, message, state, max(1, limit)))
        except Exception as e:
            console.print(f"[red]Google command failed:[/red] {e}")
            raise typer.Exit(1)
        if result and result != "__EXIT__":
            console.print(result)
        return

    console.print(f"{__logo__} Google mode (type [bold]help[/bold], [bold]exit[/bold] to quit)\n")
    while True:
        try:
            raw = typer.prompt("Google")
        except (KeyboardInterrupt, EOFError):
            console.print("\nGoodbye!")
            break

        try:
            result = asyncio.run(_gog_execute_command(config, raw, state, max(1, limit)))
        except Exception as e:
            console.print(f"[red]Google command failed:[/red] {e}")
            continue
        if result == "__EXIT__":
            console.print("Goodbye!")
            break
        if result:
            console.print(result)


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from prj3bot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "Yes" if wa.enabled else "No",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "Yes" if dc.enabled else "No",
        dc.gateway_url
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "Yes" if tg.enabled else "No",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "Yes" if slack.enabled else "No",
        slack_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "Yes" if em.enabled else "No",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = Path.home() / ".prj3bot" / "bridge"

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # prj3bot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall prj3bot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from prj3bot.config.loader import load_config

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show prj3bot status."""
    from prj3bot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} prj3bot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from prj3bot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from prj3bot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()

