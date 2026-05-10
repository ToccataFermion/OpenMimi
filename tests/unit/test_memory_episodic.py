"""Tests for the episodic memory store (roadmap #9 stage 1)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openmimi.memory.episodic import EpisodicStore, _safe_session_id


def _ts(year: int, month: int, day: int = 15) -> datetime:
    return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)


def test_append_step_writes_jsonl_with_auto_meta(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    path = store.append_step(
        session_id="abc123",
        step=0,
        record={"tool": "browser", "url": "https://example.com"},
        ts=_ts(2026, 5),
    )

    assert path == tmp_path / "2026-05" / "abc123.jsonl"
    assert path.is_file()
    line = path.read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert rec["session_id"] == "abc123"
    assert rec["step"] == 0
    assert rec["tool"] == "browser"
    assert rec["url"] == "https://example.com"
    # ISO 8601 second-precision UTC
    assert rec["ts"].startswith("2026-05-15T12:00:00")


def test_append_step_appends_multiple_lines(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    for i in range(3):
        store.append_step(
            session_id="sid",
            step=i,
            record={"tool": "shell", "ix": i},
            ts=_ts(2026, 5),
        )
    f = tmp_path / "2026-05" / "sid.jsonl"
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["step"] for p in parsed] == [0, 1, 2]
    assert [p["ix"] for p in parsed] == [0, 1, 2]


def test_append_step_meta_overrides_user_supplied(tmp_path: Path) -> None:
    """User-supplied ts/session_id/step keys must NOT win over the meta args."""
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(
        session_id="real-sid",
        step=7,
        record={
            "session_id": "wrong",
            "step": 999,
            "ts": "wrong-ts",
            "payload": "ok",
        },
        ts=_ts(2026, 5),
    )
    f = tmp_path / "2026-05" / "real-sid.jsonl"
    rec = json.loads(f.read_text(encoding="utf-8").strip())
    assert rec["session_id"] == "real-sid"
    assert rec["step"] == 7
    assert rec["ts"].startswith("2026-05-15")
    assert rec["payload"] == "ok"


def test_read_session_returns_records_in_step_order(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    # Write out of order to confirm the store sorts by step.
    for step in [2, 0, 1]:
        store.append_step(
            session_id="sid",
            step=step,
            record={"ix": step},
            ts=_ts(2026, 5),
        )
    out = store.read_session("sid")
    assert [r["step"] for r in out] == [0, 1, 2]
    assert [r["ix"] for r in out] == [0, 1, 2]


def test_read_session_unknown_session_returns_empty(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(
        session_id="known",
        step=0,
        record={},
        ts=_ts(2026, 5),
    )
    assert store.read_session("nonexistent") == []


def test_read_session_skips_malformed_lines(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(session_id="sid", step=0, record={"ok": 1}, ts=_ts(2026, 5))
    # Manually corrupt the file.
    f = tmp_path / "2026-05" / "sid.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write("not valid json\n")
    store.append_step(session_id="sid", step=1, record={"ok": 2}, ts=_ts(2026, 5))

    out = store.read_session("sid")
    assert len(out) == 2
    assert {r["ok"] for r in out} == {1, 2}


def test_read_session_stitches_across_months(tmp_path: Path) -> None:
    """A session that crosses a month boundary returns a unified list."""
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(
        session_id="sid", step=0, record={"part": "may"}, ts=_ts(2026, 5)
    )
    store.append_step(
        session_id="sid", step=1, record={"part": "june"}, ts=_ts(2026, 6)
    )

    # Two distinct files exist:
    assert (tmp_path / "2026-05" / "sid.jsonl").is_file()
    assert (tmp_path / "2026-06" / "sid.jsonl").is_file()

    out = store.read_session("sid")
    assert [r["part"] for r in out] == ["may", "june"]
    assert [r["step"] for r in out] == [0, 1]


def test_list_sessions_returns_all_when_unfiltered(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(session_id="a", step=0, record={}, ts=_ts(2026, 5))
    store.append_step(session_id="b", step=0, record={}, ts=_ts(2026, 5))
    store.append_step(session_id="c", step=0, record={}, ts=_ts(2026, 6))

    paths = store.list_sessions()
    names = sorted(p.name for p in paths)
    assert names == ["a.jsonl", "b.jsonl", "c.jsonl"]


def test_list_sessions_filters_by_month(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(session_id="a", step=0, record={}, ts=_ts(2026, 5))
    store.append_step(session_id="b", step=0, record={}, ts=_ts(2026, 6))

    may = store.list_sessions(month="2026-05")
    june = store.list_sessions(month="2026-06")
    assert [p.name for p in may] == ["a.jsonl"]
    assert [p.name for p in june] == ["b.jsonl"]

    missing = store.list_sessions(month="2026-07")
    assert missing == []


def test_list_sessions_filters_by_domain(tmp_path: Path) -> None:
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(
        session_id="a",
        step=0,
        record={"domain": "example.com"},
        ts=_ts(2026, 5),
    )
    store.append_step(
        session_id="b",
        step=0,
        record={"domain": "other.com"},
        ts=_ts(2026, 5),
    )
    # Session with no domain on the first record — should be excluded
    # when a domain filter is specified.
    store.append_step(session_id="c", step=0, record={}, ts=_ts(2026, 5))

    matched = store.list_sessions(domain="example.com")
    assert [p.name for p in matched] == ["a.jsonl"]


def test_list_sessions_ignores_unrelated_directories(tmp_path: Path) -> None:
    """Stray non-month dirs under base must not be walked."""
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(session_id="a", step=0, record={}, ts=_ts(2026, 5))
    (tmp_path / "garbage").mkdir()
    (tmp_path / "garbage" / "x.jsonl").write_text("{}\n", encoding="utf-8")

    paths = store.list_sessions()
    assert [p.name for p in paths] == ["a.jsonl"]


def test_safe_session_id_strips_unsafe_characters() -> None:
    assert _safe_session_id("simple") == "simple"
    assert _safe_session_id("with/slash") == "with_slash"
    assert _safe_session_id("a b c") == "a_b_c"
    assert _safe_session_id("../../etc/passwd") == ".._.._etc_passwd"
    assert _safe_session_id("") == "session"


def test_append_step_sanitizes_session_id(tmp_path: Path) -> None:
    """A session id with slashes must not escape the base dir."""
    store = EpisodicStore(base_dir=tmp_path)
    store.append_step(
        session_id="../escape",
        step=0,
        record={"x": 1},
        ts=_ts(2026, 5),
    )
    # The sanitized name lands inside the month dir, no path traversal.
    assert (tmp_path / "2026-05" / ".._escape.jsonl").is_file()


def test_append_step_uses_now_when_ts_missing(tmp_path: Path) -> None:
    """Default ts = datetime.now(UTC), month dir derived from it."""
    store = EpisodicStore(base_dir=tmp_path)
    path = store.append_step(session_id="x", step=0, record={})
    # Must land in *some* yyyy-mm directory under base.
    assert path.parent.parent == tmp_path
    assert len(path.parent.name) == 7  # YYYY-MM
    assert path.parent.name[4] == "-"
