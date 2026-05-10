"""Unit tests for Orchestrator.run_task (no network, no real browser)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openmimi.audit import JsonlAuditLogger
from openmimi.config.schema import AppConfig
from openmimi.orchestrator import Orchestrator, _build_system_prompt, _format_plan_summary
from openmimi.planning import LLMPlanner, NullVerifier, Plan, PlanStep
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
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        Orchestrator.from_env()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_from_env_uses_base_url_and_model_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
    assert captured["request_timeout_s"] == 90.0
    assert callable(captured["progress_logger"])


def test_from_env_reads_llm_timeout_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENMIMI_LLM_TIMEOUT_S", "12.5")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    captured: dict[str, Any] = {}

    def _fake_anthropic_client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient", _fake_anthropic_client
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"

    Orchestrator.from_env(config=cfg)

    assert captured["request_timeout_s"] == 12.5


@pytest.mark.asyncio
async def test_run_chat_turn_keeps_tools_open_across_turns(
    tmp_path: Path,
) -> None:
    llm = _ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "R1"}],
                "stop_reason": "end_turn",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "R2"}],
                "stop_reason": "end_turn",
            },
        ]
    )
    orch, tool = _make_orch(tmp_path, llm)
    messages: list[dict[str, Any]] = []

    t1 = await orch.run_chat_turn(
        messages=messages, session_id="s-chat", user_content="first"
    )
    t2 = await orch.run_chat_turn(
        messages=messages, session_id="s-chat", user_content="second"
    )

    assert t1 == "R1"
    assert t2 == "R2"
    assert tool.closed is False
    assert len(messages) == 4
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "first"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "second"

    await orch.close()
    assert tool.closed is True


def test_from_env_falls_back_when_timeout_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENMIMI_LLM_TIMEOUT_S", "not-a-number")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    captured: dict[str, Any] = {}

    def _fake_anthropic_client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient", _fake_anthropic_client
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"

    Orchestrator.from_env(config=cfg)

    assert captured["request_timeout_s"] == 90.0


# --- Planner / Verifier integration (#7 stage 3c) ---------------------------


def _format_plan_summary_helper(steps: list[tuple[str, str]]) -> Plan:
    return Plan(
        steps=[PlanStep(step=s, success_criteria=c) for s, c in steps]
    )


def test_format_plan_summary_renders_numbered_list() -> None:
    plan = _format_plan_summary_helper(
        [("open page", "page loaded"), ("search", ">=1 result")]
    )
    summary = _format_plan_summary(plan)
    assert "Planned approach" in summary
    assert "1. open page [success: page loaded]" in summary
    assert "2. search [success: >=1 result]" in summary


def test_format_plan_summary_returns_empty_for_empty_plan() -> None:
    assert _format_plan_summary(Plan()) == ""


def test_build_system_prompt_appends_plan_summary() -> None:
    plan = _format_plan_summary_helper([("a", "ok"), ("b", "ok")])
    prompt = _build_system_prompt(None, None, plan)
    assert "Planned approach" in prompt
    assert "1. a [success: ok]" in prompt


def test_build_system_prompt_skips_empty_plan() -> None:
    prompt_with_empty = _build_system_prompt(None, None, Plan())
    prompt_without = _build_system_prompt(None, None, None)
    assert prompt_with_empty == prompt_without


class _ScriptedPlanner:
    """LLMPlanner-compatible stub that returns a preset Plan or raises."""

    def __init__(self, plan: Plan | Exception | None) -> None:
        self._plan = plan
        self.calls: list[tuple[str, str]] = []

    async def plan_task(self, task: str, system_context: str = "") -> Plan:
        self.calls.append((task, system_context))
        if isinstance(self._plan, Exception):
            raise self._plan
        if self._plan is None:
            return Plan()
        return self._plan


def _make_planning_orch(
    tmp_path: Path,
    llm: Any,
    *,
    planner: Any | None = None,
    verifier: Any | None = None,
    enable_planning: bool = True,
) -> tuple[Orchestrator, _NoopTool]:
    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.max_turns = 5
    cfg.enable_planning = enable_planning

    tools = ToolCollection()
    tool = _NoopTool()
    tools.register(tool)

    audit = JsonlAuditLogger(
        audit_dir=cfg.storage.audit_dir, screen_dir=cfg.storage.screen_dir
    )

    orch = Orchestrator(
        config=cfg,
        llm=llm,
        tools=tools,
        audit=audit,
        planner=planner,
        verifier=verifier,
    )
    return orch, tool


def _capture_sampling_loop(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace orchestrator.sampling_loop with a no-op that records kwargs."""
    captured: dict[str, Any] = {}

    async def _fake_loop(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        messages = kwargs["messages"]
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "stub-final"}],
            }
        )
        return messages

    monkeypatch.setattr("openmimi.orchestrator.sampling_loop", _fake_loop)
    return captured


@pytest.mark.asyncio
async def test_run_task_skips_planner_when_planning_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sampling_loop(monkeypatch)
    plan = _format_plan_summary_helper([("a", "ok")])
    planner = _ScriptedPlanner(plan)
    orch, _ = _make_planning_orch(
        tmp_path,
        llm=object(),
        planner=planner,
        enable_planning=False,
    )
    await orch.run_task("a task")
    assert planner.calls == []
    assert captured["plan"] is None
    assert captured["verifier"] is None
    assert "Planned approach" not in captured["system"]


@pytest.mark.asyncio
async def test_run_task_invokes_planner_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sampling_loop(monkeypatch)
    plan = _format_plan_summary_helper(
        [("open homepage", "page loaded"), ("search", ">=1 result")]
    )
    planner = _ScriptedPlanner(plan)
    orch, _ = _make_planning_orch(
        tmp_path,
        llm=object(),
        planner=planner,
        enable_planning=True,
    )
    await orch.run_task("buy a laptop")
    assert len(planner.calls) == 1
    assert planner.calls[0][0] == "buy a laptop"
    assert captured["plan"] is plan
    assert captured["verifier"] is not None
    assert "Planned approach" in captured["system"]
    assert "open homepage" in captured["system"]


@pytest.mark.asyncio
async def test_run_task_uses_null_verifier_when_only_planner_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sampling_loop(monkeypatch)
    plan = _format_plan_summary_helper([("a", "ok")])
    planner = _ScriptedPlanner(plan)
    orch, _ = _make_planning_orch(
        tmp_path,
        llm=object(),
        planner=planner,
        verifier=None,
        enable_planning=True,
    )
    await orch.run_task("x")
    assert isinstance(captured["verifier"], NullVerifier)


@pytest.mark.asyncio
async def test_run_task_swallows_planner_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sampling_loop(monkeypatch)
    planner = _ScriptedPlanner(RuntimeError("planner is down"))
    orch, _ = _make_planning_orch(
        tmp_path,
        llm=object(),
        planner=planner,
        enable_planning=True,
    )
    result = await orch.run_task("x")
    assert result["final_text"] == "stub-final"
    assert captured["plan"] is None
    assert captured["verifier"] is None


@pytest.mark.asyncio
async def test_run_chat_turn_invokes_planner_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sampling_loop(monkeypatch)
    plan = _format_plan_summary_helper([("a", "ok")])
    planner = _ScriptedPlanner(plan)
    orch, _ = _make_planning_orch(
        tmp_path,
        llm=object(),
        planner=planner,
        enable_planning=True,
    )
    messages: list[dict[str, Any]] = []
    await orch.run_chat_turn(
        messages=messages, session_id="s-x", user_content="first thing"
    )
    await orch.run_chat_turn(
        messages=messages, session_id="s-x", user_content="second thing"
    )
    assert [c[0] for c in planner.calls] == ["first thing", "second thing"]
    assert captured["plan"] is plan


def test_from_env_constructs_planner_and_verifier_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient",
        lambda **kw: object(),
    )

    # Avoid spinning a real browser daemon in this unit test.
    monkeypatch.setattr(
        "openmimi.orchestrator.AgentBrowserTool",
        lambda **kw: object(),
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"
    cfg.enable_planning = True

    orch = Orchestrator.from_env(config=cfg)
    assert isinstance(orch.planner, LLMPlanner)
    assert orch.verifier is not None  # LLMVerifier


def test_from_env_leaves_planner_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient",
        lambda **kw: object(),
    )
    monkeypatch.setattr(
        "openmimi.orchestrator.AgentBrowserTool",
        lambda **kw: object(),
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"
    cfg.enable_planning = False

    orch = Orchestrator.from_env(config=cfg)
    assert orch.planner is None
    assert orch.verifier is None


# --- Compression strategy + token budget wiring (#5 stage 4) ----------------


@pytest.mark.asyncio
async def test_run_task_passes_compression_kwargs_to_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three #5 kwargs must flow from AppConfig through to sampling_loop."""
    captured = _capture_sampling_loop(monkeypatch)
    sentinel_llm = object()
    orch, _ = _make_orch(tmp_path, llm=sentinel_llm)
    orch.config.compression_strategy = "summarize"
    orch.config.max_context_tokens = 12345
    orch._compress_llm = sentinel_llm

    await orch.run_task("hi")

    assert captured["compression_strategy"] == "summarize"
    assert captured["max_context_tokens"] == 12345
    assert captured["compress_llm"] is sentinel_llm


@pytest.mark.asyncio
async def test_run_chat_turn_passes_compression_kwargs_to_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_chat_turn` must thread the same #5 kwargs as `run_task`."""
    captured = _capture_sampling_loop(monkeypatch)
    sentinel_llm = object()
    orch, _ = _make_orch(tmp_path, llm=sentinel_llm)
    orch.config.compression_strategy = "summarize"
    orch.config.max_context_tokens = 9999
    orch._compress_llm = sentinel_llm

    messages: list[dict[str, Any]] = []
    await orch.run_chat_turn(
        messages=messages, session_id="s-x", user_content="anything"
    )

    assert captured["compression_strategy"] == "summarize"
    assert captured["max_context_tokens"] == 9999
    assert captured["compress_llm"] is sentinel_llm


@pytest.mark.asyncio
async def test_run_task_truncate_strategy_passes_none_compress_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default `"truncate"` must keep compress_llm at None even with an LLM injected."""
    captured = _capture_sampling_loop(monkeypatch)
    orch, _ = _make_orch(tmp_path, llm=object())
    # Default config: compression_strategy == "truncate", compress_llm not set
    await orch.run_task("hi")
    assert captured["compression_strategy"] == "truncate"
    assert captured["compress_llm"] is None


def test_from_env_summarize_strategy_wires_compress_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`compression_strategy="summarize"` reuses the main LLMClient."""
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    sentinel_llm = object()
    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient",
        lambda **kw: sentinel_llm,
    )
    monkeypatch.setattr(
        "openmimi.orchestrator.AgentBrowserTool",
        lambda **kw: object(),
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"
    cfg.compression_strategy = "summarize"

    orch = Orchestrator.from_env(config=cfg)
    assert orch.llm is sentinel_llm
    assert orch._compress_llm is sentinel_llm  # reused, not a new channel


def test_from_env_truncate_strategy_leaves_compress_llm_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default `"truncate"` strategy must NOT wire compress_llm."""
    monkeypatch.delenv("OPENMIMI_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    monkeypatch.setattr(
        "openmimi.orchestrator.AnthropicClient",
        lambda **kw: object(),
    )
    monkeypatch.setattr(
        "openmimi.orchestrator.AgentBrowserTool",
        lambda **kw: object(),
    )

    cfg = AppConfig()
    cfg.storage.audit_dir = tmp_path / "audit"
    cfg.storage.screen_dir = tmp_path / "screens"
    cfg.browser.download_dir = tmp_path / "dl"
    # cfg.compression_strategy defaults to "truncate"

    orch = Orchestrator.from_env(config=cfg)
    assert orch._compress_llm is None
