"""Tests for replay.build_html (roadmap #11 stage 1)."""
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
