"""Sampling loop: LLM <-> tool_use <-> tool_result.

Mirrors the structure of `anthropic-quickstarts/computer-use-demo/loop.py`
but adapted to OpenMimi's ToolCollection / LLMClient / AuditSink interfaces.

The loop:
  1. Calls the LLM with the current message history and tool schemas.
  2. Appends the assistant message verbatim.
  3. If the assistant requested no tool, returns.
  4. Otherwise, dispatches every `tool_use` block through `ToolCollection.run`,
     translates each `ToolResult` back into a `tool_result` content block, and
     appends them as a single `user` message.
  5. Optionally trims older screenshot images so the prompt does not grow
     without bound (`only_n_most_recent_images`).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from typing import Any, Protocol

from .llm.base import LLMClient
from .tools.collection import ToolCollection
from .tools.errors import ErrorCode
from .tools.result import ToolResult

_log = logging.getLogger(__name__)


class AuditSink(Protocol):
    """Minimal audit surface consumed by the sampling loop.

    `JsonlAuditLogger` satisfies this Protocol; tests can plug in any object
    with the same shape.
    """

    def save_screenshot(
        self, *, session_id: str, step: int, png_bytes: bytes
    ) -> str: ...

    def log_tool_call(
        self,
        *,
        session_id: str,
        step: int,
        tool: str,
        tool_input: dict[str, Any],
        result_summary: str,
        is_error: bool,
        error_code: str | None,
        image_path: str | None,
        duration_ms: int,
    ) -> None: ...


_DEFAULT_SYSTEM_PROMPT = """
You are OpenMimi, a local Windows AI agent. You operate tools using the
Anthropic tool_use protocol. Prefer semantic locators (target_text /
target_hint) over raw coordinates whenever possible. After each tool
call you receive a fresh screenshot; observe carefully before deciding
the next action. Stop calling tools and reply in plain text once the
task is complete.

Browser best practices:
- eval: wrap multi-line JS in an IIFE like (() => { ...; return value; })().
- Checkboxes: use action='check' / 'uncheck'; never click them.
- React SPA: if a click does nothing, retry with force=true.
  Use action='react_fill' instead of 'fill' for React/Vue controlled inputs.
- Keyboard shortcuts: use action='key_combo' with a 'keys' array.
- Lazy content: use action='wait_for' with ref or target_text instead of sleeping.
- Coordinates: use action='get_box' before OS-level mouse actions.
- CAPTCHA: focus the browser window, then use computer.mouse_drag with
  a SLOW horizontal drag (steps=80, delay_ms=25) at exact screen coordinates.
- First navigate may take 2-5 min while Chromium starts; retry once if timed out.
"""

_RESULT_SUMMARY_MAX_CHARS = 4000
_OMITTED_IMAGE_PLACEHOLDER = "[image omitted to save context]"
_DEFAULT_TOOL_TIMEOUT_S = 300.0


def _tool_run_timeout_seconds() -> float | None:
    """Per-tool wall-clock budget; ``None`` means no asyncio-level cap.

    Browser-use/CDP calls can hang indefinitely on some sites; this is
    independent from Anthropic's HTTP timeout (OPENMIMI_LLM_TIMEOUT_S).
    Set OPENMIMI_TOOL_TIMEOUT_S to ``0`` to disable (not recommended).
    """
    raw = os.environ.get("OPENMIMI_TOOL_TIMEOUT_S", "").strip().lower()
    if raw in ("", "none", "off", "inf", "infinity"):
        return _DEFAULT_TOOL_TIMEOUT_S
    if raw == "0":
        return None
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_TOOL_TIMEOUT_S
    return None if v <= 0 else v


def _tool_progress(message: str) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _preview_tool_input(tool_input: dict[str, Any]) -> str:
    try:
        s = json.dumps(tool_input, ensure_ascii=False)
    except TypeError:
        s = str(tool_input)
    return (s[:160] + "...") if len(s) > 160 else s


async def sampling_loop(
    *,
    messages: list[dict[str, Any]],
    tools: ToolCollection,
    llm: LLMClient,
    session_id: str,
    audit: AuditSink | None = None,
    system: str = _DEFAULT_SYSTEM_PROMPT,
    max_turns: int = 30,
    only_n_most_recent_images: int = 2,
    max_tokens: int = 4096,
) -> list[dict[str, Any]]:
    """Run the LLM-driven tool_use loop.

    Returns the final messages list (the same list passed in, mutated in place).

    The loop terminates when:
      - the assistant produces no `tool_use` block (or `stop_reason != "tool_use"`),
      - the turn budget `max_turns` is exhausted,
      - any unhandled exception escapes (caller decides whether to retry).
    """
    step = 0

    for _turn in range(max_turns):
        _msg_count = len(messages)
        _text_len = sum(
            len(str(m.get("content", "")))
            for m in messages
        )
        print(
            f"[loop] turn {_turn + 1}: {_msg_count} messages, ~{_text_len} chars",
            file=sys.stderr,
            flush=True,
        )

        _dump_prompt(
            session_id=session_id,
            turn=_turn + 1,
            system=system,
            messages=messages,
            tools=tools.to_params(),
        )

        response = await llm.create(
            system=system,
            messages=messages,
            tools=tools.to_params(),
            max_tokens=max_tokens,
        )

        content = response.get("content") or []
        messages.append({"role": "assistant", "content": content})

        stop_reason = response.get("stop_reason")
        tool_use_blocks = [
            b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

        if not tool_use_blocks or stop_reason != "tool_use":
            return messages

        tool_result_blocks: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            step += 1
            block_id = str(block.get("id", ""))
            tool_name = str(block.get("name", ""))
            tool_input = block.get("input") or {}

            protocol_err = _detect_protocol_error(tool_name, tool_input)
            if protocol_err is not None:
                tool_result_blocks.append(
                    _make_error_result_block(block_id, protocol_err)
                )
                if audit is not None:
                    audit.log_tool_call(
                        session_id=session_id,
                        step=step,
                        tool=tool_name or "<unknown>",
                        tool_input=tool_input,
                        result_summary=protocol_err[
                            :_RESULT_SUMMARY_MAX_CHARS
                        ],
                        is_error=True,
                        error_code=ErrorCode.TOOL_INTERNAL_ERROR.value,
                        image_path=None,
                        duration_ms=0,
                    )
                continue

            _tool_progress(
                f"[tool] step {step}: {tool_name} starting: "
                f"{_preview_tool_input(tool_input)}"
            )
            t0 = time.monotonic()
            tout = _tool_run_timeout_seconds()
            # The Chromium daemon can take several minutes to cold-start on
            # Windows (especially the first time). Give browser tools a
            # longer leash so the LLM doesn't have to retry unnecessarily.
            if tool_name.startswith("browser_") and tout is not None:
                tout = max(tout, 600.0)
            try:
                run_coro = tools.run(tool_name, tool_input)
                if tout is not None:
                    result = await asyncio.wait_for(run_coro, timeout=tout)
                else:
                    result = await run_coro
            except (TimeoutError, asyncio.TimeoutError):
                duration_ms = int((time.monotonic() - t0) * 1000)
                err_text = (
                    f"Tool timed out after {tout}s (OPENMIMI_TOOL_TIMEOUT_S). "
                    "The browser automation call did not finish; try the "
                    "instruction again or restart chat if the window is stuck."
                )
                tool_result_blocks.append(
                    _make_error_result_block(block_id, err_text)
                )
                if audit is not None:
                    audit.log_tool_call(
                        session_id=session_id,
                        step=step,
                        tool=tool_name,
                        tool_input=tool_input,
                        result_summary=err_text[:_RESULT_SUMMARY_MAX_CHARS],
                        is_error=True,
                        error_code=ErrorCode.TOOL_INTERNAL_ERROR.value,
                        image_path=None,
                        duration_ms=duration_ms,
                    )
                _tool_progress(
                    f"[tool] step {step}: {tool_name} TIMEOUT after {tout}s"
                )
                if tool_name.startswith("browser_"):
                    bt = os.environ.get("OPENMIMI_BROWSER_TRACE", "").strip().lower()
                    if bt not in ("1", "true", "yes", "on"):
                        print(
                            "[browser-trace] Set OPENMIMI_BROWSER_TRACE=1 (env or .env) "
                            "to print phase timings on stderr and find the stalled await.",
                            file=sys.stderr,
                            flush=True,
                        )
                continue
            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                err_text = (
                    f"Tool internal error: {exc.__class__.__name__}: {exc}"
                )
                tool_result_blocks.append(
                    _make_error_result_block(block_id, err_text)
                )
                if audit is not None:
                    audit.log_tool_call(
                        session_id=session_id,
                        step=step,
                        tool=tool_name,
                        tool_input=tool_input,
                        result_summary=err_text[:_RESULT_SUMMARY_MAX_CHARS],
                        is_error=True,
                        error_code=ErrorCode.TOOL_INTERNAL_ERROR.value,
                        image_path=None,
                        duration_ms=duration_ms,
                    )
                _tool_progress(
                    f"[tool] step {step}: {tool_name} error: {exc!s}"
                )
                continue

            duration_ms = int((time.monotonic() - t0) * 1000)
            # Flag CAPTCHA presence so operators notice in long logs
            if result.details and result.details.get("error_code") == ErrorCode.CAPTCHA_DETECTED:
                _tool_progress(
                    f"\n{'='*60}\n"
                    f"[tool] step {step}: CAPTCHA detected – analyzing screenshot\n"
                    f"{'='*60}"
                )
            tool_result_blocks.append(_to_tool_result_block(block_id, result))
            _tool_progress(
                f"[tool] step {step}: {tool_name} finished in {duration_ms}ms"
            )

            if audit is not None:
                image_path = _persist_screenshot_if_any(
                    audit=audit,
                    session_id=session_id,
                    step=step,
                    base64_image=result.base64_image,
                )
                audit.log_tool_call(
                    session_id=session_id,
                    step=step,
                    tool=tool_name,
                    tool_input=tool_input,
                    result_summary=(result.output or "")[
                        :_RESULT_SUMMARY_MAX_CHARS
                    ],
                    is_error=result.is_error,
                    error_code=_extract_error_code(result),
                    image_path=image_path,
                    duration_ms=duration_ms,
                )

        messages.append({"role": "user", "content": tool_result_blocks})
        _trim_old_images(messages, only_n_most_recent_images)

    return messages


def _detect_protocol_error(
    tool_name: str, tool_input: dict[str, Any]
) -> str | None:
    """Return a human-readable explanation for malformed tool_use blocks.

    Some Anthropic-compatible proxies occasionally emit corrupt tool_use
    fragments: missing `name`, or a payload that contains only the partial
    raw arguments string from a streaming chunk. Without explicit handling
    the loop would either KeyError on an unknown tool or invoke a tool
    with a useless input dict; instead we surface a clear protocol error
    so the model resynthesises a clean tool call on the next turn.
    """
    if not tool_name:
        return (
            "tool_use block was missing 'name'; this is usually a streaming "
            "fragment from the upstream provider. Please reissue the full "
            "tool call with a valid `name` and `input`."
        )
    if (
        isinstance(tool_input, dict)
        and len(tool_input) == 1
        and "raw_arguments" in tool_input
    ):
        return (
            "tool_use input was a `raw_arguments` fragment from the upstream "
            "provider, not a structured object. Please reissue the call with "
            "a complete JSON `input` object."
        )
    return None


def _to_tool_result_block(tool_use_id: str, result: ToolResult) -> dict[str, Any]:
    """Translate a `ToolResult` into an Anthropic `tool_result` content block."""
    sub_content: list[dict[str, Any]] = []
    if result.output:
        sub_content.append({"type": "text", "text": result.output})
    if result.base64_image:
        sub_content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": result.base64_image,
                },
            }
        )
    if not sub_content:
        sub_content.append({"type": "text", "text": "(no output)"})

    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": sub_content,
    }
    if result.is_error:
        block["is_error"] = True
    return block


def _make_error_result_block(tool_use_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [{"type": "text", "text": text}],
        "is_error": True,
    }


def _extract_error_code(result: ToolResult) -> str | None:
    if not result.details:
        return None
    code = result.details.get("error_code")
    return str(code) if code else None


def _persist_screenshot_if_any(
    *,
    audit: AuditSink,
    session_id: str,
    step: int,
    base64_image: str | None,
) -> str | None:
    if not base64_image:
        return None
    try:
        png_bytes = base64.b64decode(base64_image, validate=False)
    except Exception:
        return None
    try:
        return audit.save_screenshot(
            session_id=session_id, step=step, png_bytes=png_bytes
        )
    except Exception:
        return None


def _trim_old_images(messages: list[dict[str, Any]], keep_n: int) -> None:
    """Replace all but the most recent `keep_n` screenshot images with text stubs.

    Walks every `user` -> `tool_result` -> `image` sub-block in chronological
    order, then rewrites older entries to a small text marker. The action
    trail (text outputs, ordering) stays intact so the model can still reason
    about earlier steps.
    """
    if keep_n < 0:
        return

    image_locations: list[tuple[list[Any], int]] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            sub = block.get("content")
            if not isinstance(sub, list):
                continue
            for i, item in enumerate(sub):
                if isinstance(item, dict) and item.get("type") == "image":
                    image_locations.append((sub, i))

    if len(image_locations) <= keep_n:
        return

    n_to_drop = len(image_locations) - keep_n
    _log.warning(
        "trimming %d older screenshot(s) from context; keeping last %d (only_n_most_recent_images)",
        n_to_drop,
        keep_n,
    )
    for parent, idx in image_locations[:n_to_drop]:
        parent[idx] = {"type": "text", "text": _OMITTED_IMAGE_PLACEHOLDER}


def _dump_prompt(
    *,
    session_id: str,
    turn: int,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> None:
    """Write the full LLM request payload to disk for debugging."""
    from pathlib import Path

    try:
        prompt_dir = Path("data/prompts")
        prompt_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "turn": turn,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        path = prompt_dir / f"{session_id}_turn{turn}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


__all__ = ["AuditSink", "sampling_loop"]
