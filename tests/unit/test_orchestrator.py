"""Unit tests for Orchestrator.run_task (no network, no real browser)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openmimi.audit import JsonlAuditLogger
from openmimi.config.schema import AppConfig
from openmimi.orchestrator import Orchestrator
from openmimi.tools.base import ToolBase
from openmimi.tools.collection import ToolCollection
from openmimi.tools.result import ToolResult


class _ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _NoopTool(ToolBase):
    name = "browser"

    def __init__(self) -> None:
        self.closed = False

    def to_params(self) -> dict[str, Any]:
        return {"name": self.name, "description": "noop", "input_schema": {}}

    async def __call__(self, tool_input: dict[str, Any]) -> ToolResult:
        return ToolResult(output="ok")

    async def close(self) -> None:
        self.closed = True


def _make_orch(
    tmp_path: Path, llm: Any, tool: ToolBase | None = None
) -> tuple[Orchestrator, _NoopTool]:
    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.max_turns = 5
    cfg.only_n_most_recent_images = 2

    tools = ToolCollection()
    tool = tool or _NoopTool()
    tools.register(tool)

    audit = JsonlAuditLogger(
        audit_dir=cfg.storage.audit_dir, screen_dir=cfg.storage.screen_dir
    )

    return Orchestrator(config=cfg, llm=llm, tools=tools, audit=audit), tool


@pytest.mark.asyncio
async def test_run_task_returns_session_and_final_text(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}],
                "stop_reason": "end_turn",
            }
        ]
    )
    orch, tool = _make_orch(tmp_path, llm)

    result = await orch.run_task("say hi")
    assert isinstance(result["session_id"], str)
    assert len(result["session_id"]) == 32  # uuid4 hex
    assert result["final_text"] == "Hello!"
    assert tool.closed is True


@pytest.mark.asyncio
async def test_run_task_with_tool_call_writes_audit_jsonl(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "browser",
                        "input": {"action": "screenshot"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "stop_reason": "end_turn",
            },
        ]
    )
    orch, _ = _make_orch(tmp_path, llm)
    result = await orch.run_task("look")

    audit_file = tmp_path / "audit" / f"{result['session_id']}.jsonl"
    assert audit_file.is_file()
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "browser" in lines[0]
    assert result["final_text"] == "Done."


@pytest.mark.asyncio
async def test_close_calls_close_all(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
            }
        ]
    )
    orch, tool = _make_orch(tmp_path, llm)
    await orch.run_task("x")
    assert tool.closed is True
    tool.closed = False
    await orch.close()
    assert tool.closed is True


def test_from_env_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        Orchestrator.from_env()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_from_env_uses_base_url_and_model_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com/v1")
    monkeypatch.setenv("ANTHROPIC_MODEL", "qwen-x")

    captured: dict[str, Any] = {}

    def _fake_anthropic_client(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Stub:
            pass

        return _Stub()

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient", _fake_anthropic_client
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"

    Orchestrator.from_env(config=cfg)

    assert captured["api_key"] == "sk-test"
    assert captured["base_url"] == "https://proxy.example.com/v1"
    assert captured["model"] == "qwen-x"
    assert captured["enable_prompt_caching"] is False
