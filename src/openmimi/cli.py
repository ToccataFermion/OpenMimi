"""OpenMimi CLI entry point."""
from __future__ import annotations

import typer

app = typer.Typer(help="OpenMimi - Local Windows AI Agent.")


@app.command()
def run(task: str) -> None:
    """Run a single task end-to-end."""
    raise NotImplementedError("M1: wire orchestrator.run_task")


@app.command()
def replay(session_id: str) -> None:
    """Replay actions and screenshots for a previous session."""
    raise NotImplementedError("M1: wire replay walker")


if __name__ == "__main__":
    app()
