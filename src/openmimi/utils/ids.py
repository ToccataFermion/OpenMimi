"""ID generation helpers."""
from __future__ import annotations

from uuid import uuid4


def new_session_id() -> str:
    return uuid4().hex


def next_step_id(prev: int | None) -> int:
    return 1 if prev is None else prev + 1
