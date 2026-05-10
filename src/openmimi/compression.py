"""Token budget + smart compression helpers (roadmap #5).

The loop currently shortens old tool_result text with a hard 400-char
truncation. That preserves the action trail but loses every structured
field the tool returned. This module ships the two pieces needed to
upgrade that:

  * ``estimate_tokens(text)`` — a cheap, dependency-free token estimate
    suitable for soft budget gates (``len // 4`` heuristic).
  * ``compress_tool_result(text, llm, *, target_chars)`` — ask a cheap
    LLM to produce a structured 3-line summary; if the LLM is missing,
    fails, or returns garbage, fall back to plain truncation so the
    caller never has to handle errors.

Both functions are pure library code with no orchestration coupling —
stage 3 wires them into ``loop.py``, stage 4 lets ``Orchestrator``
inject the LLM. ``compress_tool_result`` accepts ``llm=None`` so
the loop can call it uniformly even before the LLM is wired.
"""
from __future__ import annotations

from typing import Any, Protocol

_TRUNCATED_SUFFIX = "\n... [truncated to save context]"
_COMPRESSED_SUFFIX = "\n... [compressed by LLM]"

_COMPRESS_SYSTEM = (
    "You compress long tool-call outputs from a browser agent into a "
    "compact summary the agent can scan later. Output EXACTLY three "
    "short lines, no markdown, no prose:\n"
    "Did: <one sentence — what the tool did>\n"
    "Saw: <one sentence — what the page/data looked like>\n"
    "Data: <one sentence — key fields, URLs, IDs, error codes>\n"
    "If a line has no useful content, write 'Did: (n/a)' etc. "
    "Never quote the raw output back."
)


class _CompressLLM(Protocol):
    """Subset of ``LLMClient`` that ``compress_tool_result`` actually uses."""

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> dict[str, Any]: ...


def estimate_tokens(text: str) -> int:
    """Approximate token count for *text*.

    Uses the classic ``len // 4`` heuristic which is good enough for
    soft budget gates without pulling in a tokenizer dependency. Mixed
    ASCII + CJK text averages roughly 4 chars/token across the models
    we care about (Claude / GPT-4 / Qwen). Returns ``0`` for empty
    input and at least ``1`` for any non-empty string so callers can
    avoid divide-by-zero in budget math.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate(text: str, target_chars: int) -> str:
    """Hard truncate with the same suffix the loop already uses."""
    if len(text) <= target_chars:
        return text
    return text[:target_chars] + _TRUNCATED_SUFFIX


def _extract_response_text(response: dict[str, Any]) -> str:
    """Pull text content out of an ``LLMClient.create()`` response.

    Mirrors the helper in ``planning.py`` so this module stays
    self-contained — both Anthropic-style (``content: list[block]``)
    and bare-string content shapes are handled.
    """
    content = response.get("content") or []
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


async def compress_tool_result(
    text: str,
    llm: _CompressLLM | None,
    *,
    target_chars: int = 500,
    max_tokens: int = 300,
    max_input_chars: int = 8000,
) -> str:
    """Compress a long tool_result text into a structured summary.

    Pipeline:
      1. If *text* is short (<= ``target_chars``), return it untouched.
      2. If *llm* is None, fall back to plain truncation.
      3. Otherwise ask the LLM for a 3-line "Did / Saw / Data" summary.
         The first ``max_input_chars`` of *text* are sent so a single
         huge tool_result can't blow up the compression call itself.
      4. On any failure (network, empty reply, oversized reply), fall
         back to plain truncation — this function never raises.

    The returned summary always ends with ``_COMPRESSED_SUFFIX`` so the
    caller can tell at a glance whether compression actually fired.
    """
    if not text:
        return text
    if len(text) <= target_chars:
        return text
    if llm is None:
        return _truncate(text, target_chars)

    payload = text[:max_input_chars]
    user_prompt = (
        "Compress this tool output. Reply with EXACTLY three lines "
        "prefixed 'Did:', 'Saw:', 'Data:' — no other text.\n\n"
        "--- tool output start ---\n"
        f"{payload}\n"
        "--- tool output end ---"
    )
    try:
        response = await llm.create(
            system=_COMPRESS_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[],
            max_tokens=max_tokens,
        )
    except Exception:
        return _truncate(text, target_chars)

    summary = _extract_response_text(response).strip()
    if not summary:
        return _truncate(text, target_chars)

    # Guard against an LLM that ignored the line cap and dumped a
    # near-copy of the input. Capping at 2x target_chars keeps the
    # win meaningful even if the model misbehaves.
    cap = target_chars * 2
    if len(summary) > cap:
        summary = summary[:cap]
    return summary + _COMPRESSED_SUFFIX


__all__ = [
    "compress_tool_result",
    "estimate_tokens",
]
