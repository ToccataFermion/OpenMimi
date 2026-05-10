"""Action handler registry for AgentBrowserTool.

Goal: incrementally pull the giant ``_do_*`` family of bound methods out of
``agent_browser.py`` (3700+ lines) into focused per-family modules. Until all
handlers move out, ``AgentBrowserTool._dispatch`` checks this registry first
and only falls back to its in-class table for actions that have not yet
migrated.

Usage from a handler module::

    from . import register
    from ...result import ToolResult

    @register("navigate")
    async def navigate(engine, inp):
        ...
        return ToolResult(...)

The ``engine`` parameter is the live ``AgentBrowserTool`` instance — handlers
read its private state (``engine._started``, ``engine._exec(...)``, etc.)
directly. Python does not name-mangle single-underscore attributes, so this
keeps the migration mechanical: a ``self.foo`` call inside the class becomes
``engine.foo`` in the free function.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool
    from ..result import ToolResult

ActionHandler = Callable[["AgentBrowserTool", dict[str, Any]], Awaitable["ToolResult"]]

_REGISTRY: dict[str, ActionHandler] = {}


def register(name: str) -> Callable[[ActionHandler], ActionHandler]:
    """Decorator that adds a free-function handler to the global registry.

    Re-registration of the same name overwrites the previous handler. That is
    intentional during the migration period so a per-family module can
    transparently take over an action that still has a method-based fallback.
    """

    def _wrap(fn: ActionHandler) -> ActionHandler:
        _REGISTRY[name] = fn
        return fn

    return _wrap


def get(name: str) -> ActionHandler | None:
    """Return the registered handler for *name*, or None if unmigrated."""
    return _REGISTRY.get(name)


def registered_actions() -> list[str]:
    """Snapshot of currently-migrated actions (sorted, for stable output)."""
    return sorted(_REGISTRY)


def _reset_for_tests() -> None:
    """Clear the registry. Tests only — do not use in production code."""
    _REGISTRY.clear()


__all__ = [
    "ActionHandler",
    "get",
    "register",
    "registered_actions",
]


# Importing the per-family modules below is what populates ``_REGISTRY``.
# Keep these imports at the bottom so the decorator (defined above) is
# already available when each module's @register calls fire.
from . import navigation  # noqa: E402,F401  (side-effect: registration)
from . import interaction  # noqa: E402,F401  (side-effect: registration)
from . import scroll  # noqa: E402,F401  (side-effect: registration)
from . import extract  # noqa: E402,F401  (side-effect: registration)
from . import wait  # noqa: E402,F401  (side-effect: registration)
from . import tab_session  # noqa: E402,F401  (side-effect: registration)
from . import network_cdp  # noqa: E402,F401  (side-effect: registration)
