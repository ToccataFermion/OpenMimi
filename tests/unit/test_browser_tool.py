"""Unit tests for BrowserTool action handlers.

The tests use a FakeSession / FakePage / FakeMouse triple injected directly
into `BrowserTool._session`, so no real Chromium is launched.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from openmimi.tools.browser import BrowserTool
from openmimi.tools.errors import ErrorCode


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []
        self.scrolls: list[dict[str, Any]] = []
        self.moves: list[dict[str, Any]] = []
        self.on_click: Any = None

    async def click(self, x: int, y: int, **kwargs: Any) -> None:
        self.clicks.append({"x": x, "y": y, **kwargs})
        if self.on_click is not None:
            res = self.on_click(x, y)
            if asyncio.iscoroutine(res):
                await res

    async def move(self, x: int, y: int, steps: int = 1) -> None:
        self.moves.append({"x": x, "y": y, "steps": steps})

    async def scroll(
        self,
        x: int = 0,
        y: int = 0,
        delta_x: int | None = None,
        delta_y: int | None = None,
    ) -> None:
        self.scrolls.append(
            {"x": x, "y": y, "delta_x": delta_x, "delta_y": delta_y}
        )


class FakePage:
    def __init__(
        self,
        *,
        url: str = "https://example.com/",
        title: str = "Example",
        screenshot_b64: str = "ZmFrZQ==",
        find_text_result: Any = None,
        focus_fill_result: str = "true",
        page_text: str = "Hello world",
        goto_behaviour: Any = None,
    ) -> None:
        self._url = url
        self._title = title
        self._screenshot_b64 = screenshot_b64
        self._find_text_result = find_text_result
        self._focus_fill_result = focus_fill_result
        self._page_text = page_text
        self._goto_behaviour = goto_behaviour
        self._mouse_obj = FakeMouse()

        self.goto_calls: list[str] = []
        self.press_calls: list[str] = []
        self.viewport_calls: list[tuple[int, int]] = []
        self.evaluate_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.screenshot_calls = 0

    @property
    def mouse(self):  # noqa: ANN201 - mirrors browser_use Page.mouse signature
        async def _get() -> FakeMouse:
            return self._mouse_obj

        return _get()

    async def goto(self, url: str) -> None:
        self.goto_calls.append(url)
        if self._goto_behaviour is None:
            return
        if isinstance(self._goto_behaviour, Exception):
            raise self._goto_behaviour
        if callable(self._goto_behaviour):
            await self._goto_behaviour(url)

    async def get_url(self) -> str:
        return self._url

    async def get_title(self) -> str:
        return self._title

    async def screenshot(self, format: str = "png", quality: int | None = None) -> str:
        self.screenshot_calls += 1
        return self._screenshot_b64

    async def press(self, key: str) -> None:
        self.press_calls.append(key)

    async def set_viewport_size(self, width: int, height: int) -> None:
        self.viewport_calls.append((width, height))

    async def evaluate(self, js: str, *args: Any) -> str:
        self.evaluate_calls.append((js, args))
        if "PRIORITIZED" in js:  # _FIND_TEXT_JS
            r = self._find_text_result
            if r is None:
                return "null"
            return json.dumps(r)
        if "elementFromPoint" in js:  # _HOVER_DISPATCH_JS
            return "true"
        if "isContentEditable" in js:  # _FOCUS_AND_FILL_JS
            return self._focus_fill_result
        if "innerText" in js:  # _PAGE_TEXT_JS
            return self._page_text
        return ""


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.killed = False

    async def must_get_current_page(self) -> FakePage:
        return self._page

    async def kill(self) -> None:
        self.killed = True


def _make_tool(page: FakePage) -> BrowserTool:
    tool = BrowserTool(download_dir="./tmp", viewport=(1280, 800))
    tool._session = FakeSession(page)  # noqa: SLF001 - test injection
    return tool


# ---------- navigate / screenshot / wait / press ---------------------------


@pytest.mark.asyncio
async def test_navigate_ok() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "navigate", "url": "https://example.com"})
    assert not result.is_error
    assert page.goto_calls == ["https://example.com"]
    assert "Navigated to https://example.com" in result.output
    assert result.base64_image == "ZmFrZQ=="
    assert result.details["url"] == "https://example.com/"
    assert result.details["title"] == "Example"


@pytest.mark.asyncio
async def test_navigate_timeout() -> None:
    async def slow(_: str) -> None:
        await asyncio.sleep(1.0)

    page = FakePage(goto_behaviour=slow)
    tool = _make_tool(page)
    result = await tool(
        {"action": "navigate", "url": "https://example.com", "timeout_s": 0.1}
    )
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TIMEOUT.value


@pytest.mark.asyncio
async def test_navigate_navigation_error() -> None:
    page = FakePage(goto_behaviour=RuntimeError("dns_failed"))
    tool = _make_tool(page)
    result = await tool({"action": "navigate", "url": "https://nope.invalid"})
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.NAVIGATION_ERROR.value
    assert "dns_failed" in result.output


@pytest.mark.asyncio
async def test_screenshot_ok() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "screenshot"})
    assert not result.is_error
    assert result.base64_image == "ZmFrZQ=="
    assert page.screenshot_calls >= 1


@pytest.mark.asyncio
async def test_wait_ok() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "wait", "duration_s": 0.01})
    assert not result.is_error
    assert "Waited 0.01s" in result.output


@pytest.mark.asyncio
async def test_press_ok() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "press", "key": "Enter"})
    assert not result.is_error
    assert page.press_calls == ["Enter"]


# ---------- scroll ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "direction,expected_dx,expected_dy",
    [
        ("up", 0, -300),
        ("down", 0, 300),
        ("left", -300, 0),
        ("right", 300, 0),
    ],
)
async def test_scroll_directions(
    direction: str, expected_dx: int, expected_dy: int
) -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool(
        {"action": "scroll", "direction": direction, "amount": 300}
    )
    assert not result.is_error
    assert page._mouse_obj.scrolls == [
        {"x": 640, "y": 400, "delta_x": expected_dx, "delta_y": expected_dy}
    ]


# ---------- click ----------------------------------------------------------


@pytest.mark.asyncio
async def test_click_with_coordinate() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool(
        {"action": "click", "coordinate": [812, 124]}
    )
    assert not result.is_error
    assert page._mouse_obj.clicks == [{"x": 812, "y": 124}]
    assert result.details["target_resolved"] == {
        "by": "coordinate",
        "value": "812,124",
    }


@pytest.mark.asyncio
async def test_click_with_target_text_found() -> None:
    page = FakePage(find_text_result={"x": 100, "y": 200})
    tool = _make_tool(page)
    result = await tool({"action": "click", "target_text": "Login"})
    assert not result.is_error
    assert page._mouse_obj.clicks == [{"x": 100, "y": 200}]
    assert result.details["target_resolved"]["by"] == "text"
    assert result.details["target_resolved"]["value"] == "Login"


@pytest.mark.asyncio
async def test_click_with_target_text_not_found() -> None:
    page = FakePage(find_text_result=None)
    tool = _make_tool(page)
    result = await tool({"action": "click", "target_text": "Nope"})
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TARGET_NOT_FOUND.value
    assert page._mouse_obj.clicks == []


@pytest.mark.asyncio
async def test_click_with_target_hint_unsupported() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "click", "target_hint": "gear icon"})
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TARGET_NOT_FOUND.value
    assert page._mouse_obj.clicks == []


# ---------- hover ---------------------------------------------------------


@pytest.mark.asyncio
async def test_hover_with_target_text() -> None:
    page = FakePage(find_text_result={"x": 361, "y": 30})
    tool = _make_tool(page)
    result = await tool({"action": "hover", "target_text": "解决方案"})
    assert not result.is_error
    assert page._mouse_obj.moves == [{"x": 361, "y": 30, "steps": 1}]
    assert page._mouse_obj.clicks == []
    assert result.details["target_resolved"]["by"] == "text"
    assert result.details["target_resolved"]["value"] == "解决方案"
    assert "Hovered at (361, 30)" in result.output


@pytest.mark.asyncio
async def test_hover_with_coordinate() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "hover", "coordinate": [120, 240]})
    assert not result.is_error
    assert page._mouse_obj.moves == [{"x": 120, "y": 240, "steps": 1}]
    assert result.details["target_resolved"] == {
        "by": "coordinate",
        "value": "120,240",
    }


@pytest.mark.asyncio
async def test_hover_dispatches_js_event_chain() -> None:
    page = FakePage()
    tool = _make_tool(page)
    await tool({"action": "hover", "coordinate": [361, 30]})
    dispatch_calls = [
        (js, args)
        for js, args in page.evaluate_calls
        if "elementFromPoint" in js
    ]
    assert len(dispatch_calls) == 1
    js, args = dispatch_calls[0]
    assert args == (361, 30)
    for ev in ("mouseover", "mouseenter", "pointerover", "pointerenter"):
        assert ev in js


@pytest.mark.asyncio
async def test_hover_with_target_text_not_found() -> None:
    page = FakePage(find_text_result=None)
    tool = _make_tool(page)
    result = await tool({"action": "hover", "target_text": "Nope"})
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TARGET_NOT_FOUND.value
    assert page._mouse_obj.moves == []


# ---------- type -----------------------------------------------------------


@pytest.mark.asyncio
async def test_type_without_locator_focus_succeeds() -> None:
    page = FakePage(focus_fill_result="true")
    tool = _make_tool(page)
    result = await tool({"action": "type", "text": "hello"})
    assert not result.is_error
    assert "Typed 5" in result.output
    assert page._mouse_obj.clicks == []
    assert any("isContentEditable" in js for js, _ in page.evaluate_calls)


@pytest.mark.asyncio
async def test_type_without_locator_focus_fails() -> None:
    page = FakePage(focus_fill_result="false")
    tool = _make_tool(page)
    result = await tool({"action": "type", "text": "hello"})
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TARGET_NOT_FOUND.value


@pytest.mark.asyncio
async def test_type_with_target_text_clicks_then_fills() -> None:
    page = FakePage(find_text_result={"x": 50, "y": 60}, focus_fill_result="true")
    tool = _make_tool(page)
    result = await tool(
        {"action": "type", "target_text": "Search", "text": "playwright"}
    )
    assert not result.is_error
    assert page._mouse_obj.clicks == [{"x": 50, "y": 60}]
    assert result.details["target_resolved"]["by"] == "text"


# ---------- extract --------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_returns_page_text() -> None:
    page = FakePage(page_text="Quarterly results: ...")
    tool = _make_tool(page)
    result = await tool({"action": "extract", "instruction": "summarize"})
    assert not result.is_error
    assert "Quarterly results" in result.output


# ---------- download ------------------------------------------------------


@pytest.mark.asyncio
async def test_download_success(tmp_path: Any) -> None:
    page = FakePage(find_text_result={"x": 30, "y": 40})
    tool = BrowserTool(download_dir=str(tmp_path), viewport=(1280, 800))
    tool._session = FakeSession(page)  # noqa: SLF001

    payload = b"hello-world-bytes"

    def _drop(_x: int, _y: int) -> None:
        (tmp_path / "report.csv").write_bytes(payload)

    page._mouse_obj.on_click = _drop

    result = await tool(
        {"action": "download", "target_text": "Export", "timeout_s": 2}
    )
    assert not result.is_error
    downloads = result.details["downloads"]
    assert len(downloads) == 1
    info = downloads[0]
    assert info["path"].endswith("report.csv")
    assert info["size_bytes"] == len(payload)
    import hashlib

    assert info["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_download_timeout_when_no_file_appears(tmp_path: Any) -> None:
    page = FakePage(find_text_result={"x": 10, "y": 20})
    tool = BrowserTool(download_dir=str(tmp_path), viewport=(1280, 800))
    tool._session = FakeSession(page)  # noqa: SLF001

    result = await tool(
        {"action": "download", "target_text": "Export", "timeout_s": 0.5}
    )
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TIMEOUT.value


@pytest.mark.asyncio
async def test_download_waits_for_partial_to_finish(tmp_path: Any) -> None:
    page = FakePage(find_text_result={"x": 10, "y": 20})
    tool = BrowserTool(download_dir=str(tmp_path), viewport=(1280, 800))
    tool._session = FakeSession(page)  # noqa: SLF001

    payload = b"final-content"

    async def _drop_partial_then_complete(_x: int, _y: int) -> None:
        partial = tmp_path / "data.csv.crdownload"
        partial.write_bytes(b"partial")
        await asyncio.sleep(0.3)
        partial.unlink()
        (tmp_path / "data.csv").write_bytes(payload)

    page._mouse_obj.on_click = _drop_partial_then_complete

    result = await tool(
        {"action": "download", "target_text": "Export", "timeout_s": 3}
    )
    assert not result.is_error
    info = result.details["downloads"][0]
    assert info["path"].endswith("data.csv")
    assert info["size_bytes"] == len(payload)


# ---------- invalid input --------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_input_returns_error_result() -> None:
    page = FakePage()
    tool = _make_tool(page)
    result = await tool({"action": "navigate"})  # missing url
    assert result.is_error
    assert result.details["error_code"] == ErrorCode.TOOL_INTERNAL_ERROR.value
    assert page.goto_calls == []


@pytest.mark.asyncio
async def test_close_kills_session_when_started() -> None:
    page = FakePage()
    tool = _make_tool(page)
    await tool({"action": "screenshot"})
    session = tool._session  # noqa: SLF001
    await tool.close()
    assert session.killed is True
    assert tool._session is None  # noqa: SLF001
