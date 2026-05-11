"""Audit JSONL aggregation for tool-success monitoring.

Why this exists: the agent's per-call success rate is the load-bearing signal
for whether the eval/click/focus stack is healthy. Past field-name drift
(``js_code`` vs ``js``) and Windows foreground-lock failures only surfaced
after they had already silently inflated the failure rate for days. Scanning
``data/audit/<session>.jsonl`` and grouping by ``(tool, action)`` turns those
into a small table the user (and the agent) can glance at.

The aggregation is intentionally pure — it takes a directory of audit files
and returns plain dataclasses. The CLI layer ties it to ``typer`` and ``rich``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator


@dataclass
class ToolStat:
    """Aggregated metrics for one ``(tool, action)`` bucket."""

    tool: str
    action: str
    calls: int = 0
    errors: int = 0
    total_ms: int = 0
    last_error_ts: str = ""
    last_error_summary: str = ""
    error_codes: dict[str, int] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.tool, self.action)


def _parse_ts(ts: str | None) -> datetime | None:
    """Audit logs use ``isoformat(timespec="milliseconds")`` → ``...+00:00``.

    Returns None on anything we can't parse, so a malformed timestamp doesn't
    drop a whole session.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _iter_records(audit_dir: Path) -> Iterator[dict]:
    for path in sorted(audit_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def _action_of(rec: dict) -> str:
    """Pull the action out of ``tool_input``; older records may lack one."""
    tool_input = rec.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return "?"
    action = tool_input.get("action")
    return str(action) if action else "?"


def aggregate(
    audit_dir: Path,
    *,
    since: datetime | None = None,
    tool_filter: str | None = None,
) -> list[ToolStat]:
    """Walk every JSONL file under ``audit_dir`` and bucket records.

    ``since`` filters records by ``ts``. Records whose timestamp is missing or
    unparseable are kept (we'd rather over-include than silently hide them).
    ``tool_filter`` does substring matching against the ``tool`` field.
    """
    buckets: dict[tuple[str, str], ToolStat] = {}

    for rec in _iter_records(audit_dir):
        tool = str(rec.get("tool") or "?")
        if tool_filter and tool_filter.lower() not in tool.lower():
            continue
        ts = _parse_ts(rec.get("ts"))
        if since is not None and ts is not None and ts < since:
            continue

        action = _action_of(rec)
        key = (tool, action)
        stat = buckets.get(key)
        if stat is None:
            stat = ToolStat(tool=tool, action=action)
            buckets[key] = stat

        stat.calls += 1
        duration = rec.get("duration_ms")
        if isinstance(duration, (int, float)):
            stat.total_ms += int(duration)
        if rec.get("is_error"):
            stat.errors += 1
            code = rec.get("error_code")
            if code:
                stat.error_codes[str(code)] = stat.error_codes.get(str(code), 0) + 1
            raw_summary = rec.get("result_summary") or ""
            lines = raw_summary.splitlines()
            summary = lines[0] if lines else ""
            ts_str = rec.get("ts") or ""
            # Track the most-recent error to give the user a clue what's wrong.
            if not stat.last_error_ts or ts_str > stat.last_error_ts:
                stat.last_error_ts = ts_str
                stat.last_error_summary = summary

    return list(buckets.values())


def filter_and_sort(
    stats: Iterable[ToolStat],
    *,
    min_calls: int = 1,
    sort_by: str = "error_rate",
) -> list[ToolStat]:
    """Drop low-volume buckets and sort by the chosen metric, descending.

    ``sort_by`` accepts ``error_rate``, ``errors``, ``calls``, or ``avg_ms``.
    Anything else falls back to ``error_rate`` to keep the CLI forgiving.
    """
    kept = [s for s in stats if s.calls >= min_calls]
    key_fn = {
        "error_rate": lambda s: (s.error_rate, s.errors),
        "errors": lambda s: (s.errors, s.error_rate),
        "calls": lambda s: (s.calls, s.error_rate),
        "avg_ms": lambda s: (s.avg_ms, s.calls),
    }.get(sort_by, lambda s: (s.error_rate, s.errors))
    return sorted(kept, key=key_fn, reverse=True)


def since_from_days(days: float | None) -> datetime | None:
    """Translate ``--since N`` (days) into an absolute UTC cutoff."""
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


__all__ = [
    "ToolStat",
    "aggregate",
    "filter_and_sort",
    "since_from_days",
]
