"""Browser tool: wraps browser-use BrowserSession for Anthropic-style tool actions.

Adapter design intent: depend only on browser-use's public API (`BrowserSession`,
`BrowserProfile`, and the actor `Page` object) so upstream upgrades remain a
one-line bump.

Each action returns a fresh screenshot in `ToolResult.base64_image` plus
structured `BrowserToolDetails` (URL, title, target_resolved, error_code).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

from .base import ToolBase
from .browser_schema import (
    BROWSER_TOOL_INPUT_ADAPTER,
    BrowserToolDetails,
    ClickInput,
    DownloadInfo,
    DownloadInput,
    ExtractInput,
    HoverInput,
    NavigateInput,
    PressInput,
    ScreenshotInput,
    ScrollInput,
    TargetResolved,
    TypeInput,
    WaitInput,
    browser_tool_input_json_schema,
)
from .errors import ErrorCode
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Operate a Chromium browser with mixed locator strategies "
    "(target_text / target_hint / coordinate; coordinate is mutually exclusive "
    "with the semantic targets). Every call returns a fresh screenshot plus the "
    "current URL/title in `details`. Prefer `target_text`/`target_hint`; fall "
    "back to `coordinate` only when the element has no stable text. For "
    "navigation menus that expand on mouse-over, use `hover` first to reveal "
    "the submenu, then `click` the desired entry."
)

_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_DOWNLOAD_TIMEOUT_S = 30.0
_DOWNLOAD_POLL_INTERVAL_S = 0.25
_PARTIAL_SUFFIXES = (".crdownload", ".tmp", ".part", ".partial")
_EXTRACT_MAX_CHARS = 4000

# After page.goto() returns the CDP frame can still be in the middle of
# attaching/painting; an immediate Page.captureScreenshot occasionally
# fails on a half-attached target. Waiting ~0.3s lets the target settle
# without slowing the loop perceptibly.
_POST_NAVIGATE_SETTLE_S = 0.3

# After a hover the page often runs a CSS/JS transition before the submenu
# is fully painted; sleeping briefly lets the screenshot capture the
# expanded state so the LLM can pick the submenu entry on the next turn.
_POST_HOVER_SETTLE_S = 0.4

# JS helper: locate the centre of the element whose visible text matches `text`.
# Tries strict equality on interactive elements first, then loose contains
# matches, then any element. Returns null if nothing matches.
_FIND_TEXT_JS = """(text) => {
    const target = (text || '').trim();
    if (!target) return null;
    const PRIORITIZED = 'a, button, input, select, textarea,'
        + '[role="button"], [role="link"], [role="tab"], [role="menuitem"]';
    const labelOf = (el) => (
        el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || ''
    ).trim();
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const centre = (el) => {
        el.scrollIntoView({block: 'center', inline: 'center'});
        const r = el.getBoundingClientRect();
        return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
    };
    const scan = (selector, predicate) => {
        for (const el of document.querySelectorAll(selector)) {
            if (visible(el) && predicate(labelOf(el))) return centre(el);
        }
        return null;
    };
    const eq = (s) => s === target;
    const has = (s) => s.includes(target);
    return scan(PRIORITIZED, eq) || scan(PRIORITIZED, has) || scan('*', eq) || scan('*', has);
}
"""

_PAGE_TEXT_JS = """(maxChars) => {
    const txt = (document.body && document.body.innerText) || '';
    const limit = Number(maxChars) || 4000;
    return txt.length > limit ? txt.slice(0, limit) + '\\n...[truncated]' : txt;
}
"""

_HOVER_DISPATCH_JS = """(x, y) => {
    const el = document.elementFromPoint(x, y);
    if (!el) return false;
    const make = (type) => new MouseEvent(type, {
        bubbles: true, cancelable: true, view: window,
        clientX: x, clientY: y, button: 0
    });
    el.dispatchEvent(make('pointerover'));
    el.dispatchEvent(make('mouseover'));
    el.dispatchEvent(make('pointerenter'));
    el.dispatchEvent(make('mouseenter'));
    return true;
}
"""

_FOCUS_AND_FILL_JS = """(value) => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') {
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }
    if (el.isContentEditable) {
        el.textContent = value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        return true;
    }
    return false;
}
"""


class _BrowserToolError(Exception):
    """Internal: structured error carrying an error code for ToolResult conversion."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrowserTool(ToolBase):
    name = "browser"

    def __init__(
        self,
        *,
        download_dir: str,
        viewport: tuple[int, int] = (1280, 800),
        headless: bool = False,
    ) -> None:
        self._download_dir = download_dir
        self._viewport = viewport
        self._headless = headless
        self._session: Any = None
        self._lock = asyncio.Lock()

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": browser_tool_input_json_schema(),
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        try:
            validated = BROWSER_TOOL_INPUT_ADAPTER.validate_python(tool_input)
        except Exception as exc:
            return ToolResult(
                output=f"Invalid tool input: {exc}",
                is_error=True,
                details={"error_code": ErrorCode.TOOL_INTERNAL_ERROR.value},
            )

        async with self._lock:
            try:
                page = await self._ensure_started()
                resolved: TargetResolved | None = None

                if isinstance(validated, NavigateInput):
                    output = await self._handle_navigate(page, validated)
                elif isinstance(validated, ScreenshotInput):
                    output = "Captured screenshot."
                elif isinstance(validated, WaitInput):
                    output = await self._handle_wait(validated)
                elif isinstance(validated, PressInput):
                    output = await self._handle_press(page, validated)
                elif isinstance(validated, ScrollInput):
                    output = await self._handle_scroll(page, validated)
                elif isinstance(validated, ClickInput):
                    output, resolved = await self._handle_click(page, validated)
                elif isinstance(validated, HoverInput):
                    output, resolved = await self._handle_hover(page, validated)
                elif isinstance(validated, TypeInput):
                    output, resolved = await self._handle_type(page, validated)
                elif isinstance(validated, ExtractInput):
                    output = await self._handle_extract(page, validated)
                elif isinstance(validated, DownloadInput):
                    output, resolved, download_info = await self._handle_download(
                        page, validated
                    )
                    base64_image = await self._safe_screenshot(page)
                    details = await self._build_details(
                        page,
                        resolved=resolved,
                        downloads=[download_info] if download_info else None,
                    )
                    return ToolResult(
                        output=output,
                        base64_image=base64_image,
                        details=details.model_dump(exclude_none=True),
                    )
                else:  # pragma: no cover - schema rejects unknown actions
                    raise _BrowserToolError(
                        ErrorCode.TOOL_INTERNAL_ERROR,
                        f"unhandled action: {type(validated).__name__}",
                    )

                base64_image = await self._safe_screenshot(page)
                details = await self._build_details(page, resolved=resolved)
                return ToolResult(
                    output=output,
                    base64_image=base64_image,
                    details=details.model_dump(exclude_none=True),
                )
            except _BrowserToolError as exc:
                page_for_screenshot = await self._maybe_get_page()
                base64_image = (
                    await self._safe_screenshot(page_for_screenshot)
                    if page_for_screenshot is not None
                    else None
                )
                details = await self._build_details(
                    page_for_screenshot,
                    error_code=exc.code,
                    retryable=exc.code != ErrorCode.NAVIGATION_ERROR,
                )
                return ToolResult(
                    output=str(exc),
                    base64_image=base64_image,
                    is_error=True,
                    details=details.model_dump(exclude_none=True),
                )
            except Exception as exc:
                page_for_screenshot = await self._maybe_get_page()
                base64_image = (
                    await self._safe_screenshot(page_for_screenshot)
                    if page_for_screenshot is not None
                    else None
                )
                details = await self._build_details(
                    page_for_screenshot,
                    error_code=ErrorCode.TOOL_INTERNAL_ERROR,
                )
                return ToolResult(
                    output=f"{exc.__class__.__name__}: {exc}",
                    base64_image=base64_image,
                    is_error=True,
                    details=details.model_dump(exclude_none=True),
                )

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.kill()
            except Exception:
                pass
            self._session = None

    # ----- handlers ---------------------------------------------------------

    async def _handle_navigate(self, page: Any, action: NavigateInput) -> str:
        timeout = action.timeout_s or _DEFAULT_TIMEOUT_S
        try:
            await asyncio.wait_for(page.goto(action.url), timeout=timeout)
        except TimeoutError as exc:
            raise _BrowserToolError(
                ErrorCode.TIMEOUT,
                f"navigation to {action.url!r} timed out after {timeout}s",
            ) from exc
        except Exception as exc:
            raise _BrowserToolError(
                ErrorCode.NAVIGATION_ERROR,
                f"navigation to {action.url!r} failed: {exc}",
            ) from exc
        await asyncio.sleep(_POST_NAVIGATE_SETTLE_S)
        return f"Navigated to {action.url}"

    async def _handle_wait(self, action: WaitInput) -> str:
        await asyncio.sleep(action.duration_s)
        return f"Waited {action.duration_s}s"

    async def _handle_press(self, page: Any, action: PressInput) -> str:
        await page.press(action.key)
        return f"Pressed {action.key}"

    async def _handle_scroll(self, page: Any, action: ScrollInput) -> str:
        delta_x = 0
        delta_y = 0
        if action.direction == "up":
            delta_y = -action.amount
        elif action.direction == "down":
            delta_y = action.amount
        elif action.direction == "left":
            delta_x = -action.amount
        else:
            delta_x = action.amount

        mouse = await page.mouse
        cx, cy = self._viewport[0] // 2, self._viewport[1] // 2
        await mouse.scroll(x=cx, y=cy, delta_x=delta_x, delta_y=delta_y)
        return f"Scrolled {action.direction} by {action.amount}px"

    async def _handle_click(
        self, page: Any, action: ClickInput
    ) -> tuple[str, TargetResolved]:
        x, y, resolved = await self._resolve_locator(
            page,
            target_text=action.target_text,
            target_hint=action.target_hint,
            coordinate=action.coordinate,
            action_label="click",
        )
        mouse = await page.mouse
        await mouse.click(x=x, y=y)
        return f"Clicked at ({x}, {y}) by {resolved.by}", resolved

    async def _handle_hover(
        self, page: Any, action: HoverInput
    ) -> tuple[str, TargetResolved]:
        x, y, resolved = await self._resolve_locator(
            page,
            target_text=action.target_text,
            target_hint=action.target_hint,
            coordinate=action.coordinate,
            action_label="hover",
        )
        mouse = await page.mouse
        await mouse.move(x, y)
        # CDP mouse.move drives the CSS :hover state but does not always
        # trigger JS mouseenter/mouseover listeners (many sites build
        # hover-to-expand menus on top of those events). Dispatching the
        # canonical hover event chain on the element under the pointer
        # covers the JS path; CSS :hover is already covered by the move.
        try:
            await page.evaluate(_HOVER_DISPATCH_JS, x, y)
        except Exception:
            pass
        await asyncio.sleep(_POST_HOVER_SETTLE_S)
        return f"Hovered at ({x}, {y}) by {resolved.by}", resolved

    async def _handle_type(
        self, page: Any, action: TypeInput
    ) -> tuple[str, TargetResolved | None]:
        resolved: TargetResolved | None = None
        if (
            action.target_text is not None
            or action.target_hint is not None
            or action.coordinate is not None
        ):
            x, y, resolved = await self._resolve_locator(
                page,
                target_text=action.target_text,
                target_hint=action.target_hint,
                coordinate=action.coordinate,
                action_label="type",
            )
            mouse = await page.mouse
            await mouse.click(x=x, y=y)

        ok = await page.evaluate(_FOCUS_AND_FILL_JS, action.text)
        if str(ok).lower() != "true":
            raise _BrowserToolError(
                ErrorCode.TARGET_NOT_FOUND,
                "no editable element is focused; click an input first",
            )
        return f"Typed {len(action.text)} character(s)", resolved

    async def _handle_extract(self, page: Any, action: ExtractInput) -> str:
        text = await page.evaluate(_PAGE_TEXT_JS, _EXTRACT_MAX_CHARS)
        return f"Page text (first {_EXTRACT_MAX_CHARS} chars):\n{text}"

    async def _handle_download(
        self, page: Any, action: DownloadInput
    ) -> tuple[str, TargetResolved, DownloadInfo]:
        x, y, resolved = await self._resolve_locator(
            page,
            target_text=action.target_text,
            target_hint=action.target_hint,
            coordinate=action.coordinate,
            action_label="download",
        )

        download_dir = Path(self._download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot_files(download_dir)

        mouse = await page.mouse
        await mouse.click(x=x, y=y)

        timeout = action.timeout_s or _DEFAULT_DOWNLOAD_TIMEOUT_S
        info = await self._await_download(download_dir, before, timeout)
        if info is None:
            raise _BrowserToolError(
                ErrorCode.TIMEOUT,
                f"download did not complete within {timeout}s",
            )
        return f"Downloaded {Path(info.path).name}", resolved, info

    async def _await_download(
        self, download_dir: Path, before: set[Path], timeout: float
    ) -> DownloadInfo | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            after = _snapshot_files(download_dir)
            new_files = sorted(after - before)
            complete = [f for f in new_files if not _is_partial(f)]
            if complete:
                f = complete[-1]
                try:
                    sha = _sha256_file(f)
                    size = f.stat().st_size
                except OSError:
                    return None
                return DownloadInfo(path=str(f), sha256=sha, size_bytes=size)
            await asyncio.sleep(_DOWNLOAD_POLL_INTERVAL_S)
        return None

    # ----- locator ---------------------------------------------------------

    async def _resolve_locator(
        self,
        page: Any,
        *,
        target_text: str | None,
        target_hint: str | None,
        coordinate: tuple[int, int] | None,
        action_label: str,
    ) -> tuple[int, int, TargetResolved]:
        if coordinate is not None:
            x, y = int(coordinate[0]), int(coordinate[1])
            return x, y, TargetResolved(by="coordinate", value=f"{x},{y}")

        if target_text is not None:
            coords = await self._find_text_coords(page, target_text)
            if coords is None:
                raise _BrowserToolError(
                    ErrorCode.TARGET_NOT_FOUND,
                    f"text {target_text!r} not found on page (action={action_label})",
                )
            return coords[0], coords[1], TargetResolved(by="text", value=target_text)

        if target_hint is not None:
            raise _BrowserToolError(
                ErrorCode.TARGET_NOT_FOUND,
                "target_hint is not supported in M1; use target_text or coordinate",
            )

        # Schema requires at least one locator for click/download; this path is
        # unreachable for those actions and only relevant for type without locator,
        # which is handled by the caller.
        raise _BrowserToolError(
            ErrorCode.TOOL_INTERNAL_ERROR, "no locator provided"
        )

    async def _find_text_coords(
        self, page: Any, text: str
    ) -> tuple[int, int] | None:
        raw = await page.evaluate(_FIND_TEXT_JS, text)
        coords = _parse_coords(raw)
        return coords

    # ----- session lifecycle -----------------------------------------------

    async def _ensure_started(self) -> Any:
        if self._session is None:
            from browser_use import BrowserProfile, BrowserSession

            profile = BrowserProfile(
                downloads_path=str(self._download_dir),
                headless=self._headless,
            )
            session = BrowserSession(browser_profile=profile)
            await session.start()
            self._session = session
        page = await self._session.must_get_current_page()
        try:
            await page.set_viewport_size(self._viewport[0], self._viewport[1])
        except Exception:
            pass
        return page

    async def _maybe_get_page(self) -> Any | None:
        if self._session is None:
            return None
        try:
            return await self._session.must_get_current_page()
        except Exception:
            return None

    # ----- screenshot / details --------------------------------------------

    async def _safe_screenshot(self, page: Any | None) -> str | None:
        if page is None:
            return None
        try:
            return await page.screenshot(format="png")
        except Exception:
            return None

    async def _build_details(
        self,
        page: Any | None,
        *,
        resolved: TargetResolved | None = None,
        error_code: ErrorCode | None = None,
        retryable: bool | None = None,
        downloads: list[DownloadInfo] | None = None,
    ) -> BrowserToolDetails:
        url: str | None = None
        title: str | None = None
        if page is not None:
            try:
                url = await page.get_url()
            except Exception:
                url = None
            try:
                title = await page.get_title()
            except Exception:
                title = None
        return BrowserToolDetails(
            url=url,
            title=title,
            target_resolved=resolved,
            error_code=error_code.value if error_code else None,
            retryable=retryable,
            downloads=list(downloads) if downloads else [],
        )


def _parse_coords(raw: Any) -> tuple[int, int] | None:
    """Parse the JS evaluate result into an (x, y) tuple if possible.

    `Page.evaluate` always returns a string; objects come back as JSON.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        x, y = raw.get("x"), raw.get("y")
        return (int(x), int(y)) if x is not None and y is not None else None
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        return int(raw[0]), int(raw[1])
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() in {"null", "none", "false"}:
            return None
        try:
            import json

            obj = json.loads(s)
        except Exception:
            return None
        return _parse_coords(obj)
    return None


def _snapshot_files(directory: Path) -> set[Path]:
    """Return the set of regular files currently in `directory` (non-recursive)."""
    try:
        return {p for p in directory.iterdir() if p.is_file()}
    except FileNotFoundError:
        return set()


def _is_partial(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suf) for suf in _PARTIAL_SUFFIXES)


def _sha256_file(path: Path, *, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()
