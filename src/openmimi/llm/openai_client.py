"""OpenAI Chat Completions implementation of LLMClient.

OpenMimi's sampling loop and tools are Anthropic tool_use/tool_result shaped.
This client translates that internal shape to OpenAI Chat Completions
(``/v1/chat/completions``) and maps tool calls back to Anthropic-shaped
content blocks so the rest of the codebase stays unchanged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

_DEFAULT_REQUEST_TIMEOUT_S = 90.0


def _tool_result_to_openai_tool_content(block: dict[str, Any]) -> str:
    """Flatten Anthropic ``tool_result`` blocks into tool message content."""
    parts: list[str] = []
    sub = block.get("content")
    if not isinstance(sub, list):
        return str(sub or "")

    for item in sub:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "image":
            # Some OpenAI-compatible gateways support image parts in tool messages;
            # for maximum compatibility we embed as a markdown data URL.
            src = item.get("source") or {}
            if isinstance(src, dict) and src.get("type") == "base64":
                b64 = str(src.get("data", ""))
                media = str(src.get("media_type", "image/png"))
                parts.append(f"![screenshot](data:{media};base64,{b64})")

    body = "\n".join(p for p in parts if p).strip()
    return body if body else "(no output)"


def _user_blocks_to_openai_content(blocks: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Anthropic multimodal user blocks -> OpenAI ``content``."""
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return str(blocks[0].get("text", ""))

    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            out.append({"type": "text", "text": str(b.get("text", ""))})
        elif b.get("type") == "image":
            src = b.get("source") or {}
            if isinstance(src, dict) and src.get("type") == "base64":
                b64 = str(src.get("data", ""))
                media = str(src.get("media_type", "image/png"))
                out.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{b64}"},
                    }
                )
    return out if out else ""


def _anthropic_messages_to_openai(
    *, system: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Convert internal Anthropic-shaped history to OpenAI chat messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for m in messages:
        role = m.get("role")
        c = m.get("content")

        if role == "user":
            if isinstance(c, str):
                out.append({"role": "user", "content": c})
                continue
            if isinstance(c, list):
                # Tool results are encoded as a list of tool_result blocks.
                if c and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    for b in c:
                        tool_call_id = str(b.get("tool_use_id", ""))
                        out.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": _tool_result_to_openai_tool_content(b),
                            }
                        )
                    continue
                out.append({"role": "user", "content": _user_blocks_to_openai_content(c)})
                continue

            out.append({"role": "user", "content": str(c)})
            continue

        if role == "assistant":
            if isinstance(c, str):
                out.append({"role": "assistant", "content": c})
                continue

            if isinstance(c, list):
                text_chunks: list[str] = []
                tool_calls: list[dict[str, Any]] = []

                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        text_chunks.append(str(b.get("text", "")))
                    elif b.get("type") == "tool_use":
                        tid = str(b.get("id", ""))
                        name = str(b.get("name", ""))
                        payload = b.get("input") if isinstance(b.get("input"), dict) else {}
                        try:
                            args = json.dumps(payload, ensure_ascii=False)
                        except TypeError:
                            args = "{}"
                        tool_calls.append(
                            {
                                "id": tid,
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }
                        )

                text_joined = "\n".join(t for t in text_chunks if t).strip()
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": text_joined if text_joined else None,
                }
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out.append(msg)
                continue

            out.append({"role": "assistant", "content": str(c)})
            continue

        out.append({"role": str(role), "content": str(c)})

    return out


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions -> OpenAI function tools."""
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", ""))
        if not name:
            continue
        params = t.get("input_schema")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(t.get("description", "")),
                    "parameters": params,
                },
            }
        )
    return out


def _openai_completion_to_anthropic_shape(completion: Any) -> dict[str, Any]:
    """Map OpenAI chat completion -> Anthropic-shaped response dict."""
    choice = completion.choices[0]
    msg = choice.message

    content: list[dict[str, Any]] = []
    text = getattr(msg, "content", None)
    if isinstance(text, str) and text.strip():
        content.append({"type": "text", "text": text})

    raw_calls = getattr(msg, "tool_calls", None) or []
    for tc in raw_calls:
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", "") if fn is not None else ""
        raw_args = getattr(fn, "arguments", "") if fn is not None else ""
        try:
            parsed: dict[str, Any] = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            parsed = {"raw_arguments": raw_args}
        tid = str(getattr(tc, "id", "") or "")
        content.append({"type": "tool_use", "id": tid, "name": name, "input": parsed})

    finish = str(getattr(choice, "finish_reason", "") or "")
    if finish == "tool_calls" or any(b.get("type") == "tool_use" for b in content):
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"
    return {"content": content, "stop_reason": stop_reason}


class OpenAIChatClient:
    """LLMClient backed by ``openai.AsyncOpenAI`` chat completions."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        progress_logger: Callable[[str], None] | None = None,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._request_timeout_s = request_timeout_s
        self._progress_logger = progress_logger
        self._call_index = 0

        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {"api_key": api_key, "timeout": request_timeout_s}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = AsyncOpenAI(**kwargs)

    def _log(self, message: str) -> None:
        if self._progress_logger is None:
            return
        try:
            self._progress_logger(message)
        except Exception:
            pass

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        oai_messages = _anthropic_messages_to_openai(system=system, messages=messages)
        oai_tools = _anthropic_tools_to_openai(tools)

        self._call_index += 1
        idx = self._call_index
        self._log(
            f"[llm/openai] turn {idx}: requesting "
            f"(timeout={self._request_timeout_s:.0f}s)..."
        )
        t0 = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": oai_messages,
                "max_tokens": max_tokens,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools
                kwargs["tool_choice"] = "auto"
            result = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            inner = getattr(exc, "__cause__", None)
            cause = f" cause={inner!r}" if inner is not None else ""
            self._log(
                f"[llm/openai] turn {idx}: failed after {elapsed:.1f}s: "
                f"{exc.__class__.__name__}: {exc}{cause}"
            )
            raise

        elapsed = time.monotonic() - t0
        shaped = _openai_completion_to_anthropic_shape(result)
        stop = shaped.get("stop_reason") or "?"
        self._log(f"[llm/openai] turn {idx}: response in {elapsed:.1f}s (stop={stop})")
        return shaped


__all__ = ["OpenAIChatClient"]

