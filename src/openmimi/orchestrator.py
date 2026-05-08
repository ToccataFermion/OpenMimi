"""Top-level orchestrator: load config, init resources, run sampling loop.

Wires `AnthropicClient`, `BrowserTool`, `JsonlAuditLogger`, and the sampling
loop into a single entry point that the CLI can call. Tests inject the
parts directly via the constructor; production code uses `Orchestrator.from_env`
which reads config + env var for the API key and instantiates real
implementations.
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
from .memory.site_store import SiteMemoryStore, extract_domain
from .skills import format_skill_for_prompt
from .tools import AgentBrowserTool, CodeTool, ComputerTool, FileTool, ShellTool, ToolCollection
from .utils.ids import new_session_id

_DEFAULT_LLM_TIMEOUT_S = 90.0


def _build_system_prompt(domain: str | None) -> str:
    """Assemble system prompt from defaults + site memory + skill files."""
    system = _DEFAULT_SYSTEM_PROMPT
    extras: list[str] = []

    if domain:
        skill_text = format_skill_for_prompt(domain)
        if skill_text:
            extras.append(skill_text)

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
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = tools
        self.audit = audit
        self.memory = memory

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
        tools.register(
            AgentBrowserTool(
                download_dir=str(cfg.browser.download_dir),
                viewport=(cfg.browser.viewport_width, cfg.browser.viewport_height),
                headless=False,
                browser_args=browser_args,
                slow_mo_ms=slow_mo_ms,
            )
        )
        tools.register(ComputerTool(screen_dir=str(cfg.storage.screen_dir)))
        tools.register(ShellTool())
        tools.register(FileTool())
        tools.register(CodeTool())

        audit = JsonlAuditLogger(
            audit_dir=cfg.storage.audit_dir,
            screen_dir=cfg.storage.screen_dir,
        )
        memory = SiteMemoryStore()

        return cls(config=cfg, llm=llm, tools=tools, audit=audit, memory=memory)

    async def run_task(self, task: str) -> dict[str, Any]:
        """Execute one user task end-to-end. Returns session id, messages, and final text."""
        session_id = new_session_id()
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        domain = extract_domain(task)

        system = _build_system_prompt(domain)

        try:
            await sampling_loop(
                messages=messages,
                tools=self.tools,
                llm=self.llm,
                session_id=session_id,
                audit=self.audit,
                max_turns=self.config.max_turns,
                only_n_most_recent_images=self.config.only_n_most_recent_images,
                system=system,
            )
        finally:
            await self.tools.close_all()

        if self.memory and domain:
            try:
                new_mem = await self._summarize_session(messages, domain)
                if new_mem:
                    merged = self.memory.merge(domain, new_mem)
                    self.memory.save(domain, merged)
            except Exception:
                pass

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

        Unlike `run_task`, this does **not** close tools — the same BrowserTool
        session and message history stay alive across turns so follow-up
        instructions see the prior page state and conversation context.

        All tool audit rows for every turn share the same ``session_id``.
        """
        messages.append({"role": "user", "content": user_content})

        domain = extract_domain(user_content)
        system = _build_system_prompt(domain)

        await sampling_loop(
            messages=messages,
            tools=self.tools,
            llm=self.llm,
            session_id=session_id,
            audit=self.audit,
            max_turns=self.config.max_turns,
            only_n_most_recent_images=self.config.only_n_most_recent_images,
            system=system,
        )
        return _extract_last_assistant_text(messages)

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


__all__ = ["Orchestrator"]
