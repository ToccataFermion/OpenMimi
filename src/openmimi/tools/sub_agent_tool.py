"""LLM-facing tool wrapper around ``SubAgentRunner`` (roadmap #8 stage 3).

Lets the main agent spawn a focused sub-agent for one expensive sub-task
(CAPTCHA solving, deep search, long PDF read) without polluting its own
context. Only the sub-agent's final plain-text reply comes back as the
``ToolResult.output``; intermediate tool traffic stays in the sub-agent's
own episodic stream under a derived ``<parent>--sub-<N>`` session id.

The tool intentionally does NOT take a fixed ``parent_session_id`` at
construction time, because the parent session id changes per ``run_task``
invocation in the orchestrator. Instead the caller injects a
``session_provider`` callable that the tool consults each ``__call__`` to
read the *current* parent session id.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable

from ..sub_agent import SubAgentRequest, SubAgentRunner
from .base import ToolBase
from .errors import ErrorCode, make_error_result
from .result import ToolResult

_TOOL_DESCRIPTION = (
    "Spawn a focused sub-agent for one self-contained sub-task. The "
    "sub-agent runs its own tool loop in isolation and returns only its "
    "final plain-text reply — intermediate tool calls, screenshots, and "
    "scratchpad never enter your context. Use this for high-cost branches "
    "(CAPTCHA, deep search, long PDF reads) so they don't bloat your own "
    "history. The sub-agent inherits your toolset by default; pass "
    "allowed_tools to narrow it."
)

_DEFAULT_MAX_TURNS = 10


class SubAgentTool(ToolBase):
    """Expose a ``SubAgentRunner`` to the parent LLM as a normal tool."""

    name = "sub_agent"

    def __init__(
        self,
        runner: SubAgentRunner,
        *,
        session_provider: Callable[[], str] | None = None,
    ) -> None:
        self._runner = runner
        # session_provider is consulted on every call because the
        # orchestrator only knows the parent session id at run_task /
        # run_chat_turn entry, well after this tool has been constructed.
        self._session_provider = session_provider
        self._counter = itertools.count()

    def set_session_provider(
        self, provider: Callable[[], str] | None
    ) -> None:
        """(Re-)bind the parent session id provider after construction.

        The orchestrator uses this to wire the tool up to its own
        ``_current_session_id`` attribute once both have been built.
        """
        self._session_provider = provider

    def _next_index(self) -> int:
        return next(self._counter)

    def _current_parent_session_id(self) -> str:
        if self._session_provider is None:
            return ""
        try:
            val = self._session_provider()
        except Exception:
            return ""
        return str(val) if val else ""

    def to_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": _TOOL_DESCRIPTION,
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Self-contained instruction for the sub-agent. "
                            "Include all context it needs — it does not see "
                            "your conversation history."
                        ),
                    },
                    "allowed_tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional whitelist of tool names. Omit to "
                            "inherit your full toolset; pass [] to disable "
                            "all tools (pure reasoning sub-agent); pass a "
                            "list to restrict to those tools. Unknown "
                            "names are silently skipped."
                        ),
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": (
                            "Maximum sampling-loop turns the sub-agent may "
                            "consume (default 10). Values <= 0 are "
                            "normalized to 1."
                        ),
                    },
                    "system_addendum": {
                        "type": "string",
                        "description": (
                            "Optional extra guidance appended to the "
                            "sub-agent's system prompt (e.g. 'Reply in "
                            "English even if the page is in Chinese')."
                        ),
                    },
                },
                "required": ["task"],
            },
        }

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        task_raw = tool_input.get("task", "")
        if not isinstance(task_raw, str) or not task_raw.strip():
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                "sub_agent: 'task' is required and must be a non-empty string",
            )

        allowed_tools_raw = tool_input.get("allowed_tools")
        if allowed_tools_raw is not None and not isinstance(allowed_tools_raw, list):
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                "sub_agent: 'allowed_tools' must be an array of tool names",
            )
        allowed_tools: list[str] | None
        if allowed_tools_raw is None:
            allowed_tools = None
        else:
            allowed_tools = [n for n in allowed_tools_raw if isinstance(n, str) and n]

        max_turns_raw = tool_input.get("max_turns")
        if max_turns_raw is None:
            max_turns = _DEFAULT_MAX_TURNS
        elif isinstance(max_turns_raw, bool) or not isinstance(max_turns_raw, int):
            return make_error_result(
                ErrorCode.INVALID_INPUT,
                "sub_agent: 'max_turns' must be a positive integer",
            )
        else:
            max_turns = max_turns_raw

        addendum_raw = tool_input.get("system_addendum", "")
        if not isinstance(addendum_raw, str):
            addendum_raw = str(addendum_raw)

        parent_session_id = self._current_parent_session_id()
        sub_index = self._next_index()

        request = SubAgentRequest(
            task=task_raw,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            system_addendum=addendum_raw,
            parent_session_id=parent_session_id,
            sub_index=sub_index,
        )
        result = await self._runner.run(request)

        structured: dict[str, Any] = {
            "turns": result.turns,
            "sub_session_id": result.sub_session_id,
            "is_error": result.is_error,
        }
        if result.error_message:
            structured["error_message"] = result.error_message

        return ToolResult(
            output=result.text,
            is_error=result.is_error,
            details={
                "sub_session_id": result.sub_session_id,
                "turns": result.turns,
            },
            structured=structured,
        )


__all__ = ["SubAgentTool"]
