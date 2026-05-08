"""Site-specific memory store for OpenMimi.

Persists lessons learned across sessions so the agent does not
start from scratch on the same site every time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_MEMORY_DIR = Path("data/memory/sites")


class SiteMemoryStore:
    """Read/write per-domain memory files."""

    def __init__(self, base_dir: Path | str = _DEFAULT_MEMORY_DIR) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def load(self, domain: str) -> dict[str, Any] | None:
        path = self._path(domain)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, domain: str, memory: dict[str, Any]) -> None:
        path = self._path(domain)
        memory["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)

    def format_for_prompt(self, domain: str) -> str | None:
        """Return a concise text block suitable for injection into system prompt."""
        mem = self.load(domain)
        if not mem:
            return None
        parts: list[str] = []
        header = mem.get("domain", domain)
        parts.append(f"## Previous experience with {header}")
        if mem.get("known_refs"):
            parts.append("### Known element refs")
            for ref, desc in mem["known_refs"].items():
                parts.append(f"- {ref}: {desc}")
        if mem.get("tips"):
            parts.append("### Tips")
            for tip in mem["tips"]:
                parts.append(f"- {tip}")
        if mem.get("failure_patterns"):
            parts.append("### Known failure patterns")
            for fp in mem["failure_patterns"]:
                parts.append(f"- {fp}")
        if mem.get("success_paths"):
            parts.append("### Successful paths")
            for sp in mem["success_paths"]:
                parts.append(f"- {sp}")
        return "\n".join(parts)

    def merge(self, domain: str, new_memory: dict[str, Any]) -> dict[str, Any]:
        """Merge new_memory into existing memory for domain."""
        existing = self.load(domain) or {}
        merged: dict[str, Any] = {
            "domain": domain,
            "known_refs": {**existing.get("known_refs", {}), **new_memory.get("known_refs", {})},
            "tips": _dedupe_list(existing.get("tips", []) + new_memory.get("tips", [])),
            "failure_patterns": _dedupe_list(
                existing.get("failure_patterns", []) + new_memory.get("failure_patterns", [])
            ),
            "success_paths": _dedupe_list(
                existing.get("success_paths", []) + new_memory.get("success_paths", [])
            ),
        }
        return merged

    def _path(self, domain: str) -> Path:
        safe = domain.replace("/", "_").replace("\\", "_")
        return self._base_dir / f"{safe}.json"


def extract_domain(text: str) -> str | None:
    """Best-effort domain extraction from a task string."""
    m = re.search(r"https?://([^/\s]+)", text)
    if m:
        return m.group(1)
    return None


def _dedupe_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out
