"""M1 minimal error code enum.

Full taxonomy will be expanded in later milestones (per SDD appendix).
"""
from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class ErrorCode(StrEnum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    NAVIGATION_ERROR = "NAVIGATION_ERROR"
    TOOL_INTERNAL_ERROR = "TOOL_INTERNAL_ERROR"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
