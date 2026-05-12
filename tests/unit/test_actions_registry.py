"""Tests for the actions/ registry that backs AgentBrowserTool dispatch."""
from __future__ import annotations

import json
import time
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
    """type with target_text: focus via find+click, then keyboard.type.

    The naive `find <loc> <val> type <text>` argv path is broken in
    agent-browser — repro on the CLI returns `Unknown subaction: type`
    (cycles 65 / 81 / 89). The handler must take the click + keyboard
    path instead.
    """
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
        ("find", "text", "Username", "click", "--json"),
        ("keyboard", "type", "alice", "--json"),
    ]


@pytest.mark.asyncio
async def test_fill_handler_uses_click_clear_keyboard_when_target_text_given() -> None:
    """fill with target_text: focus, select-all, then keyboard.type.

    Same root cause as `type`: agent-browser's
    `find <loc> <val> fill <text>` drops the trailing text and returns
    "Missing 'value' for fill subaction" once the element is actually
    found (CLI-repro'd cycles 65 / 81 / 89). The Control+a step preserves
    fill's clear-then-type semantics.
    """
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("fill")(
        _Engine(),
        {"target_text": "利用 DuckDuckGo 进行搜索", "value": "OpenAI"},
    )
    assert "Filled with 6 character(s)" in result.output
    assert captured == [
        ("find", "text", "利用 DuckDuckGo 进行搜索", "click", "--json"),
        ("press", "Control+a", "--json"),
        ("keyboard", "type", "OpenAI", "--json"),
    ]


@pytest.mark.asyncio
async def test_fill_handler_with_ref_uses_direct_fill() -> None:
    """fill with ref: direct `fill <ref> <value>` (no workaround needed).

    The agent-browser bug only affects the chained `find ... fill <text>`
    form. The plain `fill <ref> <text>` path works correctly.
    """
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

        async def _take_screenshot(self) -> str | None:
            return None

    await actions.get("fill")(
        _Engine(), {"ref": "e22", "value": "OpenAI"}
    )
    assert captured == [("fill", "e22", "OpenAI", "--json")]


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
async def test_extract_text_alias_resolves_to_get_text() -> None:
    """'text' is accepted as an alias for 'get text' so the LLM doesn't have
    to remember the two-word form. Reproduced from goldset cycle 80."""
    from openmimi.tools import actions

    body_text = "hello world"

    class _Engine:
        captured_js: list[str] = []

        async def _exec(self, *args: str, **_kw: Any) -> Any:
            self.captured_js.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":"' + body_text + '"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": body_text}

    engine = _Engine()
    result = await actions.get("extract")(engine, {"instruction": "text"})
    assert result.is_error is False
    assert result.output == body_text
    assert engine.captured_js == ["document.body.innerText"]


@pytest.mark.asyncio
async def test_extract_unknown_instruction_returns_error_with_options() -> None:
    """Unknown instructions surface as errors that list the valid options
    rather than silently falling back to a generic page dump (which
    burned a full step in goldset cycle 80 when the LLM passed JS code
    as the instruction)."""
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *_args: str, **_kw: Any) -> Any:
            raise AssertionError("_exec should not be called for unknown instruction")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            raise AssertionError("_parse_data should not be called")

    result = await actions.get("extract")(
        _Engine(), {"instruction": "(() => document.title)()"}
    )
    assert result.is_error is True
    assert "Unknown extract instruction" in result.output
    assert "get text" in result.output
    assert "eval" in result.output  # nudges agent toward the right tool


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
async def test_wait_handler_accepts_duration_ms_alias() -> None:
    """Regression for cycle 25: ``browser_advanced.wait``'s schema advertises
    both ``milliseconds`` and ``duration_ms``. The LLM picked ``duration_ms:
    4000`` and ``duration_ms: 5000``; the handler silently fell back to the
    1000ms default and the audit log claimed the requested wait had run.
    Honor the documented alias."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

    result = await actions.get("wait")(_Engine(), {"duration_ms": 4000})
    assert result.is_error is False
    assert "Waited 4000ms" in result.output
    assert captured == [("wait", "4000", "--json")]


@pytest.mark.asyncio
async def test_wait_handler_accepts_timeout_ms_alias() -> None:
    """``timeout_ms`` is the more natural name for some prompts and shows up
    in other wait handlers — accept it here too so the trio of names all
    behave identically."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="ok")

    result = await actions.get("wait")(_Engine(), {"timeout_ms": 750})
    assert result.is_error is False
    assert "Waited 750ms" in result.output
    assert captured == [("wait", "750", "--json")]


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
async def test_wait_for_navigation_accepts_milliseconds_alias() -> None:
    """Regression: goldset cycle 1 caught the LLM sending ``milliseconds`` instead of
    ``timeout_ms`` for wait_for_navigation. The handler silently fell back to the 10s
    default and the actual requested timeout was ignored. The alias must now be honored.
    """
    from openmimi.tools import actions

    counter = {"i": 0}

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            counter["i"] += 1
            return SimpleNamespace(stdout="")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": "https://stuck.example/"}

        async def _take_screenshot(self) -> str | None:
            return None

    start = time.monotonic()
    result = await actions.get("wait_for_navigation")(
        _Engine(), {"milliseconds": 200, "interval_ms": 50}
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert result.is_error is True
    assert "200ms" in result.output
    assert elapsed_ms < 1500, (
        f"wait_for_navigation honored milliseconds=200 → should bail in well "
        f"under 1.5s, took {elapsed_ms:.0f}ms (likely ignored alias and used default 10s)"
    )


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
async def test_tab_new_reports_new_tab_index() -> None:
    """Regression for cycle 19: `tab_new` must surface the absolute index of
    the newly-opened tab so the LLM can `tab_switch` back to it without
    guessing — important when session-restore has revived stale tabs from
    earlier cycles and the index of "the tab I just opened" is not the
    obvious count of new opens."""
    from openmimi.tools import actions

    class _Engine:
        # Simulate a profile with 13 stale tabs already open; tab_new lands
        # the newest example.org page at index 14.
        _tabs: list[Any] = [{"id": f"t{i}", "url": "stale"} for i in range(14)]
        _active_tab_index = 14

        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout="ok")

        async def _refresh_tabs(self) -> None:
            return None

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("tab_new")(
        _Engine(), {"url": "https://example.org"}
    )
    assert result.is_error is False
    # The absolute index must appear in the output the LLM sees.
    assert "14" in result.output
    assert "https://example.org" in result.output
    # And in details so downstream callers can read it structurally.
    assert result.details["new_tab_index"] == 14
    assert result.details["active_tab"] == 14


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


def test_registry_has_misc_actions() -> None:
    from openmimi.tools import actions

    registered = actions.registered_actions()
    for name in (
        "select",
        "upload",
        "download",
        "eval",
        "batch",
        "drag",
        "mouse",
        "focus",
        "set_viewport",
        "emulate_device",
        "set_timezone",
        "set_locale",
        "set_geolocation",
    ):
        assert name in registered, f"missing migrated action: {name}"


@pytest.mark.asyncio
async def test_select_requires_options() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("select")(_Engine(), {"ref": "@e1"})
    assert result.is_error is True
    assert "options" in result.output


@pytest.mark.asyncio
async def test_upload_requires_file_path() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("upload")(_Engine(), {"ref": "@e1"})
    assert result.is_error is True
    assert "file_path" in result.output


@pytest.mark.asyncio
async def test_download_requires_selector() -> None:
    """download with file_path but no ref/target_text must error."""
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("download")(
        _Engine(), {"file_path": "/tmp/out.bin"}
    )
    assert result.is_error is True
    assert "ref" in result.output or "target_text" in result.output


@pytest.mark.asyncio
async def test_eval_rejects_empty_js() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("eval")(_Engine(), {"js": "   "})
    assert result.is_error is True
    assert "js" in result.output


@pytest.mark.asyncio
async def test_eval_accepts_js_code_as_alias_for_js() -> None:
    """browser_extract's schema historically declared 'js_code'; the backend
    only read 'js' and rejected everything LLMs sent (92% failure rate in
    the 2026-05-11 audit). Accept both so old/new schemas coexist."""
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *_args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout='{"success":true,"data":{"result":"ok"}}')

        def _parse_json(self, _raw: str) -> dict[str, Any]:
            return {"success": True, "data": {"result": "ok"}}

    # Only js_code provided — must succeed via fallback.
    result = await actions.get("eval")(_Engine(), {"js_code": "return 'ok';"})
    assert result.is_error is False, result.output


@pytest.mark.asyncio
async def test_eval_returns_serialised_result_value() -> None:
    """eval must surface data.result as a JSON string when present."""
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *_args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout='{"success":true,"data":{"result":{"answer":42}}}')

        def _parse_json(self, _raw: str) -> dict[str, Any]:
            return {"success": True, "data": {"result": {"answer": 42}}}

    result = await actions.get("eval")(_Engine(), {"js": "(() => ({answer: 42}))()"})
    assert result.is_error is False
    assert "42" in result.output
    assert "answer" in result.output


@pytest.mark.asyncio
async def test_batch_requires_steps() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("batch")(_Engine(), {})
    assert result.is_error is True
    assert "steps" in result.output


@pytest.mark.asyncio
async def test_batch_rejects_non_string_step() -> None:
    """A dict step would be silently corrupted by argv quoting — fail fast instead."""
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("batch")(
        _Engine(), {"steps": ["mouse move 1 2", {"command": "click"}]}
    )
    assert result.is_error is True
    assert "step 1" in result.output
    assert "non-empty string" in result.output


@pytest.mark.asyncio
async def test_batch_rejects_blank_step() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("batch")(_Engine(), {"steps": ["click @e1", "   "]})
    assert result.is_error is True
    assert "step 1" in result.output


@pytest.mark.asyncio
async def test_batch_accepts_token_array_step() -> None:
    """Tolerate token arrays — agent-browser's stdin JSON shape — by joining."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="[]")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("batch")(
        _Engine(),
        {"steps": [["mouse", "move", "100", "200"], "mouse down"]},
    )
    assert result.is_error is False
    # Token array joined into a single argv string, then forwarded with --bail/--json.
    assert captured == [(
        "batch", "--bail", "--json",
        "mouse move 100 200", "mouse down",
    )]


@pytest.mark.asyncio
async def test_batch_forwards_string_steps_to_exec() -> None:
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="[]")

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("batch")(
        _Engine(),
        {"steps": ["mouse move 5 5", "mouse down", "mouse up"]},
    )
    assert result.is_error is False
    assert captured == [(
        "batch", "--bail", "--json",
        "mouse move 5 5", "mouse down", "mouse up",
    )]


@pytest.mark.asyncio
async def test_drag_requires_paired_targets() -> None:
    """drag without ref+to_ref or target_text+to_target_text must error."""
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("drag")(_Engine(), {"ref": "@e1"})
    assert "ref" in result.output and "to_ref" in result.output


@pytest.mark.asyncio
async def test_mouse_move_dispatches_to_subcommand() -> None:
    """mouse move must forward x/y as positional args to engine._exec mouse move."""
    from openmimi.tools import actions

    captured: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            captured.append(args)
            return SimpleNamespace(stdout="{}")

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("mouse")(
        _Engine(), {"mouse_action": "move", "x": 100, "y": 200}
    )
    assert result.is_error is False
    assert captured[0][:4] == ("mouse", "move", "100", "200")


@pytest.mark.asyncio
async def test_set_viewport_requires_dimensions() -> None:
    from openmimi.tools import actions

    class _Engine:
        pass

    result = await actions.get("set_viewport")(_Engine(), {"width": 800})
    assert result.is_error is True
    assert "width" in result.output and "height" in result.output


@pytest.mark.asyncio
async def test_set_viewport_runs_resizeTo_eval() -> None:
    """set_viewport must build a JS payload that calls window.resizeTo."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"width":800,"height":600}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"width": 800, "height": 600}}

    result = await actions.get("set_viewport")(
        _Engine(), {"width": 800, "height": 600}
    )
    assert result.is_error is False
    assert "Viewport set" in result.output
    assert "window.resizeTo(800, 600)" in payloads[0]


@pytest.mark.asyncio
async def test_emulate_device_runs_setDeviceMetricsOverride() -> None:
    """emulate_device CDP path must reference Emulation.setDeviceMetricsOverride."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"method":"cdp","device":"iPhone 14"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "method": "cdp", "device": "iPhone 14"}}

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("emulate_device")(_Engine(), {"device_name": "iPhone 14"})
    assert result.is_error is False
    assert "iPhone 14" in result.output
    assert "Emulation.setDeviceMetricsOverride" in payloads[0]


@pytest.mark.asyncio
async def test_set_timezone_passes_timezoneid() -> None:
    """set_timezone must pass timezoneId via Emulation.setTimezoneOverride."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"timezone":"Asia/Shanghai"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "timezone": "Asia/Shanghai"}}

    result = await actions.get("set_timezone")(_Engine(), {"timezone": "Asia/Shanghai"})
    assert result.is_error is False
    assert "Asia/Shanghai" in result.output
    assert "Emulation.setTimezoneOverride" in payloads[0]
    assert "Asia/Shanghai" in payloads[0]


@pytest.mark.asyncio
async def test_set_locale_passes_locale_string() -> None:
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"locale":"zh-CN"}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "locale": "zh-CN"}}

    result = await actions.get("set_locale")(_Engine(), {"locale": "zh-CN"})
    assert result.is_error is False
    assert "zh-CN" in result.output
    assert "Emulation.setLocaleOverride" in payloads[0]


@pytest.mark.asyncio
async def test_set_geolocation_clear_when_no_coords() -> None:
    """set_geolocation without lat/lon must call Emulation.clearGeolocationOverride."""
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"cleared":true}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "cleared": True}}

    result = await actions.get("set_geolocation")(_Engine(), {})
    assert result.is_error is False
    assert "cleared" in result.output.lower()
    assert "Emulation.clearGeolocationOverride" in payloads[0]


@pytest.mark.asyncio
async def test_set_geolocation_with_coords_sets_override() -> None:
    from openmimi.tools import actions

    payloads: list[str] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            payloads.append(args[1] if len(args) > 1 else "")
            return SimpleNamespace(stdout='{"result":{"ok":true,"lat":31.23,"lon":121.47}}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": {"ok": True, "lat": 31.23, "lon": 121.47}}

    result = await actions.get("set_geolocation")(
        _Engine(), {"latitude": 31.23, "longitude": 121.47, "accuracy": 50}
    )
    assert result.is_error is False
    assert "31.23" in result.output and "121.47" in result.output
    assert "Emulation.setGeolocationOverride" in payloads[0]
    assert "31.23" in payloads[0] and "121.47" in payloads[0]


# ---------------------------------------------------------------------------
# Wave 2 #12 — structured ToolResult payload
#
# These tests pin down the contract that selected handlers populate
# ``ToolResult.structured`` alongside the existing string ``output``. The
# field is for programmatic consumers (planner / sub-agents) so they don't
# have to re-parse the JSON the handler already constructed in-memory.
# ---------------------------------------------------------------------------


def test_tool_result_structured_defaults_to_none() -> None:
    from openmimi.tools.result import ToolResult

    r = ToolResult(output="hi")
    assert r.structured is None


@pytest.mark.asyncio
async def test_extract_get_text_populates_structured() -> None:
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout='{"result":"hello world"}')

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": "hello world"}

    result = await actions.get("extract")(_Engine(), {"instruction": "get text"})
    assert result.structured == {"instruction": "get text", "data": "hello world"}


@pytest.mark.asyncio
async def test_extract_links_populates_structured_with_list() -> None:
    from openmimi.tools import actions

    rows = [
        {"text": "Home", "href": "https://example.com/"},
        {"text": "Docs", "href": "https://example.com/docs"},
    ]

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout=json.dumps({"result": rows}))

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": rows}

    result = await actions.get("extract")(_Engine(), {"instruction": "links"})
    assert result.is_error is False
    assert result.structured is not None
    assert result.structured["instruction"] == "links"
    assert result.structured["data"] == rows


@pytest.mark.asyncio
async def test_get_box_populates_structured_with_box() -> None:
    from openmimi.tools import actions

    box = {"x": 10, "y": 20, "width": 100, "height": 30}

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout=json.dumps({"box": box}))

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"box": box}

    result = await actions.get("get_box")(_Engine(), {"ref": "@e7"})
    assert result.is_error is False
    assert result.structured == {"box": box, "selector": "@e7"}


@pytest.mark.asyncio
async def test_is_visible_populates_structured_with_result_value() -> None:
    from openmimi.tools import actions

    rv = {"visible": True, "tag": "DIV", "rect": {"x": 0, "y": 0, "width": 5, "height": 5}}

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            return SimpleNamespace(stdout=json.dumps({"result": rv}))

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"result": rv}

    result = await actions.get("is_visible")(_Engine(), {"ref": "@e2"})
    assert result.is_error is False
    assert result.structured == rv


@pytest.mark.asyncio
async def test_network_log_populates_structured_with_requests() -> None:
    from openmimi.tools import actions

    requests = [
        {"url": "https://api.example.com/x", "status": 200, "method": "GET"},
        {"url": "https://api.example.com/y", "status": 404, "method": "POST"},
    ]
    calls: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            calls.append(args)
            return SimpleNamespace(stdout=json.dumps({"requests": requests}))

        def _parse_data(self, _raw: str) -> dict[str, Any]:
            return {"requests": requests}

    result = await actions.get("network_log")(_Engine(), {"duration_ms": 0})
    assert result.is_error is False
    assert result.structured == {"requests": requests}


@pytest.mark.asyncio
async def test_react_fill_with_ref_resolves_box_then_uses_elementFromPoint() -> None:
    """react_fill must NOT pass the snapshot ref through querySelector.

    Regression test for cycle 7: `document.querySelector("e22")` always
    returns null because agent-browser refs are opaque handles, not CSS
    selectors. The fix resolves the ref to a box, then targets the
    element via document.elementFromPoint.
    """
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            if args[:2] == ("get", "box"):
                return SimpleNamespace(
                    stdout=json.dumps(
                        {"box": {"x": 100, "y": 200, "width": 50, "height": 20}}
                    )
                )
            # eval call
            return SimpleNamespace(
                stdout=json.dumps(
                    {"result": {"ok": True, "tag": "input", "method": "prototype_setter"}}
                )
            )

        def _parse_data(self, raw: str) -> dict[str, Any]:
            return json.loads(raw)

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("react_fill")(
        _Engine(), {"ref": "e22", "value": "hello"}
    )

    assert result.is_error is False
    assert "React-filled" in result.output
    # First call: resolve ref to box. Second call: eval the React-aware setter.
    assert exec_calls[0] == ("get", "box", "e22", "--json")
    assert exec_calls[1][0] == "eval"
    eval_js = exec_calls[1][1]
    assert "document.elementFromPoint" in eval_js
    # The buggy implementation would have embedded querySelector("e22").
    assert 'querySelector("e22")' not in eval_js
    assert 'querySelector(\\"e22\\")' not in eval_js
    # Center of the mocked box: (125, 210).
    assert "125" in eval_js and "210" in eval_js


@pytest.mark.asyncio
async def test_react_fill_with_ref_reports_error_when_box_unresolvable() -> None:
    """If agent-browser can't resolve the ref to a box, react_fill must
    surface a clear error instead of running buggy fallback JS."""
    from openmimi.tools import actions

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            assert args[:2] == ("get", "box")
            return SimpleNamespace(stdout=json.dumps({}))

        def _parse_data(self, raw: str) -> dict[str, Any]:
            return json.loads(raw)

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("react_fill")(
        _Engine(), {"ref": "e99", "value": "x"}
    )
    assert result.is_error is True
    assert "could not resolve ref e99" in result.output


@pytest.mark.asyncio
async def test_react_fill_with_ref_handles_flat_box_response() -> None:
    """agent-browser's `get box` returns {x,y,width,height} flat under
    `data`, NOT wrapped in `data.box`. Regression for cycle 15: react_fill
    (and every get-box caller) silently failed in production because the
    parser only looked at `data.box`, while the test mocks happened to use
    the wrapped shape that the binary never emits.
    """
    from openmimi.tools import actions

    exec_calls: list[tuple[Any, ...]] = []

    class _Engine:
        async def _exec(self, *args: str, **_kw: Any) -> Any:
            exec_calls.append(args)
            if args[:2] == ("get", "box"):
                # REAL agent-browser shape: box fields flat, no `box` wrapper.
                return SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "success": True,
                            "data": {"x": 100, "y": 200, "width": 50, "height": 20},
                            "error": None,
                        }
                    )
                )
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "success": True,
                        "data": {
                            "result": {
                                "ok": True,
                                "tag": "input",
                                "method": "prototype_setter",
                            }
                        },
                        "error": None,
                    }
                )
            )

        def _parse_data(self, raw: str) -> dict[str, Any]:
            parsed = json.loads(raw)
            return parsed.get("data", {})

        async def _take_screenshot(self) -> str | None:
            return None

    result = await actions.get("react_fill")(
        _Engine(), {"ref": "e22", "value": "hello"}
    )

    assert result.is_error is False, result.output
    assert "React-filled" in result.output
    eval_js = exec_calls[1][1]
    # Center of the flat box: (125, 210).
    assert "125" in eval_js and "210" in eval_js


@pytest.mark.asyncio
async def test_extract_box_helper_accepts_both_shapes() -> None:
    """`_extract_box` must handle the real agent-browser shape (flat
    x/y/width/height under data) AND the wrapped {box:{...}} form that
    older mocks rely on. Both should produce the same dict."""
    from openmimi.tools.agent_browser import _extract_box

    flat = {"x": 1, "y": 2, "width": 3, "height": 4}
    assert _extract_box(flat) == flat
    assert _extract_box({"box": flat}) == flat
    # missing field → None
    assert _extract_box({"x": 1, "y": 2, "width": 3}) is None
    # not a dict → None
    assert _extract_box(None) is None
    assert _extract_box("not-a-dict") is None
    # empty
    assert _extract_box({}) is None


