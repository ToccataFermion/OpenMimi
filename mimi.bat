@echo off
setlocal enabledelayedexpansion

REM OpenMimi launcher for Windows
REM Works from any directory — auto-detects venv or system Python

set "SCRIPT_DIR=%~dp0"
set "PYTHON_CMD=python"

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%SCRIPT_DIR%.venv\Scripts\python.exe"
) else if exist "%SCRIPT_DIR%venv\Scripts\python.exe" (
    set "PYTHON_CMD=%SCRIPT_DIR%venv\Scripts\python.exe"
)

%PYTHON_CMD% -m openmimi %*
