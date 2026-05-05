# OpenMimi

> Local Windows AI Agent that operates browsers and desktop applications via vision-based tool use, in the Anthropic `tool_use` / `tool_result` style.

OpenMimi is an Agent that runs on your Windows machine and can:

- Operate any website inside a browser (built on top of `browser-use`)
- Operate most desktop applications via vision + input injection (planned, M2)
- Persist sessions, audit every tool call, and replay actions later

## Status

Early development.

- M1: **Browser-Only Agent** (in progress)
- M2: **Computer-Use** (vision-based, Windows-native)
- M3: long-term memory with vector retrieval
- M4: stability, hand-off and observability
- M5: sandbox for untrusted code / pages / downloads

See `docs/SDD.md` (work in progress) for the full design document.

## Requirements

- Windows 10 or 11
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) for dependency management
- An Anthropic API key (or compatible provider) for the LLM

## Quickstart (M1, work in progress)

```bash
uv sync

copy .env.example .env
# edit .env to set ANTHROPIC_API_KEY=...

uv run openmimi run "Search for the latest Anthropic blog post and summarize it."
```

## Architecture (high level)

OpenMimi follows the Anthropic `tool_use` / `tool_result` loop. The LLM proposes actions; tools execute them locally; every step returns a fresh screenshot back to the model.

```
User -> Orchestrator -> Sampling Loop <-> LLM
                            |
                            +-> Tools: browser, computer (M2), files, memory
```

The browser tool is a thin adapter around the upstream `browser-use` package. We depend on it via pip / uv so that future upgrades stay simple.

## License

Apache-2.0 (see [LICENSE](./LICENSE)).
