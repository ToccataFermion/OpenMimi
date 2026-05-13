"""Anthropic implementation of LLMClient (tool_use / tool_result).

Wraps `anthropic.AsyncAnthropic.messages.create` and exposes the same
provider-agnostic shape that `sampling_loop` consumes (a plain dict that
looks like an Anthropic Message: `content`, `stop_reason`, `usage`, ...).

Prompt caching is opt-in (default on) and applied in the conservative,
high-leverage spots:
- the system prompt block, and
- the last tool definition (which marks the entire tools array as cacheable).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol


class _AsyncMessagesLike(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class _AsyncClientLike(Protocol):
    @property
    def messages(self) -> _AsyncMessagesLike: ...


_DEFAULT_REQUEST_TIMEOUT_S = 90.0


class AnthropicClient:
    """LLMClient backed by anthropic.AsyncAnthropic.

    The optional `client` argument lets tests inject a stub that records calls
    and returns a canned Message-shaped object without touching the network.

    `request_timeout_s` overrides the SDK default (600s) with a value that is
    sane for an interactive agent. `progress_logger`, if set, receives one
    line of human-readable status before each request and one after the
    response (or failure), so a hung Aliyun MaaS request is observable from
    the CLI instead of looking like a frozen process.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        base_url: str | None = None,
        enable_prompt_caching: bool = True,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        progress_logger: Callable[[str], None] | None = None,
        client: _AsyncClientLike | None = None,
    ) -> None:
        self._model = model
        self._enable_caching = enable_prompt_caching
        self._request_timeout_s = request_timeout_s
        self._progress_logger = progress_logger
        self._call_index = 0
        if client is not None:
            self._client = client
        else:
            from anthropic import AsyncAnthropic

            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": request_timeout_s,
            }
            if base_url:
                kwargs["base_url"] = base_url
            self._client = AsyncAnthropic(**kwargs)

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        system_param = self._build_system(system)
        tools_param = self._build_tools(tools)
        messages_param = self._build_messages(messages)

        self._call_index += 1
        idx = self._call_index
        self._log_progress(
            f"[llm] turn {idx}: requesting "
            f"(timeout={self._request_timeout_s:.0f}s)..."
        )
        t0 = time.monotonic()
        try:
            result = await self._client.messages.create(
                model=self._model,
                system=system_param,
                messages=messages_param,
                tools=tools_param,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            self._log_progress(
                f"[llm] turn {idx}: failed after {elapsed:.1f}s: "
                f"{exc.__class__.__name__}: {exc}"
            )
            raise
        elapsed = time.monotonic() - t0
        as_dict = _to_dict(result)
        stop = as_dict.get("stop_reason") or "?"
        usage = as_dict.get("usage") or {}
        in_tok = usage.get("input_tokens", "?")
        out_tok = usage.get("output_tokens", "?")
        self._log_progress(
            f"[llm] turn {idx}: response in {elapsed:.1f}s (stop={stop}, tokens={in_tok}+{out_tok})"
        )
        return as_dict

    def _log_progress(self, message: str) -> None:
        if self._progress_logger is None:
            return
        try:
            self._progress_logger(message)
        except Exception:
            pass

    def _build_system(self, system: str) -> Any:
        if not self._enable_caching or not system:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_tools(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not tools:
            return []
        cloned = [dict(t) for t in tools]
        if not self._enable_caching:
            return cloned
        cloned[-1] = {**cloned[-1], "cache_control": {"type": "ephemeral"}}
        return cloned

    def _build_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Strip stale cache_control markers and add a fresh breakpoint.

        Anthropic prompt caching is prefix-based: everything before a
        ``cache_control`` block is cached and billed at the cheap read rate
        on subsequent requests.  We already cache ``system`` and the last
        ``tool`` definition.  Here we place a third breakpoint inside the
        message history so that earlier conversation turns are also cached.

        The breakpoint is placed on the message that's ~2 turns back from
        the end (each turn = assistant + user = 2 messages, so ``-3`` keeps
        the most recent 1.5 turns uncached).
        """
        # Always strip old markers so they don't accumulate across turns.
        cloned = _strip_message_caches(messages)

        if not self._enable_caching or len(cloned) < 4:
            return cloned

        breakpoint_idx = len(cloned) - 3
        if breakpoint_idx < 0:
            return cloned

        msg = cloned[breakpoint_idx]
        content = msg.get("content")

        if isinstance(content, str):
            msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list):
            new_content: list[dict[str, Any]] = []
            found_text = False
            for i in range(len(content) - 1, -1, -1):
                block = content[i]
                if isinstance(block, dict) and block.get("type") == "text":
                    new_content = [dict(b) for b in content]
                    new_content[i] = {
                        **new_content[i],
                        "cache_control": {"type": "ephemeral"},
                    }
                    found_text = True
                    break

            if not found_text:
                new_content = [dict(b) for b in content]
                new_content.append(
                    {
                        "type": "text",
                        "text": " ",
                        "cache_control": {"type": "ephemeral"},
                    }
                )

            msg["content"] = new_content

        return cloned


def _strip_message_caches(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove any ``cache_control`` keys from message content blocks."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        cloned = dict(msg)
        content = cloned.get("content")
        if isinstance(content, list):
            new_content: list[Any] = []
            for block in content:
                if isinstance(block, dict):
                    new_block = dict(block)
                    new_block.pop("cache_control", None)
                    new_content.append(new_block)
                else:
                    new_content.append(block)
            cloned["content"] = new_content
        result.append(cloned)
    return result


def _to_dict(message: Any) -> dict[str, Any]:
    """Coerce an Anthropic Message (or compatible stub) into a plain dict."""
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if hasattr(message, "dict"):
        return message.dict()
    return dict(message)


__all__ = ["AnthropicClient"]
