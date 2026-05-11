"""Unit tests for ``openmimi.audit.stats`` aggregation.

Pins the per-(tool, action) rollup that powers ``mimi audit-stats`` so the
field-name drift and Windows foreground-lock failures that triggered this
feature stay visible if they ever come back.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmimi.audit.stats import (
    ToolStat,
    aggregate,
    filter_and_sort,
    since_from_days,
)


def _write_audit(
    audit_dir: Path,
    session_id: str,
    records: list[dict],
) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    return path


def _rec(
    *,
    tool: str = "agent_browser",
    action: str = "click",
    is_error: bool = False,
    error_code: str | None = None,
    ts: str = "2026-05-11T10:00:00.000+00:00",
    duration_ms: int = 100,
    summary: str = "ok",
) -> dict:
    return {
        "ts": ts,
        "session_id": "s",
        "step": 1,
        "tool": tool,
        "tool_input": {"action": action},
        "result_summary": summary,
        "is_error": is_error,
        "error_code": error_code,
        "image_path": None,
        "duration_ms": duration_ms,
    }


def test_aggregate_groups_by_tool_and_action(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        "A",
        [
            _rec(tool="agent_browser", action="click"),
            _rec(tool="agent_browser", action="click", is_error=True),
            _rec(tool="agent_browser", action="navigate"),
        ],
    )
    _write_audit(tmp_path, "B", [_rec(tool="computer", action="screenshot")])

    stats = aggregate(tmp_path)
    by_key = {s.key: s for s in stats}

    assert by_key[("agent_browser", "click")].calls == 2
    assert by_key[("agent_browser", "click")].errors == 1
    assert by_key[("agent_browser", "click")].error_rate == 0.5
    assert by_key[("agent_browser", "navigate")].calls == 1
    assert by_key[("agent_browser", "navigate")].errors == 0
    assert by_key[("computer", "screenshot")].calls == 1


def test_aggregate_handles_missing_action(tmp_path: Path) -> None:
    """Older audit records may not have ``tool_input.action`` (e.g. memory tool)."""
    _write_audit(
        tmp_path,
        "A",
        [
            {
                "ts": "2026-05-11T10:00:00.000+00:00",
                "tool": "memory",
                "tool_input": {"query": "foo"},
                "result_summary": "x",
                "is_error": False,
                "duration_ms": 10,
            },
            {
                "ts": "2026-05-11T10:01:00.000+00:00",
                "tool": "memory",
                "tool_input": None,
                "is_error": True,
                "duration_ms": 1,
            },
        ],
    )

    stats = aggregate(tmp_path)
    assert len(stats) == 1
    assert stats[0].key == ("memory", "?")
    assert stats[0].calls == 2
    assert stats[0].errors == 1


def test_aggregate_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"tool": "x", "tool_input": {"action": "y"}, "is_error": false, '
        '"duration_ms": 5, "ts": "2026-05-11T10:00:00.000+00:00"}\n'
        "this is not json\n"
        "\n"
        '{"tool": "x", "tool_input": {"action": "y"}, "is_error": true, '
        '"duration_ms": 5, "ts": "2026-05-11T10:00:01.000+00:00"}\n',
        encoding="utf-8",
    )
    stats = aggregate(tmp_path)
    assert len(stats) == 1
    assert stats[0].calls == 2
    assert stats[0].errors == 1


def test_aggregate_since_filter_drops_old_records(tmp_path: Path) -> None:
    old = "2025-01-01T00:00:00.000+00:00"
    new = "2026-05-11T00:00:00.000+00:00"
    _write_audit(
        tmp_path,
        "A",
        [
            _rec(action="old_act", ts=old),
            _rec(action="new_act", ts=new),
        ],
    )

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stats = aggregate(tmp_path, since=cutoff)
    actions = {s.action for s in stats}
    assert actions == {"new_act"}


def test_aggregate_tool_filter_is_substring_and_case_insensitive(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        "A",
        [
            _rec(tool="agent_browser", action="x"),
            _rec(tool="browser_extract", action="x"),
            _rec(tool="computer", action="x"),
        ],
    )
    stats = aggregate(tmp_path, tool_filter="BROWSER")
    tools = sorted(s.tool for s in stats)
    assert tools == ["agent_browser", "browser_extract"]


def test_aggregate_tracks_last_error_and_codes(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        "A",
        [
            _rec(
                action="click",
                is_error=True,
                error_code="TARGET_NOT_FOUND",
                ts="2026-05-10T00:00:00.000+00:00",
                summary="early failure",
            ),
            _rec(
                action="click",
                is_error=True,
                error_code="TOOL_INTERNAL_ERROR",
                ts="2026-05-11T12:00:00.000+00:00",
                summary="later failure on click",
            ),
            _rec(
                action="click",
                is_error=True,
                error_code="TARGET_NOT_FOUND",
                ts="2026-05-09T00:00:00.000+00:00",
                summary="earliest failure",
            ),
        ],
    )

    stats = aggregate(tmp_path)
    s = stats[0]
    assert s.last_error_summary == "later failure on click"
    assert s.error_codes == {"TARGET_NOT_FOUND": 2, "TOOL_INTERNAL_ERROR": 1}


def test_aggregate_avg_ms_uses_total_ms(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        "A",
        [
            _rec(action="x", duration_ms=100),
            _rec(action="x", duration_ms=300),
        ],
    )
    s = aggregate(tmp_path)[0]
    assert s.total_ms == 400
    assert s.avg_ms == 200.0


def test_aggregate_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert aggregate(tmp_path / "does-not-exist") == []


def test_filter_and_sort_respects_min_calls(tmp_path: Path) -> None:
    stats = [
        ToolStat(tool="a", action="x", calls=2, errors=1, total_ms=20),
        ToolStat(tool="b", action="y", calls=10, errors=2, total_ms=100),
    ]
    out = filter_and_sort(stats, min_calls=5)
    assert [s.tool for s in out] == ["b"]


def test_filter_and_sort_default_is_error_rate_descending() -> None:
    a = ToolStat(tool="a", action="x", calls=10, errors=9)  # 90%
    b = ToolStat(tool="b", action="y", calls=10, errors=1)  # 10%
    c = ToolStat(tool="c", action="z", calls=10, errors=5)  # 50%
    out = filter_and_sort([b, a, c])
    assert [s.tool for s in out] == ["a", "c", "b"]


def test_filter_and_sort_supports_other_keys() -> None:
    a = ToolStat(tool="a", action="x", calls=10, errors=1, total_ms=100)  # avg 10
    b = ToolStat(tool="b", action="y", calls=2, errors=0, total_ms=2000)  # avg 1000
    assert [s.tool for s in filter_and_sort([a, b], sort_by="calls")] == ["a", "b"]
    assert [s.tool for s in filter_and_sort([a, b], sort_by="avg_ms")] == ["b", "a"]
    assert [s.tool for s in filter_and_sort([a, b], sort_by="errors")] == ["a", "b"]


def test_filter_and_sort_falls_back_for_unknown_key() -> None:
    a = ToolStat(tool="a", action="x", calls=10, errors=9)
    b = ToolStat(tool="b", action="y", calls=10, errors=1)
    out = filter_and_sort([b, a], sort_by="not_a_thing")
    assert [s.tool for s in out] == ["a", "b"]


def test_since_from_days_returns_recent_utc_or_none() -> None:
    assert since_from_days(None) is None
    cutoff = since_from_days(7)
    assert cutoff is not None
    assert cutoff.tzinfo is not None
    delta = datetime.now(timezone.utc) - cutoff
    # ~7 days, with some slack for test scheduler jitter.
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


def test_aggregate_keeps_records_with_unparseable_ts(tmp_path: Path) -> None:
    _write_audit(
        tmp_path,
        "A",
        [
            {
                "ts": "garbage",
                "tool": "x",
                "tool_input": {"action": "y"},
                "is_error": False,
                "duration_ms": 1,
            }
        ],
    )
    stats = aggregate(tmp_path, since=datetime.now(timezone.utc))
    assert len(stats) == 1
    assert stats[0].calls == 1
