"""OpenMimi CLI entry point."""
from __future__ import annotations

import asyncio
import json
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


@app.command()
def run(
    task: str = typer.Argument(..., help="The task to execute, in plain English."),
) -> None:
    """Run a single task end-to-end."""
    _maybe_load_dotenv()
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
    """Best-effort load of .env in the current working directory.

    The dotenv dependency is already required by the project; if it's missing
    or the file does not exist we simply skip the call.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


if __name__ == "__main__":
    app()
