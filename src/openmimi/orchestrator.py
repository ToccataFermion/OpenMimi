"""Top-level orchestrator: load config, init resources, run sampling loop.

Wires `AnthropicClient`, `BrowserTool`, `JsonlAuditLogger`, and the sampling
loop into a single entry point that the CLI can call. Tests inject the
parts directly via the constructor; production code uses `Orchestrator.from_env`
which reads config + env var for the API key and instantiates real
implementations.
"""
from __future__ import annotations

import os
from typing import Any

from .audit import JsonlAuditLogger
from .config import load_config
from .config.schema import AppConfig
from .llm import AnthropicClient
from .llm.base import LLMClient
from .loop import sampling_loop
from .tools import BrowserTool, ToolCollection
from .utils.ids import new_session_id


class Orchestrator:
    """Wires LLM client, tool collection, memory, and audit together."""

    def __init__(
        self,
        *,
        config: AppConfig,
        llm: LLMClient,
        tools: ToolCollection,
        audit: JsonlAuditLogger | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = tools
        self.audit = audit

    @classmethod
    def from_env(
        cls, *, config: AppConfig | None = None
    ) -> Orchestrator:
        """Build an Orchestrator from `AppConfig` + env vars.

        Recognised env vars:
          - <cfg.llm.api_key_env>   (default ANTHROPIC_API_KEY) - required
          - ANTHROPIC_BASE_URL      - optional, points at a compatible proxy
          - ANTHROPIC_MODEL         - optional, overrides cfg.llm.model

        When ANTHROPIC_BASE_URL is set we conservatively disable prompt
        caching, because most Anthropic-compatible third-party endpoints
        (OpenAI-compat gateways, Aliyun MaaS, etc.) don't recognise the
        `cache_control` field and may reject the request.
        """
        cfg = config or load_config()
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
        )

        tools = ToolCollection()
        tools.register(
            BrowserTool(
                download_dir=str(cfg.browser.download_dir),
                viewport=(cfg.browser.viewport_width, cfg.browser.viewport_height),
            )
        )

        audit = JsonlAuditLogger(
            audit_dir=cfg.storage.audit_dir,
            screen_dir=cfg.storage.screen_dir,
        )

        return cls(config=cfg, llm=llm, tools=tools, audit=audit)

    async def run_task(self, task: str) -> dict[str, Any]:
        """Execute one user task end-to-end. Returns session id, messages, and final text."""
        session_id = new_session_id()
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

        try:
            await sampling_loop(
                messages=messages,
                tools=self.tools,
                llm=self.llm,
                session_id=session_id,
                audit=self.audit,
                max_turns=self.config.max_turns,
                only_n_most_recent_images=self.config.only_n_most_recent_images,
            )
        finally:
            await self.tools.close_all()

        return {
            "session_id": session_id,
            "messages": messages,
            "final_text": _extract_last_assistant_text(messages),
        }

    async def close(self) -> None:
        await self.tools.close_all()


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
