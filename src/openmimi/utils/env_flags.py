"""Feature toggles read from environment variables."""
from __future__ import annotations

import os


def screenshots_disabled() -> bool:
    """When true, tools skip capturing screenshots (faster runs, text-only tool results)."""
    v = os.environ.get("OPENMIMI_DISABLE_SCREENSHOTS", "").strip().lower()
    return v in ("1", "true", "yes", "on")


__all__ = ["screenshots_disabled"]
