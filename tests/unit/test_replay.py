"""Tests for replay module (roadmap #11 stages 1 + 2)."""
from __future__ import annotations

from openmimi.replay import build_html


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_build_html_returns_full_document() -> None:
    out = build_html([])
    assert out.startswith("<!doctype html>")
    assert "<html" in out and "</html>" in out
    assert "<head>" in out and "</head>" in out
    assert "<body>" in out and "</body>" in out


def test_empty_records_show_friendly_placeholder() -> None:
    out = build_html([])
    assert "No step records" in out
    assert "0 steps" in out


def test_default_title_is_fallback_when_no_session_id() -> None:
    out = build_html([])
    assert "<title>OpenMimi replay</title>" in out


def test_explicit_title_wins() -> None:
    out = build_html([], title="custom-title")
    assert "<title>custom-title</title>" in out


def test_title_falls_back_to_first_session_id() -> None:
    out = build_html([{"step": 1, "session_id": "abc123", "tool": "shell"}])
    assert "<title>abc123</title>" in out
    assert "Session abc123" in out


# ---------------------------------------------------------------------------
# Step rendering
# ---------------------------------------------------------------------------


def _step(
    *,
    step: int = 1,
    tool: str = "shell",
    action: str | None = None,
    is_error: bool = False,
    error_code: str | None = None,
    url: str | None = None,
    result_summary: str = "",
    **extra: object,
) -> dict[str, object]:
    record: dict[str, object] = {
        "step": step,
        "tool": tool,
        "is_error": is_error,
        "result_summary": result_summary,
    }
    if action is not None:
        record["action"] = action
    if error_code is not None:
        record["error_code"] = error_code
    if url is not None:
        record["url"] = url
    record.update(extra)
    return record


def test_renders_one_details_per_record() -> None:
    out = build_html([_step(step=1), _step(step=2), _step(step=3)])
    assert out.count("<details") == 3


def test_step_number_appears_in_summary() -> None:
    out = build_html([_step(step=7, tool="browser")])
    assert "#7" in out
    assert "browser" in out


def test_action_appears_when_present() -> None:
    out = build_html([_step(tool="browser", action="navigate")])
    assert "navigate" in out


def test_error_marker_renders_when_is_error() -> None:
    out = build_html(
        [_step(is_error=True, error_code="NETWORK_TIMEOUT")]
    )
    assert 'class="is-error"' in out
    assert "ERROR" in out
    assert "NETWORK_TIMEOUT" in out


def test_no_error_marker_when_ok() -> None:
    out = build_html([_step(is_error=False)])
    assert 'class="is-error"' not in out
    assert ">ok<" in out


def test_url_renders_when_present() -> None:
    out = build_html([_step(url="https://example.com/path")])
    assert "https://example.com/path" in out


def test_result_summary_renders_in_pre_block() -> None:
    out = build_html(
        [_step(result_summary="multi\nline\ntext")]
    )
    assert "<pre>" in out
    assert "multi" in out


def test_unknown_keys_render_as_json_payload() -> None:
    out = build_html([_step(extra_field="foo", another=42)])
    # JSON dump should appear in a pre block
    assert "extra_field" in out
    assert "42" in out


def test_skips_non_mapping_records_silently() -> None:
    out = build_html([_step(step=1), "not a dict", None, _step(step=2)])  # type: ignore[list-item]
    assert out.count("<details") == 2


# ---------------------------------------------------------------------------
# HTML escaping (XSS safety)
# ---------------------------------------------------------------------------


def test_escapes_html_in_tool_name() -> None:
    out = build_html([_step(tool="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_escapes_html_in_result_summary() -> None:
    out = build_html(
        [_step(result_summary='<img src=x onerror="alert(1)">')]
    )
    assert 'onerror="alert(1)"' not in out
    assert "&lt;img" in out


def test_escapes_html_in_url() -> None:
    out = build_html([_step(url="https://x.com/<svg/onload=alert(1)>")])
    assert "<svg/onload=alert(1)>" not in out
    assert "&lt;svg" in out


def test_escapes_html_in_action() -> None:
    out = build_html([_step(tool="browser", action="<b>nav</b>")])
    assert "<b>nav</b>" not in out
    assert "&lt;b&gt;nav" in out


def test_escapes_html_in_title() -> None:
    out = build_html([], title="<script>x</script>")
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;x&lt;/script&gt;" in out


# ---------------------------------------------------------------------------
# Meta line
# ---------------------------------------------------------------------------


def test_meta_singular_step() -> None:
    out = build_html([_step()])
    assert "1 step" in out
    assert "1 steps" not in out


def test_meta_plural_steps() -> None:
    out = build_html([_step(step=1), _step(step=2)])
    assert "2 steps" in out


def test_meta_includes_error_count() -> None:
    out = build_html(
        [
            _step(step=1, is_error=False),
            _step(step=2, is_error=True),
            _step(step=3, is_error=True),
        ]
    )
    assert "2 errors" in out


def test_meta_shows_timestamp_range() -> None:
    out = build_html(
        [
            _step(step=1, ts="2026-05-11T01:00:00+00:00"),
            _step(step=2, ts="2026-05-11T01:05:00+00:00"),
        ]
    )
    assert "2026-05-11T01:00:00+00:00" in out
    assert "2026-05-11T01:05:00+00:00" in out


def test_meta_shows_single_timestamp_when_same() -> None:
    out = build_html(
        [
            _step(step=1, ts="2026-05-11T01:00:00+00:00"),
            _step(step=2, ts="2026-05-11T01:00:00+00:00"),
        ]
    )
    # Should not show the arrow when start == end
    assert "→" not in out
    assert "2026-05-11T01:00:00+00:00" in out


# ---------------------------------------------------------------------------
# Missing fields don't crash
# ---------------------------------------------------------------------------


def test_missing_step_renders_question_mark() -> None:
    out = build_html([{"tool": "shell", "is_error": False}])
    assert "#?" in out


def test_missing_tool_renders_unknown() -> None:
    out = build_html([{"step": 1, "is_error": False}])
    assert "&lt;unknown&gt;" in out


def test_iterables_work_too() -> None:
    """Generator input should work, not just list."""

    def gen():
        yield _step(step=1)
        yield _step(step=2)

    out = build_html(gen())
    assert out.count("<details") == 2


# ===========================================================================
# Stage 2 — audit merge + image rendering + CLI
# ===========================================================================

import json
from pathlib import Path

import pytest

from openmimi.replay import (
    _build_arg_parser,
    _cli,
    _looks_like_image,
    _read_jsonl,
    load_audit_session,
    load_episodic_session,
    merge_records,
    render_session,
)


# ---------------------------------------------------------------------------
# _looks_like_image
# ---------------------------------------------------------------------------


def test_looks_like_image_accepts_common_extensions() -> None:
    for p in ("a.png", "b.JPG", "c.jpeg", "d.gif", "e.webp", "f.svg"):
        assert _looks_like_image(p), p


def test_looks_like_image_rejects_other_extensions() -> None:
    for p in ("doc.pdf", "x.txt", "noext", "data.tar.gz"):
        assert not _looks_like_image(p), p


# ---------------------------------------------------------------------------
# _read_jsonl
# ---------------------------------------------------------------------------


def test_read_jsonl_skips_blanks_and_malformed(tmp_path: Path) -> None:
    f = tmp_path / "x.jsonl"
    f.write_text(
        '{"a":1}\n'
        "\n"
        "not json\n"
        '{"b":2}\n'
        "[1,2,3]\n",  # arrays dropped (not dict)
        encoding="utf-8",
    )
    rows = _read_jsonl(f)
    assert rows == [{"a": 1}, {"b": 2}]


def test_read_jsonl_missing_file_returns_empty(tmp_path: Path) -> None:
    rows = _read_jsonl(tmp_path / "nope.jsonl")
    assert rows == []


# ---------------------------------------------------------------------------
# load_audit_session
# ---------------------------------------------------------------------------


def test_load_audit_session_returns_records(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "sid.jsonl").write_text(
        '{"step":1,"tool":"shell"}\n{"step":2,"tool":"browser"}\n',
        encoding="utf-8",
    )
    rows = load_audit_session("sid", audit_dir=audit_dir)
    assert [r["step"] for r in rows] == [1, 2]


def test_load_audit_session_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_audit_session("missing", audit_dir=tmp_path) == []


# ---------------------------------------------------------------------------
# load_episodic_session
# ---------------------------------------------------------------------------


def test_load_episodic_session_walks_month_dirs(tmp_path: Path) -> None:
    base = tmp_path / "episodic"
    (base / "2026-05").mkdir(parents=True)
    (base / "2026-06").mkdir()
    (base / "2026-05" / "sid.jsonl").write_text(
        '{"step":1,"ts":"2026-05-31T23:59:00+00:00","tool":"shell"}\n',
        encoding="utf-8",
    )
    (base / "2026-06" / "sid.jsonl").write_text(
        '{"step":2,"ts":"2026-06-01T00:01:00+00:00","tool":"browser"}\n',
        encoding="utf-8",
    )
    rows = load_episodic_session("sid", episodic_dir=base)
    assert [r["step"] for r in rows] == [1, 2]


def test_load_episodic_session_missing_dir(tmp_path: Path) -> None:
    assert load_episodic_session("sid", episodic_dir=tmp_path / "nope") == []


def test_load_episodic_session_sorts_by_step(tmp_path: Path) -> None:
    base = tmp_path / "episodic"
    (base / "2026-05").mkdir(parents=True)
    (base / "2026-05" / "sid.jsonl").write_text(
        '{"step":3,"ts":"t3"}\n{"step":1,"ts":"t1"}\n{"step":2,"ts":"t2"}\n',
        encoding="utf-8",
    )
    rows = load_episodic_session("sid", episodic_dir=base)
    assert [r["step"] for r in rows] == [1, 2, 3]


# ---------------------------------------------------------------------------
# merge_records
# ---------------------------------------------------------------------------


def test_merge_records_episodic_only() -> None:
    ep = [{"step": 1, "tool": "shell"}, {"step": 2, "tool": "browser"}]
    merged = merge_records(ep, [])
    assert [r["step"] for r in merged] == [1, 2]
    assert merged[0]["tool"] == "shell"


def test_merge_records_audit_only() -> None:
    au = [{"step": 1, "tool_input": {"cmd": "ls"}, "duration_ms": 12}]
    merged = merge_records([], au)
    assert len(merged) == 1
    assert merged[0]["tool_input"] == {"cmd": "ls"}
    assert merged[0]["duration_ms"] == 12


def test_merge_records_patches_audit_fields_into_episodic() -> None:
    ep = [{"step": 1, "tool": "shell", "result_summary": "ok"}]
    au = [
        {
            "step": 1,
            "tool": "shell",
            "tool_input": {"cmd": "ls"},
            "image_path": "screens/sid/step_1.png",
            "duration_ms": 47,
        }
    ]
    merged = merge_records(ep, au)
    assert len(merged) == 1
    r = merged[0]
    assert r["result_summary"] == "ok"  # episodic kept
    assert r["tool_input"] == {"cmd": "ls"}
    assert r["image_path"] == "screens/sid/step_1.png"
    assert r["duration_ms"] == 47


def test_merge_records_audit_doesnt_clobber_episodic_summary() -> None:
    """Episodic is the richer source — audit fields like result_summary
    on the same step shouldn't overwrite it."""
    ep = [{"step": 1, "tool": "shell", "result_summary": "episodic-summary"}]
    au = [
        {
            "step": 1,
            "tool": "shell",
            "result_summary": "audit-summary",
            "tool_input": {"cmd": "ls"},
        }
    ]
    merged = merge_records(ep, au)
    assert merged[0]["result_summary"] == "episodic-summary"
    assert merged[0]["tool_input"] == {"cmd": "ls"}


def test_merge_records_backfills_error_code_when_missing() -> None:
    ep = [{"step": 1, "is_error": True}]  # no error_code
    au = [{"step": 1, "is_error": True, "error_code": "NETWORK_TIMEOUT"}]
    merged = merge_records(ep, au)
    assert merged[0]["error_code"] == "NETWORK_TIMEOUT"


def test_merge_records_skips_rows_without_int_step() -> None:
    ep = [{"step": 1, "tool": "shell"}, {"tool": "no-step"}]
    au = [{"step": "two", "tool": "x"}, {"step": True}]  # str + bool dropped
    merged = merge_records(ep, au)
    assert [r["step"] for r in merged] == [1]


def test_merge_records_union_when_steps_differ() -> None:
    ep = [{"step": 1, "tool": "a"}]
    au = [{"step": 2, "tool": "b"}]
    merged = merge_records(ep, au)
    assert [r["step"] for r in merged] == [1, 2]


def test_merge_records_output_sorted_by_step() -> None:
    ep = [{"step": 3}, {"step": 1}]
    au = [{"step": 2}]
    merged = merge_records(ep, au)
    assert [r["step"] for r in merged] == [1, 2, 3]


# ---------------------------------------------------------------------------
# build_html — image + duration rendering
# ---------------------------------------------------------------------------


def test_image_path_renders_img_tag() -> None:
    out = build_html(
        [_step(step=1, image_path="screens/sid/step_1.png")]
    )
    assert '<img class="screenshot"' in out
    assert 'src="screens/sid/step_1.png"' in out


def test_image_path_non_image_extension_skipped() -> None:
    out = build_html([_step(step=1, image_path="report.pdf")])
    assert "<img" not in out
    # path still appears in JSON payload? No — image_path is excluded
    # from the JSON dump; non-image extensions just don't render.
    assert "report.pdf" not in out


def test_duration_ms_renders_in_summary() -> None:
    out = build_html([_step(step=1, duration_ms=123)])
    assert "123ms" in out


def test_duration_ms_only_when_numeric() -> None:
    out = build_html([_step(step=1, duration_ms="slow")])
    assert "slowms" not in out


def test_image_path_excluded_from_json_payload() -> None:
    out = build_html(
        [_step(step=1, image_path="screens/x.png", extra="kept")]
    )
    # JSON payload should still show 'extra' but not the image_path key
    assert "extra" in out
    # image_path key text shouldn't appear as a JSON key (it's pulled
    # out into the <img> tag).
    json_blocks = out.split("<pre>")
    # Find any pre block that contains "image_path" as a JSON key —
    # there shouldn't be one.
    for block in json_blocks:
        assert '"image_path"' not in block


def test_image_alt_includes_step_number() -> None:
    out = build_html([_step(step=7, image_path="screens/step_7.png")])
    assert 'alt="step #7 screenshot"' in out


def test_image_src_is_escaped() -> None:
    out = build_html(
        [_step(step=1, image_path='screens/"injection".png')]
    )
    assert 'screens/"injection".png' not in out
    assert "&quot;injection&quot;" in out


# ---------------------------------------------------------------------------
# render_session
# ---------------------------------------------------------------------------


def _seed_session(
    tmp_path: Path,
    *,
    episodic: list[dict] | None = None,
    audit: list[dict] | None = None,
    session_id: str = "sid",
) -> tuple[Path, Path]:
    """Write episodic + audit JSONL fixtures and return (audit_dir, ep_dir)."""
    audit_dir = tmp_path / "audit"
    ep_dir = tmp_path / "episodic"
    audit_dir.mkdir()
    (ep_dir / "2026-05").mkdir(parents=True)
    if audit:
        (audit_dir / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in audit) + "\n",
            encoding="utf-8",
        )
    if episodic:
        (ep_dir / "2026-05" / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in episodic) + "\n",
            encoding="utf-8",
        )
    return audit_dir, ep_dir


def test_render_session_combines_both_sources(tmp_path: Path) -> None:
    audit_dir, ep_dir = _seed_session(
        tmp_path,
        episodic=[
            {"step": 1, "session_id": "sid", "tool": "shell", "result_summary": "hi"}
        ],
        audit=[
            {
                "step": 1,
                "session_id": "sid",
                "tool": "shell",
                "tool_input": {"cmd": "echo hi"},
                "duration_ms": 5,
            }
        ],
    )
    out = render_session("sid", audit_dir=audit_dir, episodic_dir=ep_dir)
    assert "<!doctype html>" in out
    assert "echo hi" in out
    assert "5ms" in out
    assert "Session sid" in out


def test_render_session_episodic_only(tmp_path: Path) -> None:
    audit_dir, ep_dir = _seed_session(
        tmp_path,
        episodic=[
            {"step": 1, "session_id": "sid", "tool": "shell"},
            {"step": 2, "session_id": "sid", "tool": "browser"},
        ],
    )
    out = render_session("sid", audit_dir=audit_dir, episodic_dir=ep_dir)
    assert out.count("<details") == 2


def test_render_session_audit_only(tmp_path: Path) -> None:
    audit_dir, ep_dir = _seed_session(
        tmp_path,
        audit=[{"step": 1, "tool": "shell", "tool_input": {"cmd": "x"}}],
    )
    out = render_session("sid", audit_dir=audit_dir, episodic_dir=ep_dir)
    assert out.count("<details") == 1
    assert "cmd" in out


def test_render_session_unknown_id_returns_empty_page(tmp_path: Path) -> None:
    out = render_session(
        "no-such-sid", audit_dir=tmp_path, episodic_dir=tmp_path
    )
    assert "No step records" in out


# ---------------------------------------------------------------------------
# CLI argparse smoke
# ---------------------------------------------------------------------------


def test_arg_parser_requires_session_id() -> None:
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_arg_parser_defaults_have_expected_paths() -> None:
    args = _build_arg_parser().parse_args(["sid"])
    assert args.session_id == "sid"
    assert args.out is None
    assert str(args.audit_dir).endswith("audit")
    assert "episodic" in str(args.episodic_dir)


def test_arg_parser_accepts_overrides() -> None:
    args = _build_arg_parser().parse_args(
        [
            "sid",
            "--out",
            "/tmp/x.html",
            "--audit-dir",
            "/a",
            "--episodic-dir",
            "/e",
            "--title",
            "T",
        ]
    )
    assert str(args.out) == "/tmp/x.html" or str(args.out).endswith("x.html")
    assert args.title == "T"


def test_cli_writes_html_file(tmp_path: Path) -> None:
    audit_dir, ep_dir = _seed_session(
        tmp_path,
        episodic=[
            {"step": 1, "session_id": "sid", "tool": "shell", "result_summary": "ok"}
        ],
    )
    out_path = tmp_path / "out.html"
    rc = _cli(
        [
            "sid",
            "--out",
            str(out_path),
            "--audit-dir",
            str(audit_dir),
            "--episodic-dir",
            str(ep_dir),
        ]
    )
    assert rc == 0
    text = out_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in text
    assert "Session sid" in text


def test_cli_default_out_path_uses_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir, ep_dir = _seed_session(
        tmp_path,
        episodic=[{"step": 1, "session_id": "sid", "tool": "shell"}],
    )
    monkeypatch.chdir(tmp_path)
    rc = _cli(
        [
            "sid",
            "--audit-dir",
            str(audit_dir),
            "--episodic-dir",
            str(ep_dir),
        ]
    )
    assert rc == 0
    assert (tmp_path / "replay_sid.html").is_file()
