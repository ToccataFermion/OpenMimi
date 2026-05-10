"""Memory v2 retrieval tools (roadmap #9 stage 3).

Four ``ToolBase`` subclasses that expose the on-disk memory directory to
the LLM in a Claude-Code-style filesystem + grep workflow:

    data/memory/
      episodic/<YYYY-MM>/<session_id>.jsonl   # system-managed (loop writes)
      sites/<domain>.md|json                   # agent-writable site lessons
      skills/<name>.md                         # agent-writable skill templates

Tools:
    - memory_grep — substring or regex search across one or all scopes
    - memory_read — read one file (offset / limit windowed)
    - memory_write — create / append to a file under sites/ or skills/
                     (episodic is read-only from the LLM's perspective)
    - memory_list — enumerate files in a scope, with episodic-only
                    filters by month / domain

All four tools enforce path-traversal safety: a relative *path* / *glob*
input is resolved beneath the memory root and rejected if it escapes.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable

from .base import ToolBase
from .errors import ErrorCode, make_error_result
from .result import ToolResult

_log = logging.getLogger(__name__)

_DEFAULT_MEMORY_ROOT = Path("data/memory")

# Scope -> subdirectory name. Centralized so the 4 tools agree on the
# layout and so adding a new scope is a one-line change here.
_SCOPES: dict[str, str] = {
    "episodic": "episodic",
    "sites": "sites",
    "skills": "skills",
}

# Per-call output caps. The grep tool especially can produce a lot of
# bytes if the user is fishing in a fat episodic dir, and we'd rather
# truncate than blow up the LLM context.
_MAX_GREP_MATCHES = 50
_MAX_GREP_LINE_CHARS = 200
_MAX_READ_BYTES = 100_000
_MAX_WRITE_BYTES = 500_000
_MAX_LIST_ENTRIES = 200


def _resolve_scope_path(
    root: Path, scope: str, rel: str | None
) -> Path:
    """Resolve ``<root>/<scope>/<rel>`` and reject path traversal.

    ``rel`` may be empty / ``None`` to mean the scope dir itself.
    Returns a fully-resolved absolute Path. Raises ``ValueError`` if the
    resolved path escapes ``root/<scope>`` (e.g. ``rel="../../etc/passwd"``)
    or if *scope* is unknown.
    """
    sub = _SCOPES.get(scope)
    if sub is None:
        raise ValueError(f"unknown scope: {scope!r}")
    base = (root / sub).resolve()
    target = (base / (rel or "")).resolve()
    if target != base and not target.is_relative_to(base):
        raise ValueError(f"path escapes scope dir: {rel!r}")
    return target


def _split_scope_and_rel(path: str) -> tuple[str, str]:
    """Split a user-supplied ``path`` like ``"sites/github.com.md"`` into
    ``("sites", "github.com.md")``.

    Raises ValueError if the first component isn't a known scope or the
    string is empty.
    """
    parts = path.replace("\\", "/").lstrip("/").split("/", 1)
    head = parts[0] if parts else ""
    if head not in _SCOPES:
        raise ValueError(
            f"path must start with one of {sorted(_SCOPES)}; got {path!r}"
        )
    rel = parts[1] if len(parts) > 1 else ""
    return head, rel


def _iter_files(root: Path, scope: str, glob: str | None) -> Iterable[Path]:
    """Walk every regular file under ``<root>/<scope>``, optionally filtered.

    The iteration is deterministic (sorted) so grep output stays stable
    across runs. ``glob`` is matched against the path *relative to the
    scope dir* using ``fnmatch`` semantics so callers can write
    ``"*.md"`` or ``"2026-05/*.jsonl"``.
    """
    base = _resolve_scope_path(root, scope, None)
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if glob:
            rel = path.relative_to(base).as_posix()
            if not fnmatch.fnmatch(rel, glob):
                continue
        yield path


class MemoryGrepTool(ToolBase):
    """Substring / regex search over the on-disk memory tree.

    Output mimics ``ripgrep -n`` so the LLM can read it without parsing
    JSON: ``<scope>/<rel-path>:<line>:<text>``. Each line is clipped to
    200 chars and the whole result to 50 matches by default.
    """

    name = "memory_grep"

    def __init__(
        self, *, memory_root: Path | str = _DEFAULT_MEMORY_ROOT
    ) -> None:
        self._root = Path(memory_root)

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Search the on-disk memory tree for a pattern. By default a "
                "case-sensitive substring search; pass regex=true for full "
                "Python re syntax. Use scope to limit to one of "
                "episodic/sites/skills (omit to search all three). Output is "
                "ripgrep-style 'path:line:text', up to 50 matches."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Substring or regex to look for.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": list(_SCOPES.keys()),
                        "description": (
                            "Restrict to one scope. Omit to search all "
                            "three."
                        ),
                    },
                    "glob": {
                        "type": "string",
                        "description": (
                            "fnmatch glob, relative to scope dir "
                            "(e.g. '*.md', '2026-05/*.jsonl')."
                        ),
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "Treat pattern as Python regex.",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case-insensitive search.",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Cap results (default 50).",
                    },
                },
                "required": ["pattern"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        pattern = str(tool_input.get("pattern", ""))
        if not pattern:
            return make_error_result(
                ErrorCode.INVALID_INPUT, "pattern is required"
            )
        scope = tool_input.get("scope")
        glob = tool_input.get("glob")
        use_regex = bool(tool_input.get("regex", False))
        ignore_case = bool(tool_input.get("ignore_case", False))
        try:
            max_matches = int(tool_input.get("max_matches", _MAX_GREP_MATCHES))
        except (TypeError, ValueError):
            max_matches = _MAX_GREP_MATCHES
        max_matches = max(1, min(max_matches, 500))

        try:
            matcher = _compile_matcher(pattern, regex=use_regex, ignore_case=ignore_case)
        except re.error as exc:
            return make_error_result(
                ErrorCode.INVALID_INPUT, f"bad regex: {exc}"
            )

        scopes = [scope] if scope else list(_SCOPES.keys())
        if scope and scope not in _SCOPES:
            return make_error_result(
                ErrorCode.INVALID_INPUT, f"unknown scope: {scope!r}"
            )

        matches: list[str] = []
        files_scanned = 0
        try:
            for sc in scopes:
                for path in _iter_files(self._root, sc, glob):
                    files_scanned += 1
                    rel_label = f"{sc}/{path.relative_to(_resolve_scope_path(self._root, sc, None)).as_posix()}"
                    try:
                        with path.open("r", encoding="utf-8", errors="replace") as fh:
                            for line_no, line in enumerate(fh, 1):
                                if matcher(line):
                                    snippet = line.rstrip("\n")[:_MAX_GREP_LINE_CHARS]
                                    matches.append(
                                        f"{rel_label}:{line_no}:{snippet}"
                                    )
                                    if len(matches) >= max_matches:
                                        break
                    except OSError as exc:
                        _log.warning("memory_grep skipped %s: %s", path, exc)
                        continue
                    if len(matches) >= max_matches:
                        break
                if len(matches) >= max_matches:
                    break
        except ValueError as exc:
            return make_error_result(ErrorCode.INVALID_INPUT, str(exc))

        if not matches:
            return ToolResult(
                output=f"No matches in {files_scanned} file(s).",
                structured={"matches": [], "files_scanned": files_scanned},
            )
        truncated = len(matches) >= max_matches
        body = "\n".join(matches)
        suffix = "\n[truncated]" if truncated else ""
        return ToolResult(
            output=f"{len(matches)} match(es) across {files_scanned} file(s):\n{body}{suffix}",
            structured={
                "matches": matches,
                "files_scanned": files_scanned,
                "truncated": truncated,
            },
        )


class MemoryReadTool(ToolBase):
    """Read one memory file by scope-prefixed path.

    The path must be of the form ``<scope>/<rel>`` so the LLM never has
    to know that the actual root is ``data/memory``. Window with
    ``offset`` (1-based) and ``limit``.
    """

    name = "memory_read"

    def __init__(
        self, *, memory_root: Path | str = _DEFAULT_MEMORY_ROOT
    ) -> None:
        self._root = Path(memory_root)

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Read one memory file. Path must start with a scope: "
                "'episodic/<YYYY-MM>/<session>.jsonl', 'sites/<domain>.md', "
                "or 'skills/<name>.md'. Windowed with offset (1-based) and "
                "limit (max lines)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Scope-prefixed path, e.g. "
                            "'sites/github.com.md'."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-based line offset (default 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default 200).",
                    },
                },
                "required": ["path"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        path_str = str(tool_input.get("path", ""))
        if not path_str:
            return make_error_result(
                ErrorCode.INVALID_INPUT, "path is required"
            )
        try:
            scope, rel = _split_scope_and_rel(path_str)
            target = _resolve_scope_path(self._root, scope, rel)
        except ValueError as exc:
            return make_error_result(ErrorCode.INVALID_INPUT, str(exc))

        if not target.exists():
            return make_error_result(
                ErrorCode.TARGET_NOT_FOUND, f"memory file not found: {path_str}"
            )
        if target.is_dir():
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                f"path is a directory (use memory_list): {path_str}",
            )

        try:
            offset = max(1, int(tool_input.get("offset", 1)))
        except (TypeError, ValueError):
            offset = 1
        try:
            limit = max(1, min(int(tool_input.get("limit", 200)), 1000))
        except (TypeError, ValueError):
            limit = 200

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return make_error_result(
                ErrorCode.TOOL_INTERNAL_ERROR, f"read failed: {exc}"
            )
        lines = content.splitlines()
        start = offset - 1
        end = start + limit
        selected = lines[start:end]
        truncated = len(lines) > end
        prefix = (
            f"# {path_str}\n"
            f"Lines {offset}-{min(offset + limit - 1, len(lines))} "
            f"of {len(lines)}\n---\n"
        )
        suffix = "\n---\n[truncated]" if truncated else ""
        return ToolResult(
            output=prefix + "\n".join(selected) + suffix,
            structured={
                "path": path_str,
                "total_lines": len(lines),
                "offset": offset,
                "limit": limit,
            },
        )


class MemoryWriteTool(ToolBase):
    """Create or append a memory file under ``sites/`` or ``skills/``.

    The episodic scope is intentionally read-only here — episodic JSONL
    is system-managed by ``loop.py`` and a stray write would corrupt the
    one-line-per-step invariant. If the LLM tries it we fail clearly.
    """

    name = "memory_write"

    def __init__(
        self, *, memory_root: Path | str = _DEFAULT_MEMORY_ROOT
    ) -> None:
        self._root = Path(memory_root)

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Create / append a memory file under sites/ or skills/. "
                "Episodic is system-managed and rejected. mode='write' "
                "overwrites; mode='append' appends without trailing newline "
                "munging. Max 500KB per call."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Scope-prefixed path, e.g. "
                            "'sites/github.com.md' or "
                            "'skills/login_form.md'."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write or append.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["write", "append"],
                        "description": "Default 'write'.",
                    },
                },
                "required": ["path", "content"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        path_str = str(tool_input.get("path", ""))
        if not path_str:
            return make_error_result(
                ErrorCode.INVALID_INPUT, "path is required"
            )
        content = tool_input.get("content")
        if content is None:
            return make_error_result(
                ErrorCode.INVALID_INPUT, "content is required"
            )
        content = str(content)
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                f"content exceeds {_MAX_WRITE_BYTES} bytes",
            )
        mode = str(tool_input.get("mode", "write")).lower()
        if mode not in {"write", "append"}:
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                f"mode must be 'write' or 'append', got {mode!r}",
            )

        try:
            scope, rel = _split_scope_and_rel(path_str)
        except ValueError as exc:
            return make_error_result(ErrorCode.INVALID_INPUT, str(exc))
        if scope == "episodic":
            return make_error_result(
                ErrorCode.PERMISSION_DENIED,
                "episodic scope is read-only; write to sites/ or skills/",
            )
        if not rel:
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                "path must include a filename, not just a scope",
            )
        try:
            target = _resolve_scope_path(self._root, scope, rel)
        except ValueError as exc:
            return make_error_result(ErrorCode.INVALID_INPUT, str(exc))
        if target.is_dir():
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                f"path is a directory: {path_str}",
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with target.open("a", encoding="utf-8") as f:
                    f.write(content)
                action = "Appended"
            else:
                target.write_text(content, encoding="utf-8")
                action = "Wrote"
        except OSError as exc:
            return make_error_result(
                ErrorCode.TOOL_INTERNAL_ERROR, f"write failed: {exc}"
            )
        return ToolResult(
            output=f"{action} {len(content)} chars to {path_str}",
            structured={"path": path_str, "bytes": len(content), "mode": mode},
        )


class MemoryListTool(ToolBase):
    """Enumerate files in one scope, with episodic-only month/domain filters."""

    name = "memory_list"

    def __init__(
        self, *, memory_root: Path | str = _DEFAULT_MEMORY_ROOT
    ) -> None:
        self._root = Path(memory_root)

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List memory files in a scope. For episodic, optional "
                "month=YYYY-MM and domain=<host> filters narrow the listing. "
                "Output is one scope-prefixed path per line."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": list(_SCOPES.keys()),
                        "description": "Which subtree to list.",
                    },
                    "month": {
                        "type": "string",
                        "description": "YYYY-MM, episodic scope only.",
                    },
                    "domain": {
                        "type": "string",
                        "description": (
                            "Match the first record's 'domain' field; "
                            "episodic scope only."
                        ),
                    },
                    "glob": {
                        "type": "string",
                        "description": (
                            "fnmatch glob, relative to scope dir; "
                            "applied last."
                        ),
                    },
                },
                "required": ["scope"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        scope = str(tool_input.get("scope", ""))
        if scope not in _SCOPES:
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                f"scope must be one of {sorted(_SCOPES)}; got {scope!r}",
            )
        month = tool_input.get("month")
        domain = tool_input.get("domain")
        glob = tool_input.get("glob")

        try:
            base = _resolve_scope_path(self._root, scope, None)
        except ValueError as exc:
            return make_error_result(ErrorCode.INVALID_INPUT, str(exc))
        if not base.is_dir():
            return ToolResult(
                output=f"{scope}: (empty)",
                structured={"scope": scope, "files": []},
            )

        if scope == "episodic":
            files = _list_episodic(base, month=month, domain=domain)
        else:
            if month or domain:
                return make_error_result(
                    ErrorCode.INVALID_INPUT,
                    "month/domain filters only apply to episodic scope",
                )
            files = sorted(p for p in base.rglob("*") if p.is_file())

        if glob:
            files = [
                p for p in files
                if fnmatch.fnmatch(p.relative_to(base).as_posix(), glob)
            ]

        files = files[:_MAX_LIST_ENTRIES]
        rels = [
            f"{scope}/{p.relative_to(base).as_posix()}" for p in files
        ]
        if not rels:
            body = f"{scope}: (no matching files)"
        else:
            body = f"{scope}: {len(rels)} file(s)\n" + "\n".join(rels)
        return ToolResult(
            output=body,
            structured={"scope": scope, "files": rels},
        )


def _compile_matcher(
    pattern: str, *, regex: bool, ignore_case: bool
):
    """Return a callable ``line -> bool`` for the chosen pattern mode."""
    if regex:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(pattern, flags)
        return lambda line: bool(compiled.search(line))
    if ignore_case:
        needle = pattern.lower()
        return lambda line: needle in line.lower()
    return lambda line: pattern in line


def _list_episodic(
    base: Path, *, month: Any, domain: Any
) -> list[Path]:
    """Episodic-specific listing: walk YYYY-MM/ subdirs, filter by domain.

    Mirrors ``EpisodicStore.list_sessions`` but operates on whatever
    base dir the tool was constructed with so tests can redirect freely.
    """
    if month and not re.fullmatch(r"\d{4}-\d{2}", str(month)):
        return []
    out: list[Path] = []
    if month:
        candidates = [base / str(month)] if (base / str(month)).is_dir() else []
    else:
        candidates = sorted(
            p for p in base.iterdir()
            if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name)
        )
    for month_dir in candidates:
        for jsonl in sorted(month_dir.glob("*.jsonl")):
            if domain is None or _first_record_domain(jsonl) == str(domain):
                out.append(jsonl)
    return out


def _first_record_domain(path: Path) -> str | None:
    """Read the first JSON line and return its ``domain`` field, if any."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    d = rec.get("domain")
                    if isinstance(d, str):
                        return d
                return None
    except OSError:
        return None
    return None


__all__ = [
    "MemoryGrepTool",
    "MemoryReadTool",
    "MemoryWriteTool",
    "MemoryListTool",
]
