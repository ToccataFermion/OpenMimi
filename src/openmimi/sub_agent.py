"""Sub-agent isolation (roadmap #8).

A SubAgent runs an inner ``sampling_loop`` with a filtered ``ToolCollection``
so a "one-shot, high-cost" sub-task (CAPTCHA solving, deep search, long PDF
read) can finish without polluting the parent's context window. Only the
final assistant text comes back to the parent — intermediate screenshots,
tool traffic, and scratchpad messages never enter the parent's history.

Three pieces here:

  * ``SubAgentRequest`` / ``SubAgentResult`` — wire-shaped dataclasses so
    callers (and the ``sub_agent`` ToolBase wrapper in stage 3) can build
    structured requests / consume structured results.
  * ``filter_tools`` — pull a named subset of tools out of the parent
    ``ToolCollection`` (sharing instances, NOT copies, so a daemon-backed
    browser tool keeps its live session).
  * ``SubAgentRunner`` — orchestrates the actual ``sampling_loop`` call
    with a minimal sub-agent system prompt, isolated session id, and
    optional parent ``EpisodicSink`` / ``AuditSink`` pass-through.

Stage 3 (the ``sub_agent`` ``ToolBase``) wraps a ``SubAgentRunner`` and
exposes it to the main LLM through the standard tool protocol.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .llm.base import LLMClient
from .loop import AuditSink, EpisodicSink, sampling_loop
from .tools.collection import ToolCollection

_log = logging.getLogger(__name__)


_SUB_AGENT_SYSTEM_PROMPT = """
You are a focused sub-agent spawned by a parent OpenMimi agent. The parent
delegated one specific sub-task to you and will only ever see your final
plain-text reply — none of your intermediate tool calls, screenshots, or
scratch reasoning are returned to the parent.

Rules:
- Stay narrowly on the delegated task; do not branch into unrelated work.
- Call tools as needed, but stop as soon as you can answer the parent.
- When you have the answer, reply in plain text with no tool call. That
  reply IS the result the parent receives — make it concise, factual, and
  self-contained.
- If you cannot make progress, reply with a short explanation of what you
  tried and why it failed, so the parent can re-plan.
"""


@dataclass
class SubAgentRequest:
    """Inputs for one ``SubAgentRunner.run`` call.

    ``allowed_tools=None`` means inherit the parent's full toolset; an
    empty list explicitly disables every tool (the sub-agent can only
    reason and reply); a non-empty list narrows to exactly those names
    that exist on the parent (missing names are silently skipped — the
    caller can pre-validate against ``ToolCollection.names()`` if it
    cares).

    ``parent_session_id`` + ``sub_index`` together form the sub-session
    id used for episodic / audit isolation — see
    ``default_sub_session_id``.

    ``system_addendum`` is appended verbatim to the sub-agent system
    prompt so callers can inject task-specific guidance (e.g. "the page
    is in Chinese; reply in English").
    """

    task: str
    allowed_tools: list[str] | None = None
    max_turns: int = 10
    system_addendum: str = ""
    parent_session_id: str = ""
    sub_index: int = 0


@dataclass
class SubAgentResult:
    """Output from one ``SubAgentRunner.run`` call.

    ``text`` is the final assistant message — the only thing the parent
    sees. ``turns`` is the number of inner sampling-loop iterations the
    sub-agent consumed (useful for the parent's budget accounting).
    ``sub_session_id`` mirrors the value the loop used so the caller
    can later grep episodic memory for the sub-run.

    On error (exception or empty reply), ``is_error=True`` and
    ``error_message`` carries a short reason. ``text`` is also populated
    with a human-readable error so the parent LLM sees something useful
    even if it does not inspect ``is_error``.
    """

    text: str
    turns: int = 0
    sub_session_id: str = ""
    is_error: bool = False
    error_message: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)


def default_sub_session_id(parent_session_id: str, sub_index: int) -> str:
    """Build the sub-agent's session id from the parent's id + index.

    Format: ``<parent>--sub-<N>``. If the parent has no id (empty / None),
    falls back to ``sub-<N>`` so episodic store still gets something
    valid to write under.
    """
    parent = (parent_session_id or "").strip()
    if not parent:
        return f"sub-{sub_index}"
    return f"{parent}--sub-{sub_index}"


def filter_tools(
    parent: ToolCollection,
    allowed: list[str] | None,
) -> ToolCollection:
    """Build a sub-ToolCollection containing the named subset of parent's tools.

    Semantics:
      * ``allowed is None`` → return a fresh ``ToolCollection`` registering
        every parent tool (sharing instances; daemon state is preserved).
      * ``allowed == []``  → return an empty ``ToolCollection``.
      * non-empty list      → register only the tools whose name appears in
        ``allowed``; missing names are skipped with a warning so a typo in
        ``allowed`` doesn't kill the sub-agent — it just runs with fewer
        tools.

    The parent ``ToolCollection`` is NOT mutated. The returned collection
    shares ``ToolBase`` instances with the parent: this is intentional —
    closing it (``close_all``) would close the parent's daemon too, so
    the sub-agent runner deliberately does NOT call ``close_all`` on its
    own collection.
    """
    sub = ToolCollection()
    if allowed is None:
        for name in parent.names():
            tool = parent.get(name)
            if tool is not None:
                sub.register(tool)
        return sub
    seen: set[str] = set()
    for name in allowed:
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        tool = parent.get(name)
        if tool is None:
            _log.warning(
                "sub-agent allowed_tools mentions unknown tool %r; skipping",
                name,
            )
            continue
        sub.register(tool)
    return sub


def _build_sub_system_prompt(addendum: str) -> str:
    """Compose the sub-agent system prompt from the base + caller addendum."""
    base = _SUB_AGENT_SYSTEM_PROMPT
    if addendum and addendum.strip():
        return f"{base}\n\n{addendum.strip()}"
    return base


def _count_assistant_turns(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") == "assistant")


def _final_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Pull the final assistant message's text content as a single string."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(c.get("text", ""))
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p)
            if joined:
                return joined
        return ""
    return ""


class SubAgentRunner:
    """Run sub-agent tasks against a parent's LLM + tool inventory.

    The runner is constructed once (typically by the orchestrator that
    owns the main LLM client and ToolCollection) and called many times
    via :meth:`run`. Each call yields a self-contained ``SubAgentResult``;
    no internal state leaks between sub-agents.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        parent_tools: ToolCollection,
        episodic: EpisodicSink | None = None,
        audit: AuditSink | None = None,
        max_tokens: int = 4096,
        only_n_most_recent_images: int = 2,
    ) -> None:
        self._llm = llm
        self._parent_tools = parent_tools
        self._episodic = episodic
        self._audit = audit
        self._max_tokens = max_tokens
        self._only_n_most_recent_images = only_n_most_recent_images

    async def run(self, request: SubAgentRequest) -> SubAgentResult:
        """Execute one sub-agent task end-to-end.

        Builds a fresh message list, filters tools per ``request.allowed_tools``,
        and runs ``sampling_loop`` with the sub-agent system prompt. Returns
        a ``SubAgentResult`` whose ``text`` is the sub-agent's final reply.

        Any exception is caught and translated into an ``is_error=True``
        result — sub-agent failures must never propagate up to crash the
        parent loop.
        """
        sub_session = default_sub_session_id(
            request.parent_session_id, request.sub_index
        )
        sub_tools = filter_tools(self._parent_tools, request.allowed_tools)
        system = _build_sub_system_prompt(request.system_addendum)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": request.task},
        ]

        if not request.task or not request.task.strip():
            return SubAgentResult(
                text="(sub-agent received empty task)",
                turns=0,
                sub_session_id=sub_session,
                is_error=True,
                error_message="empty task",
                messages=messages,
            )

        try:
            final = await sampling_loop(
                messages=messages,
                tools=sub_tools,
                llm=self._llm,
                session_id=sub_session,
                audit=self._audit,
                episodic=self._episodic,
                system=system,
                max_turns=max(1, request.max_turns),
                only_n_most_recent_images=self._only_n_most_recent_images,
                max_tokens=self._max_tokens,
            )
        except Exception as exc:
            _log.warning(
                "sub-agent %s raised %s: %s",
                sub_session,
                exc.__class__.__name__,
                exc,
            )
            return SubAgentResult(
                text=(
                    f"(sub-agent {sub_session} failed: "
                    f"{exc.__class__.__name__}: {exc})"
                ),
                turns=_count_assistant_turns(messages),
                sub_session_id=sub_session,
                is_error=True,
                error_message=f"{exc.__class__.__name__}: {exc}",
                messages=messages,
            )

        text = _final_assistant_text(final)
        turns = _count_assistant_turns(final)
        if not text:
            return SubAgentResult(
                text=(
                    "(sub-agent produced no final text; the loop ended "
                    "without a plain-text reply — likely hit max_turns "
                    f"or stopped on tool_use. turns={turns})"
                ),
                turns=turns,
                sub_session_id=sub_session,
                is_error=True,
                error_message="no final text",
                messages=final,
            )
        return SubAgentResult(
            text=text,
            turns=turns,
            sub_session_id=sub_session,
            is_error=False,
            error_message=None,
            messages=final,
        )


__all__ = [
    "SubAgentRequest",
    "SubAgentResult",
    "SubAgentRunner",
    "default_sub_session_id",
    "filter_tools",
]
