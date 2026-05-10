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
