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

