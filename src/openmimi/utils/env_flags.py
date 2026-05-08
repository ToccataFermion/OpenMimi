"""Feature toggles read from environment variables."""
from __future__ import annotations

import os


def screenshots_disabled() -> bool:
    """When true, tools skip capturing screenshots (faster runs, text-only tool results).

    Screenshots are disabled by default. Set ``OPENMIMI_ENABLE_SCREENSHOTS=1``
    (or pass ``--screenshots``) to opt in.
    """
    v = os.environ.get("OPENMIMI_ENABLE_SCREENSHOTS", "").strip().lower()
    return v not in ("1", "true", "yes", "on")


__all__ = ["screenshots_disabled"]
