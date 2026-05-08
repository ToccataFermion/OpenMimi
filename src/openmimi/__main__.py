"""Allow `python -m openmimi` execution.

Without arguments: starts chat REPL directly (same as `mimi`).
With arguments: passes to the full Typer CLI (same as `openmimi ...`).
"""
import sys

from openmimi.cli import app, chat_main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        chat_main()
    else:
        app()
