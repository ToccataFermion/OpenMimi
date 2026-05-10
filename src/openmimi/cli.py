"""OpenMimi CLI entry point."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any

import typer

from .config import load_config

app = typer.Typer(help="OpenMimi - Local Windows AI Agent.")


def _polish_console_io() -> None:
    """Force UTF-8 stdout/stderr and silence noisy Windows asyncio teardown.

    PowerShell/cmd default codepages (cp936, cp1252, ...) mangle non-ASCII
    output, including the assistant's Chinese reply. We reconfigure both
    streams to UTF-8 with backslashreplace so unicode characters survive.

    On Windows + Python 3.11 + browser_use 0.12, killing the BrowserSession
    leaves asyncio `_ProactorBasePipeTransport` instances to be GC'd,
    which triggers `unclosed transport` / `I/O operation on closed pipe`
    messages from `__del__`. These are cosmetic; we suppress them so the
    agent's final answer is the last thing the user sees on screen.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass

    warnings.filterwarnings(
        "ignore",
        message=r".*unclosed transport.*",
        category=ResourceWarning,
    )

    _orig_unraisable = sys.unraisablehook

    def _quiet_proactor_unraisable(unraisable: Any) -> None:
        exc = unraisable.exc_value
        msg = str(exc) if exc is not None else ""
        if isinstance(exc, ValueError) and "closed pipe" in msg:
            return
        if isinstance(exc, ResourceWarning) and "unclosed transport" in msg:
            return
        _orig_unraisable(unraisable)

    sys.unraisablehook = _quiet_proactor_unraisable


@app.callback()
def _root_callback() -> None:
    """Pre-command setup that runs before every subcommand."""
    _polish_console_io()


def _apply_cli_screenshots(screenshots: bool) -> None:
    """Honor ``--screenshots`` after ``.env`` load so the flag wins for this run."""
    if screenshots:
        os.environ["OPENMIMI_ENABLE_SCREENSHOTS"] = "1"


def _screenshots_status() -> str:
    from .utils.env_flags import screenshots_disabled

    return "off" if screenshots_disabled() else "on"


def _handle_slash_command(line: str, messages: list[dict[str, Any]]) -> bool:
    """Process slash commands. Return True if the line was handled."""
    parts = line.split()
    cmd = parts[0].lower() if parts else ""

    if cmd == "/screenshots":
        if len(parts) >= 2:
            arg = parts[1].lower()
            if arg in ("on", "1", "true", "yes"):
                os.environ["OPENMIMI_ENABLE_SCREENSHOTS"] = "1"
                print("Screenshots enabled.")
            elif arg in ("off", "0", "false", "no"):
                os.environ.pop("OPENMIMI_ENABLE_SCREENSHOTS", None)
                print("Screenshots disabled.")
            else:
                print(f"Usage: /screenshots on|off  (currently {_screenshots_status()})")
        else:
            print(f"Screenshots: {_screenshots_status()}")
        return True

    if cmd == "/clear":
        messages.clear()
        print("Conversation history cleared.")
        return True

    if cmd == "/help":
        print(
            """Commands:
  /screenshots [on|off]  Toggle tool screenshots
  /clear                 Clear conversation history
  /exit, /quit           Exit
  /help                  Show this help"""
        )
        return True

    return False


@app.command()
def run(
    task: str = typer.Argument(..., help="The task to execute, in plain English."),
    screenshots: bool = typer.Option(
        False,
        "--screenshots",
        help="Enable tool screenshots for this run (same as OPENMIMI_ENABLE_SCREENSHOTS=1).",
    ),
) -> None:
    """Run a single task end-to-end."""
    _maybe_load_dotenv()
    _apply_cli_screenshots(screenshots)
    from .orchestrator import Orchestrator

    try:
        orch = Orchestrator.from_env()
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        result = asyncio.run(orch.run_task(task))
    except KeyboardInterrupt:
        typer.echo("\ninterrupted", err=True)
        raise typer.Exit(code=130) from None

    typer.echo(f"\n[session {result['session_id']}]")
    final = result.get("final_text") or "(no final text)"
    typer.echo(final)


def _read_chat_line(prompt: str) -> str:
    """Read one line of user input (separate for tests to monkeypatch)."""
    return input(prompt)


def _mimi_version() -> str:
    try:
        from importlib.metadata import version
        return version("openmimi")
    except Exception:
        return "0.0.1"


def _print_welcome(session_id: str) -> None:
    ver = _mimi_version()
    logo = f"""
   /\_/\     ╔══════════════════════════════════╗
  ( o.o )    ║  OpenMimi  v{ver}                ║
   > ^ <     ║  Local Windows AI Agent          ║
             ╚══════════════════════════════════╝
"""
    print(logo)
    print(f"  session : {session_id}")
    print(f"  tips    : 输入任务开始，/exit 或 Ctrl+C 退出")
    print()


def _announce_prewarm(orch: Any) -> None:
    """Print a one-line note when the browser daemon is still warming up.

    The actual warmup is fired eagerly in ``AgentBrowserTool.__init__``; this
    just surfaces it so the user understands why the first task may be slow
    on a cold Windows machine.
    """
    try:
        if orch.prewarm_browser():
            print("  browser : warming up in background (first task may be slow)")
    except Exception:
        pass


@app.command()
def chat(
    screenshots: bool = typer.Option(
        False,
        "--screenshots",
        help="Enable tool screenshots for this run (same as OPENMIMI_ENABLE_SCREENSHOTS=1).",
    ),
) -> None:
    """Multi-turn REPL with a single browser session and shared context."""
    _maybe_load_dotenv()
    _apply_cli_screenshots(screenshots)
    from .orchestrator import Orchestrator
    from .utils.ids import new_session_id

    try:
        orch = Orchestrator.from_env()
    except RuntimeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    async def _runner() -> None:
        session_id = new_session_id()
        messages: list[dict[str, Any]] = []
        _print_welcome(session_id)
        _announce_prewarm(orch)
        try:
            while True:
                try:
                    raw = await asyncio.to_thread(_read_chat_line, "> ")
                except EOFError:
                    break
                line = raw.strip()
                if not line:
                    continue
                lower = line.lower()
                if lower in ("/exit", "/quit", "exit", "quit"):
                    break
                if _handle_slash_command(line, messages):
                    continue
                try:
                    reply = await orch.run_chat_turn(
                        messages=messages,
                        session_id=session_id,
                        user_content=line,
                    )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    typer.echo(f"\n[error] {exc}", err=True)
                    continue
                typer.echo(f"\n{reply}\n")
        finally:
            try:
                await orch.save_chat_memory(messages)
            except Exception:
                pass
            await orch.close()

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        typer.echo("\ninterrupted", err=True)
        raise typer.Exit(code=130) from None


@app.command()
def replay(session_id: str) -> None:
    """Replay actions and screenshots for a previous session."""
    cfg = load_config()
    audit_path = Path(cfg.storage.audit_dir) / f"{session_id}.jsonl"
    if not audit_path.is_file():
        typer.echo(f"session not found: {audit_path}", err=True)
        raise typer.Exit(code=1)

    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            typer.echo(f"  ! malformed line: {line[:80]}")
            continue
        marker = "ERR" if rec.get("is_error") else "OK "
        tool_input_preview = json.dumps(
            rec.get("tool_input", {}), ensure_ascii=False
        )
        if len(tool_input_preview) > 80:
            tool_input_preview = tool_input_preview[:77] + "..."
        summary = (rec.get("result_summary") or "").splitlines()[0]
        if len(summary) > 80:
            summary = summary[:77] + "..."
        typer.echo(
            f"step {rec.get('step', '?'):>3} [{marker}] "
            f"{rec.get('tool', '?'):<10} "
            f"{rec.get('duration_ms', 0):>5}ms  "
            f"{tool_input_preview} -> {summary}"
        )
        if rec.get("error_code"):
            typer.echo(f"           code: {rec['error_code']}")
        if rec.get("image_path"):
            typer.echo(f"           image: {rec['image_path']}")


def _maybe_load_dotenv() -> None:
    """Best-effort load of .env from user config dir, then current directory.

    Precedence: ~/.openmimi/.env (global) < ./.env (local override).
    The dotenv dependency is already required by the project; if it's missing
    or the file does not exist we simply skip the call.
    """
    try:
        from pathlib import Path
        from dotenv import load_dotenv

        global_env = Path.home() / ".openmimi" / ".env"
        if global_env.exists():
            load_dotenv(dotenv_path=global_env, override=False)
        load_dotenv(override=True)
    except Exception:
        pass


def chat_main() -> None:
    """Short-cut entry point: `mimi` starts chat REPL directly."""
    _polish_console_io()
    _maybe_load_dotenv()
    from .orchestrator import Orchestrator

    try:
        orch = Orchestrator.from_env()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    from .utils.ids import new_session_id

    async def _runner() -> None:
        session_id = new_session_id()
        messages: list[dict[str, Any]] = []
        _print_welcome(session_id)
        _announce_prewarm(orch)
        try:
            while True:
                try:
                    raw = await asyncio.to_thread(_read_chat_line, "> ")
                except EOFError:
                    break
                line = raw.strip()
                if not line:
                    continue
                lower = line.lower()
                if lower in ("/exit", "/quit", "exit", "quit"):
                    break
                if _handle_slash_command(line, messages):
                    continue
                try:
                    reply = await orch.run_chat_turn(
                        messages=messages,
                        session_id=session_id,
                        user_content=line,
                    )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    print(f"\n[error] {exc}", file=sys.stderr)
                    continue
                print(f"\n{reply}\n")
        finally:
            try:
                await orch.save_chat_memory(messages)
            except Exception:
                pass
            await orch.close()

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    app()
