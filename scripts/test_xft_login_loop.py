"""Run a limited-turn xft login test through the full sampling loop."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.orchestrator import Orchestrator


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    task = (
        "Navigate to https://xft.cmbchina.com/, click 登录 to open the login page, "
        "fill in phone 18584828398 and password Liszt123, check the agreement checkbox, "
        "then click the login button. If a slider CAPTCHA appears, analyze the screenshot "
        "and use computer.mouse_drag with precise screen coordinates to solve it."
    )

    orch = Orchestrator.from_env()
    # Give the LLM enough turns to handle login + CAPTCHA + retries
    orch.config.max_turns = 30

    try:
        result = await orch.run_task(task)
        log(f"\n[session {result['session_id']}]")
        final = result.get("final_text") or "(no final text)"
        log(final)
    except Exception as exc:
        log(f"Test ended with exception: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
