@echo off
REM Quick reinstall after pyproject.toml or entry-point changes
cd /d "%~dp0"
.venv\Scripts\pip install -e . --no-deps
