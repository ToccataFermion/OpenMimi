"""Top-level orchestrator: load config, init resources, run loop."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Orchestrator:
    """Wires LLM client, tool collection, memory, and audit together."""

    def run_task(self, task: str) -> str:
        raise NotImplementedError("M1: implement run_task")

    def close(self) -> None:
        return None
