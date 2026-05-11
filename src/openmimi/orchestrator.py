"""Top-level orchestrator: load config, init resources, run sampling loop.

Wires `AnthropicClient`, `AgentBrowserTool`, `JsonlAuditLogger`, and the
sampling loop into a single entry point that the CLI can call. Tests inject
the parts directly via the constructor; production code uses
`Orchestrator.from_env` which reads config + env var for the API key and
instantiates real implementations.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from .audit import JsonlAuditLogger
from .config import load_config
from .config.schema import AppConfig
from .llm import AnthropicClient, OpenAIChatClient
from .llm.base import LLMClient
from .loop import _DEFAULT_SYSTEM_PROMPT, sampling_loop
from .memory.episodic import EpisodicStore
from .memory.site_store import SiteMemoryStore, extract_domain
from .planning import LLMPlanner, LLMVerifier, NullVerifier, Plan, Verifier
from .sub_agent import SubAgentRunner
from .tools import (
    AgentBrowserTool,
    BrowserAdvancedTool,
    BrowserExtractTool,
    BrowserInteractTool,
    BrowserNavigateTool,
    CodeTool,
    ComputerTool,
    FileTool,
    MemoryGrepTool,
    MemoryListTool,
    MemoryReadTool,
    MemoryWriteTool,
    ShellTool,
    ToolCollection,
)
# Imported from the submodule directly to avoid the cycle that re-exporting
# through ``openmimi.tools`` would introduce — see note in tools/__init__.py.
from .tools.sub_agent_tool import SubAgentTool
from .utils.ids import new_session_id

_DEFAULT_LLM_TIMEOUT_S = 90.0


def _format_plan_summary(plan: Plan) -> str:
    """Render a Plan as a numbered list for the system prompt.

    Empty plans return an empty string so callers can skip the section
    entirely. Each step is one line including its success criteria, so
    the Verifier and the Executor see the same ground truth.
    """
    if not plan.steps:
        return ""
    lines = ["Planned approach (verifier will grade each step):"]
    for i, step in enumerate(plan.steps, 1):
        lines.append(
            f"  {i}. {step.step} [success: {step.success_criteria}]"
        )
    lines.append(
        "\nYou must execute ALL steps above in order. "
        "Do not stop or reply to the user until every step is completed."
    )
    return "\n".join(lines)


def _build_system_prompt(
    domain: str | None,
    memory: SiteMemoryStore | None = None,
    plan: Plan | None = None,
) -> str:
    """Assemble system prompt from defaults + per-site memory + optional plan.

    Site memory (if present for *domain*) is appended verbatim so the agent
    starts each session with whatever lessons the previous one summarized.
    A non-empty Plan is rendered after the site memory so the Executor can
    see the steps the Verifier will grade against.
    """
    system = _DEFAULT_SYSTEM_PROMPT
    extras: list[str] = []

    if domain and memory is not None:
        site_text = memory.format_for_prompt(domain)
        if site_text:
            extras.append(site_text)

    if plan is not None:
        plan_text = _format_plan_summary(plan)
        if plan_text:
            extras.append(plan_text)

    if extras:
        system = f"{system}\n\n" + "\n\n".join(extras)
    return system


class Orchestrator:
    """Wires LLM client, tool collection, memory, and audit together."""

    def __init__(
        self,
        *,
        config: AppConfig,
        llm: LLMClient,
        tools: ToolCollection,
        audit: JsonlAuditLogger | None = None,
        memory: SiteMemoryStore | None = None,
        browser_engine: AgentBrowserTool | None = None,
        planner: LLMPlanner | None = None,
        verifier: Verifier | None = None,
        compress_llm: LLMClient | None = None,
        episodic: EpisodicStore | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = tools
        self.audit = audit
        self.memory = memory
        self._browser_engine = browser_engine
        self.planner = planner
        self.verifier = verifier
        self._compress_llm = compress_llm
        self._episodic = episodic
        # Updated at every ``run_task`` / ``run_chat_turn`` entry so
        # ``SubAgentTool`` can stamp the current parent session id onto
        # the sub-session ids it derives.
        self._current_session_id: str = ""

    @classmethod
    def from_env(
        cls, *, config: AppConfig | None = None
    ) -> Orchestrator:
        """Build an Orchestrator from `AppConfig` + env vars.

        Recognised env vars:
          - OPENMIMI_LLM_PROVIDER     - ``anthropic`` (default) or ``openai``
          - <cfg.llm.api_key_env>     (default ANTHROPIC_API_KEY) - required for
                                        Anthropic provider
          - ANTHROPIC_BASE_URL        - optional (Anthropic provider)
          - ANTHROPIC_MODEL           - optional, overrides cfg.llm.model
          - OPENAI_API_KEY            - required for OpenAI provider
          - OPENAI_BASE_URL           - optional; for OpenAI-compatible gateways
                                        (e.g. Alibaba compatible-mode)
          - OPENAI_MODEL              - optional; defaults to ANTHROPIC_MODEL then cfg.llm.model
          - OPENMIMI_LLM_TIMEOUT_S    - optional, per-LLM-request timeout
                                        in seconds (default 90)

        When ANTHROPIC_BASE_URL is set we conservatively disable prompt
        caching, because most Anthropic-compatible third-party endpoints
        (OpenAI-compat gateways, Aliyun MaaS, etc.) don't recognise the
        `cache_control` field and may reject the request.

        A stderr progress logger is wired by default so a slow upstream
        (or a hung request) is visible from the terminal instead of
        manifesting as a frozen process.
        """
        cfg = config or load_config()
        provider = (
            os.environ.get("OPENMIMI_LLM_PROVIDER") or cfg.llm.provider or "anthropic"
        ).strip().lower()
        timeout_s = _coerce_positive_float(
            os.environ.get("OPENMIMI_LLM_TIMEOUT_S"), _DEFAULT_LLM_TIMEOUT_S
        )

        if provider == "openai":
            api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not api_key:
                raise RuntimeError(
                    "missing env var 'OPENAI_API_KEY'; set it in .env or your shell before running"
                )
            base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
            model = (
                (os.environ.get("OPENAI_MODEL") or "").strip()
                or (os.environ.get("ANTHROPIC_MODEL") or "").strip()
                or cfg.llm.model
            )
            llm: LLMClient = OpenAIChatClient(
                api_key=api_key,
                model=model,
                base_url=base_url,
                request_timeout_s=timeout_s,
                progress_logger=_stderr_progress_logger,
            )
        else:
            api_key = os.environ.get(cfg.llm.api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"missing env var {cfg.llm.api_key_env!r}; "
                    "set it in .env or your shell before running"
                )

            base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
            model = os.environ.get("ANTHROPIC_MODEL") or cfg.llm.model
            enable_caching = base_url is None

            llm = AnthropicClient(
                api_key=api_key,
                model=model,
                base_url=base_url,
                enable_prompt_caching=enable_caching,
                request_timeout_s=timeout_s,
                progress_logger=_stderr_progress_logger,
            )

        tools = ToolCollection()
        browser_args = cfg.browser.args or []
        # Allow overriding via env var for quick experimentation
        extra_args = os.environ.get("OPENMIMI_BROWSER_ARGS", "")
        if extra_args:
            browser_args = [a.strip() for a in extra_args.split(",") if a.strip()]
        slow_mo_raw = os.environ.get("OPENMIMI_BROWSER_SLOW_MO_MS", "")
        slow_mo_ms = int(slow_mo_raw) if slow_mo_raw.strip().lstrip("-").isdigit() else 0
        browser_engine = AgentBrowserTool(
            download_dir=str(cfg.browser.download_dir),
            viewport=(cfg.browser.viewport_width, cfg.browser.viewport_height),
            headless=False,
            browser_args=browser_args,
            slow_mo_ms=slow_mo_ms,
        )
        # Register focused facade tools instead of the single god tool
        # to reduce per-request LLM context/token usage.
        tools.register(BrowserNavigateTool(browser_engine))
        tools.register(BrowserInteractTool(browser_engine))
        tools.register(BrowserExtractTool(browser_engine))
        tools.register(BrowserAdvancedTool(browser_engine))
        tools.register(ComputerTool(screen_dir=str(cfg.storage.screen_dir)))
        tools.register(ShellTool())
        tools.register(FileTool())
        tools.register(CodeTool())
        # Memory v2 (#9 stage 3) — give the LLM grep / read / write / list
        # access to data/memory/{episodic,sites,skills}. Episodic is
        # system-managed (loop appends); sites + skills are LLM-writable.
        tools.register(MemoryGrepTool())
        tools.register(MemoryReadTool())
        tools.register(MemoryWriteTool())
        tools.register(MemoryListTool())

        audit = JsonlAuditLogger(
            audit_dir=cfg.storage.audit_dir,
            screen_dir=cfg.storage.screen_dir,
        )
        memory = SiteMemoryStore()

        planner: LLMPlanner | None = None
        verifier: Verifier | None = None
        if cfg.enable_planning:
            planner = LLMPlanner(llm)
            verifier = LLMVerifier(llm)

        # Reuse the main LLMClient for cheap compression calls when the
        # summarize strategy is on (same decision as #7 stage 3c — keep
        # one channel rather than wire a separate cheap-model client).
        compress_llm: LLMClient | None = (
            llm if cfg.compression_strategy == "summarize" else None
        )

        episodic = EpisodicStore()

        # Wave 5 #8 stage 3 — register the sub_agent tool last, so the
        # SubAgentRunner sees the full parent toolset (excluding itself
        # by construction: registering before instantiating the runner
        # would cause infinite recursion in principle, even though the
        # sub-agent system prompt forbids it).
        sub_agent_runner = SubAgentRunner(
            llm=llm, parent_tools=tools, episodic=episodic
        )
        sub_agent_tool = SubAgentTool(sub_agent_runner)
        tools.register(sub_agent_tool)

        orch = cls(
            config=cfg,
            llm=llm,
            tools=tools,
            audit=audit,
            memory=memory,
            browser_engine=browser_engine,
            planner=planner,
            verifier=verifier,
            compress_llm=compress_llm,
            episodic=episodic,
        )
        # Now that the orchestrator instance exists, wire the SubAgentTool
        # to read its current session id on every call. Until this line
        # runs the tool returns "" as the parent id, which is fine — the
        # SubAgentRunner falls back to ``sub-<N>`` when there is no
        # parent id.
        sub_agent_tool.set_session_provider(lambda: orch._current_session_id)
        return orch

    def prewarm_browser(self) -> bool:
        """Surface the daemon prewarm state for REPL startup.

        ``AgentBrowserTool.__init__`` already kicks off a background warmup
        thread (5-min Windows cold-start), so by the time ``from_env`` returns
        the daemon is already on its way up. This method just *reports*
        whether that warmup is in flight so the CLI can print a status line
        before the welcome banner instead of leaving the user wondering why
        the first task is slow.

        Returns ``True`` if a warmup is currently running, ``False`` otherwise.
        """
        engine = self._browser_engine
        if engine is None:
            return False
        try:
            return bool(engine.is_warming_up())
        except Exception:
            return False

    async def run_task(self, task: str) -> dict[str, Any]:
        """Execute one user task end-to-end. Returns session id, messages, and final text."""
        session_id = new_session_id()
        # Publish session id so ``SubAgentTool`` can stamp derived
        # sub-session ids (``<sid>--sub-<N>``) on episodic appends.
        self._current_session_id = session_id
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        domain = extract_domain(task)

        plan = await self._maybe_plan_task(task, domain)
        system = _build_system_prompt(domain, self.memory, plan)
        verifier = self._effective_verifier(plan)

        try:
            await sampling_loop(
                messages=messages,
                tools=self.tools,
                llm=self.llm,
                session_id=session_id,
                audit=self.audit,
                episodic=self._episodic,
                max_turns=self.config.max_turns,
                only_n_most_recent_images=self.config.only_n_most_recent_images,
                max_context_turns=self.config.max_context_turns,
                system=system,
                verifier=verifier,
                plan=plan,
                compression_strategy=self.config.compression_strategy,
                max_context_tokens=self.config.max_context_tokens,
                compress_llm=self._compress_llm,
            )
        finally:
            await self.tools.close_all()

        await self._save_session_memory(messages, domain)

        return {
            "session_id": session_id,
            "messages": messages,
            "final_text": _extract_last_assistant_text(messages),
        }

    async def _summarize_session(
        self,
        messages: list[dict[str, Any]],
        domain: str,
    ) -> dict[str, Any] | None:
        """Ask the LLM to summarize lessons learned from this session."""
        recent = json.dumps(messages[-6:], ensure_ascii=False)
        prompt = (
            "You just finished a browser automation session. "
            "Summarize the key lessons learned in this session. "
            "Return ONLY a JSON object with this exact shape, no markdown, no explanation:\n"
            '{"known_refs": {"@e1": "description"}, '
            '"tips": ["tip1"], '
            '"failure_patterns": ["pattern1"], '
            '"success_paths": ["path1"]}\n'
            "If nothing useful was learned, return {}.\n\n"
            f"Recent conversation: {recent[:4000]}"
        )
        try:
            response = await self.llm.create(
                system="You are a session summarizer. Output only valid JSON.",
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=1024,
            )
            content = response.get("content", [])
            text = _extract_text_from_response_content(content)
            if not text:
                return None
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return None

    async def run_chat_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str,
        user_content: str,
    ) -> str:
        """Append one user message and run the tool loop until the assistant stops.

        Unlike `run_task`, this does **not** close tools — the same browser
        session and message history stay alive across turns so follow-up
        instructions see the prior page state and conversation context.

        All tool audit rows for every turn share the same ``session_id``.
        """
        # Publish session id for ``SubAgentTool`` before any tool call.
        self._current_session_id = session_id
        messages.append({"role": "user", "content": user_content})

        domain = extract_domain(user_content)
        plan = await self._maybe_plan_task(user_content, domain)
        system = _build_system_prompt(domain, self.memory, plan)
        verifier = self._effective_verifier(plan)

        await sampling_loop(
            messages=messages,
            tools=self.tools,
            llm=self.llm,
            session_id=session_id,
            audit=self.audit,
            episodic=self._episodic,
            max_turns=self.config.max_turns,
            only_n_most_recent_images=self.config.only_n_most_recent_images,
            max_context_turns=self.config.max_context_turns,
            system=system,
            verifier=verifier,
            plan=plan,
            compression_strategy=self.config.compression_strategy,
            max_context_tokens=self.config.max_context_tokens,
            compress_llm=self._compress_llm,
        )
        return _extract_last_assistant_text(messages)

    async def _save_session_memory(
        self, messages: list[dict[str, Any]], domain: str | None = None
    ) -> None:
        if not self.memory:
            return
        if domain is None:
            domain = _extract_domain_from_messages(messages)
        if not domain:
            return
        try:
            new_mem = await self._summarize_session(messages, domain)
            if new_mem:
                merged = self.memory.merge(domain, new_mem)
                self.memory.save(domain, merged)
        except Exception:
            pass

    async def _maybe_plan_task(
        self, task: str, domain: str | None
    ) -> Plan | None:
        """Run the Planner if planning is enabled and a planner is configured.

        Returns None when planning is off OR no planner was injected, so
        `sampling_loop` keeps its legacy unplanned behavior. Errors inside
        the planner fall back to a single-step Plan (the LLMPlanner does
        this itself), so this method never raises.
        """
        if not self.config.enable_planning or self.planner is None:
            return None
        context = self._planner_context(domain)
        try:
            return await self.planner.plan_task(task, context)
        except Exception:
            # Defensive: the planner is supposed to swallow its own errors,
            # but a buggy custom planner shouldn't take down the loop.
            return None

    def _planner_context(self, domain: str | None) -> str:
        """Build the `system_context` string passed to `LLMPlanner.plan_task`.

        Surfaces the per-site memory (if any) so the planner can craft
        steps that respect what the agent already knows about the domain.
        """
        if domain and self.memory is not None:
            site_text = self.memory.format_for_prompt(domain)
            if site_text:
                return site_text
        return ""

    def _effective_verifier(self, plan: Plan | None) -> Verifier | None:
        """Pick the Verifier instance to pass to `sampling_loop`.

        Only meaningful when we actually have a plan to grade against;
        without a plan, the loop ignores the verifier anyway, so we save
        an LLM call by returning None. If planning is enabled but no
        verifier was injected, fall back to a `NullVerifier` so the loop
        wiring stays consistent.
        """
        if plan is None or not self.config.enable_planning:
            return None
        return self.verifier or NullVerifier()

    async def save_chat_memory(self, messages: list[dict[str, Any]]) -> None:
        """Persist site memory after a chat session ends.

        Call this before ``close()`` so the session's lessons are
        summarized and written to disk once, not every turn.
        """
        await self._save_session_memory(messages)

    async def close(self) -> None:
        await self.tools.close_all()


def _stderr_progress_logger(message: str) -> None:
    """Best-effort stderr progress sink for LLM clients.

    Wrapped in try/except inside `_log_progress`, so a closed/broken stderr
    can never break the LLM call path.
    """
    print(message, file=sys.stderr, flush=True)


def _coerce_positive_float(raw: str | None, fallback: float) -> float:
    if raw is None or raw.strip() == "":
        return fallback
    try:
        v = float(raw)
    except ValueError:
        return fallback
    return v if v > 0 else fallback


def _extract_text_from_response_content(content: list[dict[str, Any]]) -> str:
    """Extract text from LLM response content blocks."""
    parts = [
        c.get("text", "")
        for c in content
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)


def _extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Pull all `text` blocks from the most recent assistant message."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p)
        if isinstance(content, str):
            return content
    return ""


def _extract_domain_from_messages(messages: list[dict[str, Any]]) -> str | None:
    """Find the first URL domain in any user message (search newest first)."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            domain = extract_domain(content)
            if domain:
                return domain
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    domain = extract_domain(block.get("text", ""))
                    if domain:
                        return domain
    return None


__all__ = ["Orchestrator"]
