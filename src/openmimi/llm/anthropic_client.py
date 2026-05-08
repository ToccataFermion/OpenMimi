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
                messages=messages,
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
