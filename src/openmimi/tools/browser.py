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
import sys
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
    OpenTabRow,
    PressInput,
    ScreenshotInput,
    ScrollInput,
    SwitchTabInput,
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
    "the submenu, then `click` the desired entry. When a link opens a new tab, "
    "`details.open_tabs` lists every tab and the tool output explains focus; "
    "new tabs switch agent focus to the newest automatically so the screenshot "
    "matches. To pick a tab explicitly, use action `switch_tab` with "
    "`tab_index` (1-based, same order as `open_tabs`)."
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

# After a click (or other pointer action) that may open target=_blank, wait
# briefly so SessionManager registers the new target before we snapshot tabs.
_POST_POINTER_TAB_SETTLE_S = 0.25

# JS helper: locate the centre of the element whose visible text matches `text`.
# Tries strict equality on interactive elements first, then loose contains
# matches, then any element. Returns null if nothing matches.
_FIND_TEXT_JS = """(text) => {
    const target = (text || '').trim();
    if (!target) return null;
    const PRIORITIZED = 'a, button, input, select, textarea,'
        + '[role="button"], [role="link"], [role="tab"], [role="menuitem"]';
    const labelOf = (el) => (
        el.innerText || el.value
        || el.getAttribute('placeholder') || el.getAttribute('name')
        || el.getAttribute('aria-label') || el.getAttribute('title') || ''
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

# Coordinates are from the same locator click; many SPAs leave focus on a host
# <div> while the real <input> sits in light DOM below or inside shadow DOM.
_FOCUS_AND_FILL_JS = """/*__OPENMIMI_FOCUS_FILL__*/(value, x, y) => {
    const tryFill = (el, val) => {
        if (!el) return false;
        const tag = (el.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea') {
            if (el.disabled) return false;
            const ty = (el.getAttribute('type') || 'text').toLowerCase();
            if (ty === 'hidden') return false;
            el.focus();
            el.value = val;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }
        if (el.isContentEditable) {
            el.focus();
            el.textContent = val;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        }
        return false;
    };
    const pickEditableUnder = (root) => {
        if (!root) return null;
        if (root.shadowRoot) {
            const inner = root.shadowRoot.querySelector(
                'input:not([type="hidden"]):not([disabled]), textarea:not([disabled]),'
                + '[contenteditable="true"]'
            );
            if (inner) return inner;
        }
        const sub = root.querySelector && root.querySelector(
            ':scope input:not([type="hidden"]):not([disabled]),'
            + ':scope textarea:not([disabled]), :scope [contenteditable="true"]'
        );
        return sub || null;
    };
    const fromPoint = (px, py) => {
        if (px === undefined || py === undefined || px === null || py === null) {
            return null;
        }
        const top = document.elementFromPoint(Number(px), Number(py));
        if (!top) return null;
        let el = top;
        for (let depth = 0; el && depth < 14; depth++) {
            const pick = pickEditableUnder(el);
            if (pick) return pick;
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || el.isContentEditable) {
                return el;
            }
            el = el.parentElement;
        }
        return null;
    };
    let el = fromPoint(x, y);
    if (!el) el = document.activeElement;
    return tryFill(el, value);
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
                assert self._session is not None
                ids_before = self._page_target_ids(self._session)
                resolved: TargetResolved | None = None
                tab_frag: dict[str, Any] = {}

                if isinstance(validated, NavigateInput):
                    output, tab_frag = await self._handle_navigate(
                        page, validated, ids_before=ids_before
                    )
                elif isinstance(validated, ScreenshotInput):
                    output = "Captured screenshot."
                elif isinstance(validated, WaitInput):
                    output = await self._handle_wait(validated)
                elif isinstance(validated, PressInput):
                    output = await self._handle_press(page, validated)
                elif isinstance(validated, ScrollInput):
                    output = await self._handle_scroll(page, validated)
                elif isinstance(validated, ClickInput):
                    output, resolved, tab_frag = await self._handle_click(
                        page, validated, ids_before=ids_before
                    )
                elif isinstance(validated, HoverInput):
                    output, resolved = await self._handle_hover(page, validated)
                elif isinstance(validated, TypeInput):
                    output, resolved, tab_frag = await self._handle_type(
                        page, validated, ids_before=ids_before
                    )
                elif isinstance(validated, ExtractInput):
                    output = await self._handle_extract(page, validated)
                elif isinstance(validated, SwitchTabInput):
                    output, tab_frag = await self._handle_switch_tab(validated)
                elif isinstance(validated, DownloadInput):
                    output, resolved, download_info, tab_frag = (
                        await self._handle_download(
                            page, validated, ids_before=ids_before
                        )
                    )
                    await _unstick_debugger_paused_pages(self._session)
                    page = await self._session.must_get_current_page()
                    base64_image = await self._safe_screenshot(page)
                    details = await self._build_details(
                        page,
                        resolved=resolved,
                        downloads=[download_info] if download_info else None,
                        **tab_frag,
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

                await _unstick_debugger_paused_pages(self._session)
                page = await self._session.must_get_current_page()
                base64_image = await self._safe_screenshot(page)
                details = await self._build_details(
                    page, resolved=resolved, **tab_frag
                )
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

    async def _handle_navigate(
        self, page: Any, action: NavigateInput, *, ids_before: set[str]
    ) -> tuple[str, dict[str, Any]]:
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
        msg = f"Navigated to {action.url}"
        note, frag = await self._reconcile_tabs_if_needed(
            self._session, ids_before, "navigate"
        )
        if note:
            msg = f"{msg}\n{note}"
        return msg, frag

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
        self,
        page: Any,
        action: ClickInput,
        *,
        ids_before: set[str],
    ) -> tuple[str, TargetResolved, dict[str, Any]]:
        x, y, resolved = await self._resolve_locator(
            page,
            target_text=action.target_text,
            target_hint=action.target_hint,
            coordinate=action.coordinate,
            action_label="click",
        )
        mouse = await page.mouse
        await mouse.click(x=x, y=y)
        msg = f"Clicked at ({x}, {y}) by {resolved.by}"
        note, frag = await self._reconcile_tabs_if_needed(
            self._session, ids_before, "click"
        )
        if note:
            msg = f"{msg}\n{note}"
        return msg, resolved, frag

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
        self,
        page: Any,
        action: TypeInput,
        *,
        ids_before: set[str],
    ) -> tuple[str, TargetResolved | None, dict[str, Any]]:
        tab_frag: dict[str, Any] = {}
        resolved: TargetResolved | None = None
        click_x: int | None = None
        click_y: int | None = None
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
            click_x, click_y = x, y
            mouse = await page.mouse
            await mouse.click(x=x, y=y)
            note, tab_frag = await self._reconcile_tabs_if_needed(
                self._session, ids_before, "type (focus click)"
            )
        else:
            note = None

        ok = await page.evaluate(
            _FOCUS_AND_FILL_JS, action.text, click_x, click_y
        )
        if str(ok).lower() != "true":
            raise _BrowserToolError(
                ErrorCode.TARGET_NOT_FOUND,
                "no editable element is focused; click an input first",
            )
        msg = f"Typed {len(action.text)} character(s)"
        if note:
            msg = f"{msg}\n{note}"
        return msg, resolved, tab_frag

    async def _handle_extract(self, page: Any, action: ExtractInput) -> str:
        text = await page.evaluate(_PAGE_TEXT_JS, _EXTRACT_MAX_CHARS)
        return f"Page text (first {_EXTRACT_MAX_CHARS} chars):\n{text}"

    async def _handle_download(
        self,
        page: Any,
        action: DownloadInput,
        *,
        ids_before: set[str],
    ) -> tuple[str, TargetResolved, DownloadInfo | None, dict[str, Any]]:
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

        tab_note, tab_frag = await self._reconcile_tabs_if_needed(
            self._session, ids_before, "download"
        )

        timeout = action.timeout_s or _DEFAULT_DOWNLOAD_TIMEOUT_S
        info = await self._await_download(download_dir, before, timeout)
        if info is None:
            raise _BrowserToolError(
                ErrorCode.TIMEOUT,
                f"download did not complete within {timeout}s",
            )
        msg = f"Downloaded {Path(info.path).name}"
        if tab_note:
            msg = f"{msg}\n{tab_note}"
        return msg, resolved, info, tab_frag

    async def _handle_switch_tab(self, action: SwitchTabInput) -> tuple[str, dict[str, Any]]:
        session = self._session
        assert session is not None
        gp = getattr(session, "get_page_targets", None)
        if not callable(gp):
            raise _BrowserToolError(
                ErrorCode.TOOL_INTERNAL_ERROR,
                "switch_tab requires a live BrowserSession with tab support",
            )
        targets = gp()
        n = len(targets)
        idx = action.tab_index
        if idx < 1 or idx > n:
            raise _BrowserToolError(
                ErrorCode.TARGET_NOT_FOUND,
                f"switch_tab: tab_index {idx} out of range (open tabs: {n})",
            )
        tid = targets[idx - 1].target_id

        await _focus_page_target_for_agent(session, tid)
        await asyncio.sleep(0.12)
        targets2 = gp()
        rows = self._open_tab_rows(session, targets2)
        frag: dict[str, Any] = {
            "open_tabs": rows,
            "tab_count": len(rows),
            "switched_to_target_id": tid,
        }
        tab_block = self._format_open_tabs_text(rows)
        msg = (
            f"Switched agent focus to tab [{idx}] "
            f"(target …{tid[-6:]}).\n{tab_block}"
        )
        return msg, frag

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

    # ----- multi-tab (CDP targets) ----------------------------------------

    def _page_target_ids(self, session: Any | None) -> set[str]:
        if session is None:
            return set()
        gp = getattr(session, "get_page_targets", None)
        if not callable(gp):
            return set()
        try:
            return {t.target_id for t in gp()}
        except Exception:
            return set()

    def _open_tab_rows(self, session: Any, targets: list[Any]) -> list[OpenTabRow]:
        focused = getattr(session, "agent_focus_target_id", None)
        rows: list[OpenTabRow] = []
        for i, t in enumerate(targets, start=1):
            tid = t.target_id
            rows.append(
                OpenTabRow(
                    index=i,
                    target_id_suffix=tid[-8:],
                    target_id=tid,
                    url=getattr(t, "url", "") or "",
                    title=getattr(t, "title", "") or "",
                    agent_has_focus=tid == focused,
                )
            )
        return rows

    def _format_open_tabs_text(self, rows: list[OpenTabRow]) -> str:
        lines = ["Open tabs:"]
        for r in rows:
            mark = "*" if r.agent_has_focus else " "
            u = r.url if len(r.url) <= 100 else f"{r.url[:97]}..."
            lines.append(
                f"  [{r.index}]{mark} …{r.target_id_suffix} | {u}"
            )
        return "\n".join(lines)

    def _tabs_debug_stderr(self, *, n_tabs: int, focus_suffix: str, n_new: int) -> None:
        try:
            print(
                f"[tabs] count={n_tabs} focus=…{focus_suffix} new_targets={n_new}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass

    async def _reconcile_tabs_if_needed(
        self,
        session: Any | None,
        ids_before: set[str],
        verb: str,
    ) -> tuple[str | None, dict[str, Any]]:
        if session is None:
            return None, {}
        gp = getattr(session, "get_page_targets", None)
        if not callable(gp):
            return None, {}

        await asyncio.sleep(_POST_POINTER_TAB_SETTLE_S)
        try:
            targets = gp()
        except Exception:
            return None, {}

        await _unstick_debugger_paused_pages(session)

        new_ids = [t.target_id for t in targets if t.target_id not in ids_before]
        rows = self._open_tab_rows(session, targets)
        frag: dict[str, Any] = {
            "open_tabs": rows,
            "tab_count": len(rows),
        }
        if len(rows) <= 1:
            return None, {}

        focused = getattr(session, "agent_focus_target_id", None) or ""
        self._tabs_debug_stderr(
            n_tabs=len(rows),
            focus_suffix=focused[-6:] if focused else "?",
            n_new=len(new_ids),
        )

        note_lines: list[str] = []
        if new_ids:
            newest = new_ids[-1]
            await _focus_page_target_for_agent(session, newest)
            frag["switched_to_target_id"] = newest
            note_lines.append(
                f"New tab(s) after {verb} ({len(new_ids)} opened). "
                f"Agent focus was switched to the newest tab "
                f"(target …{newest[-6:]}) so this screenshot matches it."
            )
        else:
            note_lines.append(
                f"Multiple tabs open ({len(rows)}) after {verb}; "
                f"agent focus unchanged. Tab marked * is what tools/screenshots "
                f"use. To switch, call action \"switch_tab\" with tab_index."
            )

        tab_block = self._format_open_tabs_text(rows)
        return "\n".join(note_lines + [tab_block]), frag

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
        open_tabs: list[OpenTabRow] | None = None,
        tab_count: int | None = None,
        switched_to_target_id: str | None = None,
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
        ot = list(open_tabs) if open_tabs else []
        tc = tab_count if tab_count is not None else (len(ot) if ot else None)
        return BrowserToolDetails(
            url=url,
            title=title,
            target_resolved=resolved,
            open_tabs=ot,
            tab_count=tc,
            switched_to_target_id=switched_to_target_id,
            error_code=error_code.value if error_code else None,
            retryable=retryable,
            downloads=list(downloads) if downloads else [],
        )


async def _focus_page_target_for_agent(session: Any, target_id: str) -> None:
    """Point agent focus at ``target_id`` (e.g. newest tab after a ``target=_blank`` click).

    Avoids ``await event_bus.dispatch(SwitchTabEvent)`` which ends by awaiting
    ``AgentFocusChangedEvent`` and can stall ~54s when CDP appears disconnected
    (``RECONNECT_WAIT_TIMEOUT``). OpenMimi only needs CDP activate + cache
    invalidate like ``BrowserSession``'s handlers.

    Fakes without ``session_manager`` fall back to ``SwitchTabEvent``.
    """
    sm = getattr(session, "session_manager", None)
    gc = getattr(session, "get_or_create_cdp_session", None)
    if sm is None or not callable(gc):
        from browser_use.browser.events import SwitchTabEvent

        await session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
        return

    cdp_session = await gc(target_id=target_id, focus=True)
    await cdp_session.cdp_client.send.Target.activateTarget(
        params={"targetId": target_id}
    )

    dw = getattr(session, "_dom_watchdog", None)
    if dw is not None:
        dw.clear_cache()
    session._cached_browser_state_summary = None
    session._cached_selector_map.clear()

    await gc(target_id=target_id, focus=True)

    profile = getattr(session, "browser_profile", None)
    if (
        profile is not None
        and getattr(profile, "viewport", None)
        and not getattr(profile, "no_viewport", False)
    ):
        vw = profile.viewport.width
        vh = profile.viewport.height
        dpr = profile.device_scale_factor or 1.0
        await session._cdp_set_viewport(vw, vh, dpr, target_id=target_id)


async def _unstick_debugger_paused_pages(session: Any) -> None:
    """Best-effort: resume tabs stuck in the Chrome 'paused in debugger' overlay.

    Some sites call ``debugger`` or hit a breakpoint on the opener tab when opening
    a login window. That dims the page and shows a yellow banner (often mojibake
    under non-UTF8 consoles). CDP can stay blocked until the pause is cleared.
    """
    gp = getattr(session, "get_page_targets", None)
    gc = getattr(session, "get_or_create_cdp_session", None)
    if not callable(gp) or not callable(gc):
        return
    try:
        targets = gp()
    except Exception:
        return
    for t in targets:
        try:
            cdp_sess = await gc(target_id=t.target_id, focus=False)
            sid = cdp_sess.session_id
            client = cdp_sess.cdp_client
            await client.send.Runtime.runIfWaitingForDebugger(session_id=sid)
            try:
                await client.send.Debugger.resume(session_id=sid)
            except Exception:
                pass
        except Exception:
            pass


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
