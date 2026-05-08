"""Load domain-specific skill files from the ``skills/`` directory.

Each ``<domain>.md`` file is injected into the system prompt when the user
task mentions that domain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_DEFAULT_SKILLS_DIR = Path("skills")


def load_skill(domain: str) -> str | None:
    """Return the raw markdown content for *domain* if a matching file exists."""
    path = _DEFAULT_SKILLS_DIR / f"{domain}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def format_skill_for_prompt(domain: str) -> str | None:
    """Return a text block suitable for appending to the system prompt."""
    raw = load_skill(domain)
    if not raw:
        return None
    lines = raw.splitlines()
    # Drop the H1 title (first line starting with #) to avoid repetition
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        return None
    return f"## Site-specific guidance for {domain}\n\n{body}"
