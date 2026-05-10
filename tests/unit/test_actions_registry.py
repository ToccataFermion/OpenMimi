"""Tests for the actions/ registry that backs AgentBrowserTool dispatch."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def test_registry_has_navigation_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in ("navigate", "back", "forward", "reload"):
        assert name in registered, f"missing migrated action: {name}"


def test_registry_has_interaction_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "click",
        "right_click",
        "double_click",
        "check",
        "uncheck",
        "type",
        "fill",
        "react_fill",
        "press",
        "key_combo",
        "hover",
    ):
        assert name in registered, f"missing migrated action: {name}"


def test_get_returns_callable_for_known_action() -> None:
    from openmimi.tools import actions

    handler = actions.get("navigate")
    assert handler is not None
    assert callable(handler)


def test_get_returns_none_for_unknown_action() -> None:
    from openmimi.tools import actions

    assert actions.get("definitely_not_an_action") is None


@pytest.mark.asyncio
async def test_navigate_handler_drives_engine_when_started() -> None:
    """Migrated navigate must call open + snapshot via engine._exec."""
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []
    refresh_calls = 0

    class _FakeEngine:
        _started = True
        _tabs: list[Any] = []
        _active_tab_index = 1

        async def _start_browser(self, _url: str | None = None) -> None:  # pragma: no cover
            raise AssertionError("must not start when already running")

        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            return SimpleNamespace(stdout='{"text":"page loaded"}')

        async def _refresh_tabs(self) -> None:
            nonlocal refresh_calls
            refresh_calls += 1

        async def _take_screenshot(self) -> str | None:
            return None

        def _parse_snapshot(self, _raw: str) -> tuple[str, dict[str, Any]]:
            return ("page loaded", {})

    handler = actions.get("navigate")
    assert handler is not None

    result = await handler(_FakeEngine(), {"url": "https://example.com"})

    assert result.is_error is False
    assert "Navigated to https://example.com" in result.output
    assert ("open", "https://example.com", "--json") in exec_calls
    assert refresh_calls >= 1


@pytest.mark.asyncio
async def test_back_handler_returns_navigation_text() -> None:
    from openmimi.tools import actions

    exec_args: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_args.append(args)
            return SimpleNamespace(stdout="back-ok")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("back")(_Engine(), {})
    assert "Navigated back" in result.output
    assert exec_args == [("back", "--json")]


@pytest.mark.asyncio
async def test_forward_and_reload_call_correct_subcommand() -> None:
    from openmimi.tools import actions

    captured: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args[0])
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    eng = _Engine()
    await actions.get("forward")(eng, {})
    await actions.get("reload")(eng, {})
    assert captured == ["forward", "reload"]


@pytest.mark.asyncio
async def test_click_handler_returns_clicked_text_and_screenshot() -> None:
    """click via ref must call ``click`` subcommand and refresh tabs."""
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []
    switched = 0

    class _Engine:
        _tabs: list[Any] = []
        _active_tab_index = 0

        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            return SimpleNamespace(stdout='{"clicked":"button"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"clicked": "button"}

        async def _switch_to_newest_tab(self) -> None:
            nonlocal switched
            switched += 1

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("click")(_Engine(), {"ref": "@e3"})
    assert result.is_error is False
    assert "Clicked button" in result.output
    assert ("click", "@e3", "--json") in exec_calls
    assert switched == 1


@pytest.mark.asyncio
async def test_click_requires_ref_or_target_text() -> None:
    from openmimi.tools import actions

    class _Engine:
        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("click")(_Engine(), {})
    assert "click requires" in result.output


@pytest.mark.asyncio
async def test_type_handler_uses_find_when_target_text_given() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("type")(
        _Engine(), {"target_text": "Username", "value": "alice"}
    )
    assert "Typed 5 character(s)" in result.output
    assert captured == [
        ("find", "text", "Username", "type", "alice", "--json")
    ]


@pytest.mark.asyncio
async def test_press_handler_passes_key_to_exec() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("press")(_Engine(), {"key": "Tab"})
    assert "Pressed Tab" in result.output
    assert captured == [("press", "Tab", "--json")]


@pytest.mark.asyncio
async def test_right_click_uses_box_then_mouse_sequence() -> None:
    """right_click must compute box center, then drive move/down/up via ``mouse``."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            if args[:2] == ("get", "box"):
                return SimpleNamespace(
                    stdout='{"box":{"x":10,"y":20,"width":40,"height":20}}'
                )
            return SimpleNamespace(stdout="ok")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"box": {"x": 10, "y": 20, "width": 40, "height": 20}}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("right_click")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "Right-clicked @e1 at (30, 30)" in result.output
    # ``get box`` first, then mouse move/down/up at center (30, 30)
    assert captured[0] == ("get", "box", "@e1", "--json")
    assert ("mouse", "move", "30", "30", "--json") in captured
    assert ("mouse", "down", "right", "--json") in captured
    assert ("mouse", "up", "right", "--json") in captured


def test_registry_has_scroll_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in ("scroll", "human_scroll", "scroll_until", "scroll_into_view"):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_scroll_handler_calls_subcommand() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("scroll")(
        _Engine(), {"direction": "down", "amount": 250}
    )
    assert result.is_error is False
    assert "Scrolled down 250px" in result.output
    assert captured == [("scroll", "down", "250", "--json")]


@pytest.mark.asyncio
async def test_scroll_until_returns_immediately_when_box_present() -> None:
    """If get box returns a box on the very first probe, scroll_until exits without scrolling."""
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            return SimpleNamespace(stdout='{"box":{"x":1,"y":2,"width":3,"height":4}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"box": {"x": 1, "y": 2, "width": 3, "height": 4}}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("scroll_until")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "Found after scrolling 0 steps: @e1" in result.output
    # Only the get box probe should have run; no scroll subcommand.
    assert exec_calls == [("get", "box", "@e1", "--json")]


@pytest.mark.asyncio
async def test_scroll_until_requires_target() -> None:
    from openmimi.tools import actions

    class _Engine:
        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("scroll_until")(_Engine(), {})
    assert result.is_error is True
    assert "scroll_until requires" in result.output


@pytest.mark.asyncio
async def test_scroll_into_view_uses_eval() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true,"tag":"DIV","text":"hello"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "tag": "DIV", "text": "hello"}}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("scroll_into_view")(_Engine(), {"ref": "@e2"})
    assert result.is_error is False
    assert "Scrolled into view" in result.output
    # First positional arg must be ``eval`` and the JS payload must mention scrollIntoView.
    assert captured[0][0] == "eval"
    assert "scrollIntoView" in captured[0][1]


def test_registry_has_extract_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "snapshot",
        "page_source",
        "get_url",
        "get_title",
        "get_attribute",
        "set_attribute",
        "get_property",
        "extract",
        "get_box",
        "is_visible",
        "visual_locate",
    ):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_get_url_returns_window_location() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":"https://example.com/page"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": "https://example.com/page"}

    result = await actions.get("get_url")(_Engine(), {})
    assert result.is_error is False
    assert result.output == "https://example.com/page"
    # Must call eval with window.location.href expression.
    assert captured[0][0] == "eval"
    assert "window.location.href" in captured[0][1]


@pytest.mark.asyncio
async def test_get_title_returns_document_title() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":"Hello"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": "Hello"}

    result = await actions.get("get_title")(_Engine(), {})
    assert result.is_error is False
    assert result.output == "Hello"
    assert captured[0][0] == "eval"
    assert "document.title" in captured[0][1]


@pytest.mark.asyncio
async def test_get_box_calls_get_box_subcommand() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"box":{"x":1,"y":2,"width":10,"height":20}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"box": {"x": 1, "y": 2, "width": 10, "height": 20}}

    result = await actions.get("get_box")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "x" in result.output  # JSON dump includes the key
    assert captured == [("get", "box", "@e1", "--json")]


@pytest.mark.asyncio
async def test_get_box_requires_selector() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("get_box")(_Engine(), {})
    assert result.is_error is True
    assert "get_box requires" in result.output


@pytest.mark.asyncio
async def test_extract_get_text_truncates_to_4000() -> None:
    """The default 'get text' instruction returns innerText capped at 4000 chars."""
    from openmimi.tools import actions

    big_text = "a" * 5000

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout='{"result":"' + big_text + '"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": big_text}

    result = await actions.get("extract")(_Engine(), {"instruction": "get text"})
    assert result.is_error is False
    assert len(result.output) == 4000


@pytest.mark.asyncio
async def test_get_attribute_requires_attribute_name() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("get_attribute")(_Engine(), {"ref": "@e1"})
    assert result.is_error is True
    assert "attribute_name" in result.output


@pytest.mark.asyncio
async def test_is_visible_dispatches_eval_with_ref_selector() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(
                stdout='{"result":{"visible":true,"tag":"BUTTON","rect":{"x":1,"y":2,"width":3,"height":4}}}'
            )

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {
                "result": {
                    "visible": True,
                    "tag": "BUTTON",
                    "rect": {"x": 1, "y": 2, "width": 3, "height": 4},
                }
            }

    result = await actions.get("is_visible")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "Visible: True" in result.output
    assert captured[0][0] == "eval"
    # The JS payload should mention getBoundingClientRect since it does that probe.
    assert "getBoundingClientRect" in captured[0][1]


def test_registry_has_wait_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "wait",
        "wait_for",
        "wait_for_disappear",
        "wait_for_navigation",
        "wait_for_network_idle",
    ):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_wait_handler_calls_wait_subcommand() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

    result = await actions.get("wait")(_Engine(), {"milliseconds": 250})
    assert result.is_error is False
    assert "Waited 250ms" in result.output
    assert captured == [("wait", "250", "--json")]


@pytest.mark.asyncio
async def test_wait_for_returns_immediately_when_box_present() -> None:
    """wait_for must exit on the first probe if get box returns a box."""
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            return SimpleNamespace(stdout='{"box":{"x":1,"y":2,"width":3,"height":4}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"box": {"x": 1, "y": 2, "width": 3, "height": 4}}

    result = await actions.get("wait_for")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "Element found: @e1" in result.output
    assert exec_calls == [("get", "box", "@e1", "--json")]


@pytest.mark.asyncio
async def test_wait_for_requires_target() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("wait_for")(_Engine(), {})
    assert result.is_error is True
    assert "wait_for requires" in result.output


@pytest.mark.asyncio
async def test_wait_for_disappear_returns_when_box_missing() -> None:
    """wait_for_disappear must exit on the first probe if get box returns no box."""
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout='{}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {}

    result = await actions.get("wait_for_disappear")(_Engine(), {"ref": "@e1"})
    assert result.is_error is False
    assert "disappeared" in result.output


@pytest.mark.asyncio
async def test_wait_for_navigation_detects_url_change() -> None:
    """wait_for_navigation must return as soon as the URL transitions."""
    from openmimi.tools import actions

    urls = ["https://a.example/", "https://a.example/", "https://b.example/"]
    parsed_results = ["https://a.example/", "https://a.example/", "https://b.example/"]
    counter = {"i": 0}

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            i = counter["i"]
            counter["i"] = i + 1
            return SimpleNamespace(stdout=urls[min(i, len(urls) - 1)])

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            i = min(counter["i"] - 1, len(parsed_results) - 1)
            return {"result": parsed_results[i]}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("wait_for_navigation")(
        _Engine(), {"timeout_ms": 5000, "interval_ms": 1}
    )
    assert result.is_error is False
    assert "Navigation detected" in result.output
    assert "https://a.example/" in result.output
    assert "https://b.example/" in result.output


@pytest.mark.asyncio
async def test_wait_for_network_idle_installs_hook_and_returns_when_idle() -> None:
    """First eval is the install JS; subsequent polls return idle=True immediately."""
    from openmimi.tools import actions

    captured: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout="ok")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"idle": True, "idleFor": 2500, "count": 0}}

    result = await actions.get("wait_for_network_idle")(
        _Engine(), {"idle_duration_ms": 100, "timeout_ms": 5000, "interval_ms": 1}
    )
    assert result.is_error is False
    assert "Network idle" in result.output
    # First call should install the hook (mentions __openmimi_network_idle_hooked).
    assert "__openmimi_network_idle_hooked" in captured[0]


def test_registry_has_tab_session_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "clipboard",
        "tab_list",
        "tab_switch",
        "tab_new",
        "tab_close",
        "save_session",
        "load_session",
        "clear_cache",
    ):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_clipboard_read_returns_text() -> None:
    """clipboard read forwards to the daemon clipboard subcommand and unwraps text."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"text":"hello"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"text": "hello"}

    result = await actions.get("clipboard")(_Engine(), {"clipboard_action": "read"})
    assert result.is_error is False
    assert "Clipboard: hello" in result.output
    assert captured == [("clipboard", "read", "--json")]


@pytest.mark.asyncio
async def test_clipboard_write_reports_chars_written() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

    result = await actions.get("clipboard")(
        _Engine(), {"clipboard_action": "write", "clipboard_text": "abc"}
    )
    assert result.is_error is False
    assert "Wrote 3 chars" in result.output
    assert captured == [("clipboard", "write", "abc", "--json")]


@pytest.mark.asyncio
async def test_tab_list_returns_open_tabs_summary() -> None:
    from openmimi.tools import actions

    refresh_calls = 0

    class _Engine:
        _tabs = [{"id": "t1", "url": "https://a/"}, {"id": "t2", "url": "https://b/"}]
        _active_tab_index = 1

        async def _refresh_tabs(self) -> None:
            nonlocal refresh_calls
            refresh_calls += 1

    result = await actions.get("tab_list")(_Engine(), {})
    assert result.is_error is False
    assert "Tab 1: https://a/" in result.output
    assert "Tab 2: https://b/" in result.output
    assert "Active tab: 1" in result.output
    assert refresh_calls == 1
    assert result.details is not None
    assert result.details["active_tab"] == 1


@pytest.mark.asyncio
async def test_tab_new_opens_tab_via_subcommand() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        _tabs: list[Any] = []
        _active_tab_index = 1

        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _refresh_tabs(self) -> None:
            return None

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("tab_new")(_Engine(), {"url": "https://example.com"})
    assert result.is_error is False
    assert "New tab opened: https://example.com" in result.output
    assert captured == [("tab", "new", "https://example.com", "--json")]


@pytest.mark.asyncio
async def test_save_session_requires_file_path() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("save_session")(_Engine(), {})
    assert result.is_error is True
    assert "file_path" in result.output


@pytest.mark.asyncio
async def test_load_session_requires_file_path() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("load_session")(_Engine(), {})
    assert result.is_error is True
    assert "file_path" in result.output


@pytest.mark.asyncio
async def test_clear_cache_runs_eval_and_reports_ok() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True}}

    result = await actions.get("clear_cache")(_Engine(), {})
    assert result.is_error is False
    assert "Cache cleared" in result.output
    # First arg must be eval and the JS payload should mention localStorage.clear()
    assert captured[0][0] == "eval"
    assert "localStorage.clear()" in captured[0][1]


def test_registry_has_network_cdp_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "cdp",
        "screenshot",
        "network_log",
        "network_modify",
        "storage",
        "pdf",
        "console",
    ):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_cdp_handler_unwraps_result_value() -> None:
    """cdp must wrap caller args into __openmimi_cdp_send and surface result."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true,"result":{"data":42}}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "result": {"data": 42}}}

    result = await actions.get("cdp")(
        _Engine(), {"cdp_method": "Runtime.evaluate", "cdp_params": {"expression": "1+1"}}
    )
    assert result.is_error is False
    # Output is JSON-encoded result_value (full {ok, result})
    assert '"data": 42' in result.output
    assert captured[0][0] == "eval"
    assert "Runtime.evaluate" in captured[0][1]


@pytest.mark.asyncio
async def test_cdp_requires_cdp_method() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("cdp")(_Engine(), {})
    assert result.is_error is True
    assert "cdp_method" in result.output


@pytest.mark.asyncio
async def test_screenshot_returns_image_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """screenshot must call engine._take_screenshot and return its bytes as base64."""
    from openmimi.tools.actions import network_cdp

    monkeypatch.setattr(network_cdp, "screenshots_disabled", lambda: False)

    class _Engine:
        async def _take_screenshot(self, path_override: str | None = None, annotate: bool = False) -> str | None:
            return "fake_b64"

    from openmimi.tools import actions

    result = await actions.get("screenshot")(_Engine(), {})
    assert result.is_error is False
    assert result.base64_image == "fake_b64"
    assert "Screenshot taken" in result.output


@pytest.mark.asyncio
async def test_screenshot_blocked_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from openmimi.tools.actions import network_cdp

    monkeypatch.setattr(network_cdp, "screenshots_disabled", lambda: True)

    class _Engine:
        async def _take_screenshot(self, **_kw: Any) -> str | None:  # pragma: no cover
            raise AssertionError("must not call screenshot when disabled")

    from openmimi.tools import actions

    result = await actions.get("screenshot")(_Engine(), {})
    assert result.is_error is False
    assert "Screenshots disabled" in result.output
    assert result.base64_image is None


@pytest.mark.asyncio
async def test_network_log_installs_hook_then_reads_requests() -> None:
    """First eval installs interceptor; second returns captured requests."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"requests":[{"url":"x"}],"count":1}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"requests": [{"url": "x"}], "count": 1}

    result = await actions.get("network_log")(_Engine(), {"duration_ms": 0, "filter": "x"})
    assert result.is_error is False
    # First call installs interceptor (mentions __openmimi_network_hooked).
    assert "__openmimi_network_hooked" in payloads[0]
    # Output must include captured request count.
    assert '"count"' in result.output
    assert result.details is not None
    assert result.details["requests"] == [{"url": "x"}]


@pytest.mark.asyncio
async def test_network_modify_inject_headers_requires_headers() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("network_modify")(
        _Engine(), {"modify_action": "inject_headers"}
    )
    assert result.is_error is True
    assert "headers" in result.output


@pytest.mark.asyncio
async def test_network_modify_user_agent_calls_eval() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true,"method":"cdp"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "method": "cdp"}}

    result = await actions.get("network_modify")(
        _Engine(), {"modify_action": "user_agent", "user_agent": "Mozilla/Test"}
    )
    assert result.is_error is False
    assert "User-Agent set to" in result.output
    assert captured[0][0] == "eval"
    assert "Network.setUserAgentOverride" in captured[0][1]


@pytest.mark.asyncio
async def test_storage_localstorage_set_writes_via_eval() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True}}

    result = await actions.get("storage")(
        _Engine(),
        {
            "storage_action": "set",
            "storage_type": "localStorage",
            "storage_key": "k",
            "storage_value": "v",
        },
    )
    assert result.is_error is False
    assert captured[0][0] == "eval"
    assert "localStorage.setItem" in captured[0][1]


@pytest.mark.asyncio
async def test_storage_set_localstorage_requires_key() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("storage")(
        _Engine(), {"storage_action": "set", "storage_type": "localStorage"}
    )
    assert result.is_error is True
    assert "storage_key" in result.output


@pytest.mark.asyncio
async def test_storage_cookies_get_uses_cdp_first() -> None:
    """Cookie reads must call CDP Network.getAllCookies in the first eval payload."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"method":"cdp","cookies":[]}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "method": "cdp", "cookies": []}}

    result = await actions.get("storage")(
        _Engine(), {"storage_action": "get", "storage_type": "cookies"}
    )
    assert result.is_error is False
    assert "Network.getAllCookies" in payloads[0]
    assert result.details is not None
    assert result.details["method"] == "cdp"


@pytest.mark.asyncio
async def test_pdf_requires_file_path() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("pdf")(_Engine(), {})
    assert result.is_error is True
    assert "file_path" in result.output


@pytest.mark.asyncio
async def test_pdf_happy_path_reports_saved() -> None:
    """When CDP printToPDF reports ok, handler returns 'PDF saved to ...'."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout='{"result":{"ok":true,"path":"/tmp/x.pdf"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "path": "/tmp/x.pdf"}}

    result = await actions.get("pdf")(_Engine(), {"file_path": "/tmp/x.pdf"})
    assert result.is_error is False
    assert "PDF saved to /tmp/x.pdf" in result.output
    # Must call eval with Page.printToPDF in payload.
    assert captured[0][0] == "eval"
    assert "Page.printToPDF" in captured[0][1]


@pytest.mark.asyncio
async def test_console_installs_hook_then_reads_logs() -> None:
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"count":1,"logs":[{"level":"error","message":"boom"}]}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"count": 1, "logs": [{"level": "error", "message": "boom"}]}}

    result = await actions.get("console")(_Engine(), {"console_level": "error"})
    assert result.is_error is False
    # First call installs hook (mentions __openmimi_console_logs).
    assert "__openmimi_console_logs" in payloads[0]
    assert '"level": "error"' in result.output

