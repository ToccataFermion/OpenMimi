"""Episodic memory store (roadmap #9 stage 1).

Persists per-step session records on disk so future runs can grep across
prior trajectories. The layout is intentionally human-readable so users
can ``cat`` / ``grep`` / open in an editor without an extra index:

    data/memory/episodic/<YYYY-MM>/<session_id>.jsonl

Each line is a JSON object with the auto-injected meta keys ``ts`` /
``session_id`` / ``step`` plus whatever payload the caller supplied
(typically ``tool`` / ``action`` / ``result_summary`` / ``is_error`` /
``url`` / ``domain``). Sessions that cross a month boundary land in
two files — ``read_session`` stitches them back together in chronological
order.

This module ships only the storage primitives. Stage 2 wires the loop
to call ``append_step`` after each tool result, stage 3 exposes the
``memory_grep`` / ``memory_read`` / ``memory_write`` / ``memory_list``
tools to the LLM.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_EPISODIC_DIR = Path("data/memory/episodic")
_MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")


def _safe_session_id(session_id: str) -> str:
    """Strip filesystem-unsafe chars from a session id.

    Session ids are normally 32-hex uuids so this is a defensive guard
    for callers that pass arbitrary strings (e.g. tests).
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", session_id.strip())
    return cleaned or "session"


class EpisodicStore:
    """Append-only JSONL store for per-step session traces.

    Thread-safety: file appends use ``open(..., "a")`` which on POSIX is
    atomic for writes shorter than ``PIPE_BUF`` (~4KB). Each step record
    is a single JSON line well below that limit, so concurrent
    ``append_step`` calls from different processes don't interleave.
    Windows offers similar guarantees in append mode for small writes.
    """

    def __init__(self, base_dir: Path | str = _DEFAULT_EPISODIC_DIR) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def append_step(
        self,
        *,
        session_id: str,
        step: int,
        record: dict[str, Any],
        ts: datetime | None = None,
    ) -> Path:
        """Append one step record to the session JSONL file.

        ``ts`` defaults to ``datetime.now(tz=UTC)``; the month directory
        is derived from the timestamp so a session that crosses midnight
        on the first of a month naturally splits across two files. The
        returned path points at the JSONL file that was appended to.

        Auto-injected keys (``ts`` / ``session_id`` / ``step``) override
        any same-named keys in *record* so the on-disk shape is
        consistent.
        """
        ts = ts or datetime.now(tz=timezone.utc)
        month = ts.strftime("%Y-%m")
        merged: dict[str, Any] = {
            **record,
            "session_id": session_id,
            "step": step,
            "ts": ts.isoformat(timespec="seconds"),
        }
        path = self._session_path(month, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(merged, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return path

    def read_session(self, session_id: str) -> list[dict[str, Any]]:
        """Return all step records for *session_id*, sorted by step ascending.

        Walks every month directory under ``base_dir`` so a session that
        spans a month boundary still returns a single ordered list.
        Malformed JSON lines are skipped (and warning-logged) so a single
        corrupt step can't poison the whole session read.
        """
        safe = _safe_session_id(session_id)
        records: list[dict[str, Any]] = []
        for month_dir in self._month_dirs():
            f = month_dir / f"{safe}.jsonl"
            if not f.is_file():
                continue
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        _log.warning(
                            "skipping malformed JSONL line in %s", f
                        )
                        continue
        records.sort(key=lambda r: (r.get("step", 0), r.get("ts", "")))
        return records

    def list_sessions(
        self,
        *,
        month: str | None = None,
        domain: str | None = None,
    ) -> list[Path]:
        """Return JSONL session paths optionally filtered by month / domain.

        ``month`` is a ``YYYY-MM`` string; passing ``None`` walks every
        month directory. ``domain`` matches against the first record's
        ``domain`` field — sessions whose first row has no domain are
        excluded when a filter is provided.

        Output is sorted by ``(month, session_id)`` for deterministic
        listing across runs.
        """
        if month is not None:
            target = self._base_dir / month
            month_dirs = [target] if target.is_dir() else []
        else:
            month_dirs = self._month_dirs()

        out: list[Path] = []
        for month_dir in month_dirs:
            for jsonl in sorted(month_dir.glob("*.jsonl")):
                if domain is None or self._first_record_domain(jsonl) == domain:
                    out.append(jsonl)
        return out

    def _session_path(self, month: str, session_id: str) -> Path:
        return self._base_dir / month / f"{_safe_session_id(session_id)}.jsonl"

    def _month_dirs(self) -> list[Path]:
        if not self._base_dir.is_dir():
            return []
        out = [
            p
            for p in self._base_dir.iterdir()
            if p.is_dir() and _MONTH_DIR_RE.match(p.name)
        ]
        out.sort(key=lambda p: p.name)
        return out

    @staticmethod
    def _first_record_domain(path: Path) -> str | None:
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


__all__ = ["EpisodicStore"]
