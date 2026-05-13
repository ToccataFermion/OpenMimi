"""Navigation actions: navigate / back / forward / reload.

Free-function handlers extracted from ``AgentBrowserTool._do_*``. The
``engine`` argument is the live tool instance; handlers reach into its
private state directly (``engine._started``, ``engine._exec``, ...).

The registry decorator runs at import time, so importing this module is
all that's needed to make the action available to the dispatcher.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


@register("navigate")
async def navigate(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    url = inp.get("url", "about:blank")
    if not engine._started:
        await engine._start_browser(url)
    else:
        await engine._exec("open", url, "--json")
    await engine._refresh_tabs()
    snapshot = await engine._exec("snapshot", "--json")
    text, _ = engine._parse_snapshot(snapshot.stdout)

    # Retry once if the page is still empty (slow initial load / first start)
    if "(empty page)" in text:
        await asyncio.sleep(3)
        await engine._refresh_tabs()
        snapshot = await engine._exec("snapshot", "--json")
        text, _ = engine._parse_snapshot(snapshot.stdout)

    details = {
        "url": url,
        "open_tabs": engine._tabs,
        "active_tab": engine._active_tab_index,
    }
    return ToolResult(
        output=f"Navigated to {url}\n{text[:2000]}",
        details=details,
    )


@register("back")
async def back(engine: "AgentBrowserTool", _inp: dict[str, Any]) -> ToolResult:
    result = await engine._exec("back", "--json")
    return ToolResult(output=f"Navigated back\n{result.stdout[:1000]}")


@register("forward")
async def forward(engine: "AgentBrowserTool", _inp: dict[str, Any]) -> ToolResult:
    result = await engine._exec("forward", "--json")
    return ToolResult(output=f"Navigated forward\n{result.stdout[:1000]}")


@register("reload")
async def reload(engine: "AgentBrowserTool", _inp: dict[str, Any]) -> ToolResult:
    result = await engine._exec("reload", "--json")
    return ToolResult(output=f"Page reloaded\n{result.stdout[:1000]}")
