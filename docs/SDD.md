# OpenMimi - Software Design Document (Draft v0.1)

> Local Windows AI Agent that operates browsers and desktop applications via vision-based tool use, in the Anthropic `tool_use` / `tool_result` style.

## 1. Goals & Non-goals

- Run on Windows (Windows 10 / 11). macOS is out of scope.
- Operate any website inside a browser (built on `browser-use`, pip / uv dependency).
- Operate most desktop applications using a vision-only approach (screenshot + input injection); no UIA / accessibility tree.
- Use the Anthropic `tool_use` / `tool_result` protocol as the single orchestration shape.
- Mixed locator for browser interactions: `target_text` / `target_hint` (semantic) plus `coordinate` (pixel) - mutually exclusive.
- Sandbox is a low-priority milestone (M5).

## 2. Architecture

```
User -> Orchestrator -> Sampling Loop <-> LLM
                            |
                            +-> Tools: browser, computer (M2), files, memory
```

- Orchestrator: load config, init resources, run sampling loop.
- Sampling Loop: LLM -> tool_use -> tools.run -> tool_result -> repeat.
- Tools: each tool returns a unified `ToolResult` and (for UI tools) a fresh screenshot.
- Memory layer (M1): minimal SQLite for sessions / steps / audit.
- Audit: append-only JSONL plus screenshots on disk for replay.

## 3. Milestones

- M1 - Browser-Only Agent (in progress)
  - Sampling loop, BrowserTool wrapping `browser-use`, SQLite store, JSONL audit, CLI.
  - Mixed locator browser actions: navigate / click / type / press / scroll / wait / screenshot / extract / download.
  - Minimal error codes: TARGET_NOT_FOUND / TIMEOUT / NAVIGATION_ERROR / TOOL_INTERNAL_ERROR.
- M2 - Computer-Use (vision-only, Windows native)
  - Screen capture (mss), input injection (SendInput), DPI / multi-monitor handling.
  - Action set mirroring Anthropic's computer tool: mouse / keyboard / observation.
- M3 - Long-term memory
  - Vector store + indexing (LlamaIndex or equivalent).
  - Memory exposed as a tool for retrieval-augmented decisions.
- M4 - Stability & UX
  - Failure fallbacks (text -> hint -> coordinate), human-in-the-loop hand-off, dashboards.
- M5 - Sandbox
  - WSL2 / Windows Sandbox for untrusted code execution; isolated browser for untrusted pages and downloads.

## 4. Browser tool input contract (preview, finalized in next task)

- `action`: navigate / click / type / press / scroll / wait / screenshot / extract / download
- Locator (mutually exclusive): `target_text` | `target_hint` | `coordinate`
- Optional: `expect.url_contains`, `expect.text_contains`, `timeout_s`

## 5. Open questions

- Browser tool granularity: atomic actions vs `run_steps` batch (M1 starts with atomic).
- Whether to expose the memory layer as an LLM tool in M1 (current decision: no).
- Strategy for handling captchas / 2FA - in M1 we hand off to the user.
