"""OpenMimi CLI entry point."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .config import load_config

app = typer.Typer(help="OpenMimi - Local Windows AI Agent.")


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
