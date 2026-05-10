"""Error taxonomy for tool results.

Each ErrorCode classifies a failure mode and pairs with a structured
``next_step_hint`` that guides the LLM toward a recovery action. The string
value of the enum is what gets stored in audit logs and
``ToolResult.details["error_code"]``.

Use ``make_error_result`` to build a ToolResult that carries both the code
and the hint — the hint is also prepended to ``output`` so the LLM sees
recovery guidance even without inspecting structured details.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover — pre-3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


if TYPE_CHECKING:
    from .result import ToolResult


class ErrorCode(StrEnum):
    # Element / target
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
    ELEMENT_DETACHED = "ELEMENT_DETACHED"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"

    # Navigation / network
    NAVIGATION_ERROR = "NAVIGATION_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"

    # Auth / access
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"

    # Page state
    UNEXPECTED_DIALOG = "UNEXPECTED_DIALOG"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    BOT_DETECTION = "BOT_DETECTION"

    # Execution
    TIMEOUT = "TIMEOUT"
    SCRIPT_ERROR = "SCRIPT_ERROR"
    INVALID_INPUT = "INVALID_INPUT"

    # System
    BROWSER_CRASHED = "BROWSER_CRASHED"
    TOOL_INTERNAL_ERROR = "TOOL_INTERNAL_ERROR"


_HINTS: dict[ErrorCode, str] = {
    ErrorCode.TARGET_NOT_FOUND: (
        "The selector matched no element. Try a different selector strategy "
        "(text/role/aria-label), call extract to inspect the DOM, or wait_for "
        "the element first if it's loaded async."
    ),
    ErrorCode.ELEMENT_NOT_VISIBLE: (
        "The element exists but is not visible. Scroll it into view, dismiss "
        "any overlay/modal, or wait for visibility before clicking."
    ),
    ErrorCode.ELEMENT_DETACHED: (
        "The element was removed from the DOM after you grabbed it. Re-query "
        "the selector and retry — the page likely re-rendered."
    ),
    ErrorCode.ELEMENT_DISABLED: (
        "The element is present but disabled. Check for prerequisite fields "
        "or wait for the form to validate before interacting."
    ),
    ErrorCode.NAVIGATION_ERROR: (
        "Navigation failed. Verify the URL, check for redirects, or retry "
        "with a longer timeout if the network is slow."
    ),
    ErrorCode.NETWORK_ERROR: (
        "A network request failed. Retry once, check connectivity, or "
        "inspect the network log to see the failing request."
    ),
    ErrorCode.PAGE_LOAD_TIMEOUT: (
        "The page did not finish loading in time. Try wait_for on a specific "
        "element instead of full page load, or increase the timeout."
    ),
    ErrorCode.DOWNLOAD_FAILED: (
        "A download did not complete. Retry, or fetch the file URL directly "
        "with the shell tool if the browser path is fragile."
    ),
    ErrorCode.AUTH_REQUIRED: (
        "The page requires authentication. Navigate to the login page, "
        "complete the login flow, or restore a saved session."
    ),
    ErrorCode.SESSION_EXPIRED: (
        "The previous session expired. Re-authenticate; do not retry the "
        "same action without a fresh login."
    ),
    ErrorCode.PERMISSION_DENIED: (
        "Logged in but lacks permission for this resource. Stop and report "
        "to the user — do not try to escalate privileges."
    ),
    ErrorCode.RATE_LIMITED: (
        "The site is throttling you. Wait 30-60 seconds before retrying, or "
        "abandon the rate-limited action and report to the user."
    ),
    ErrorCode.UNEXPECTED_DIALOG: (
        "A native dialog (alert/confirm/prompt) interrupted the flow. Use "
        "the dialog handler to accept or dismiss it before continuing."
    ),
    ErrorCode.CAPTCHA_DETECTED: (
        "A CAPTCHA blocks progress. Take a screenshot, analyze the challenge "
        "type, then follow the captcha-solving workflow."
    ),
    ErrorCode.BOT_DETECTION: (
        "The site detected automation. Slow down, vary timing, or report "
        "the block to the user — built-in stealth measures are limited."
    ),
    ErrorCode.TIMEOUT: (
        "The operation timed out. Retry with a longer timeout, or break the "
        "task into smaller steps to localise the slow phase."
    ),
    ErrorCode.SCRIPT_ERROR: (
        "JavaScript evaluation failed. Inspect the script, check for syntax "
        "errors, or fall back to a simpler DOM query."
    ),
    ErrorCode.INVALID_INPUT: (
        "The tool input was malformed. Re-read the tool schema and fix the "
        "argument that triggered the validation error."
    ),
    ErrorCode.BROWSER_CRASHED: (
        "The browser process is gone. The daemon will restart on the next "
        "call; retry the operation. Persistent crashes mean a bug — report it."
    ),
    ErrorCode.TOOL_INTERNAL_ERROR: (
        "An unexpected error occurred inside the tool. Check the message; "
        "if it looks transient, retry. Otherwise switch strategy."
    ),
}


def next_step_hint(code: ErrorCode | str) -> str:
    """Return the structured recovery hint for *code*, or empty string.

    Accepts either an ``ErrorCode`` member or its string value, so call
    sites that already store the value in ``details`` keep working.
    """
    if isinstance(code, str) and not isinstance(code, ErrorCode):
        try:
            code = ErrorCode(code)
        except ValueError:
            return ""
    return _HINTS.get(code, "")  # type: ignore[arg-type]


def make_error_result(
    code: ErrorCode,
    message: str,
    *,
    base64_image: str | None = None,
    extra_details: dict | None = None,
) -> "ToolResult":
    """Build a ToolResult that carries error_code + next_step_hint.

    The hint is prepended to ``output`` so the LLM sees recovery guidance
    in the tool_result content even without parsing structured details.
    """
    from .result import ToolResult  # imported lazily to avoid cycles

    hint = _HINTS.get(code, "")
    output = f"[{code.value}] {message}"
    if hint:
        output += f"\n\nNext step: {hint}"

    details: dict = {"error_code": code.value, "next_step_hint": hint}
    if extra_details:
        details.update(extra_details)

    return ToolResult(
        output=output,
        base64_image=base64_image,
        is_error=True,
        details=details,
    )


__all__ = ["ErrorCode", "next_step_hint", "make_error_result"]
