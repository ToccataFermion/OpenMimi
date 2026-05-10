"""Tests for the expanded ErrorCode taxonomy + next_step_hint helpers."""
from __future__ import annotations

from openmimi.tools.errors import (
    ErrorCode,
    _HINTS,
    make_error_result,
    next_step_hint,
)


def test_every_error_code_has_a_hint() -> None:
    """No orphan codes — each enum member must have a recovery hint."""
    missing = [c for c in ErrorCode if c not in _HINTS or not _HINTS[c].strip()]
    assert missing == [], f"codes without hints: {missing}"


def test_taxonomy_covers_promised_categories() -> None:
    """The roadmap promised at least these codes; lock them in."""
    required = {
        "TARGET_NOT_FOUND",
        "ELEMENT_NOT_VISIBLE",
        "ELEMENT_DETACHED",
        "NETWORK_ERROR",
        "AUTH_REQUIRED",
        "RATE_LIMITED",
        "PERMISSION_DENIED",
        "UNEXPECTED_DIALOG",
        "SESSION_EXPIRED",
        "SCRIPT_ERROR",
        "TIMEOUT",
        "NAVIGATION_ERROR",
        "TOOL_INTERNAL_ERROR",
        "CAPTCHA_DETECTED",
    }
    actual = {c.value for c in ErrorCode}
    assert required <= actual, f"missing: {required - actual}"
    assert len(actual) >= 18, f"taxonomy too narrow: {len(actual)} codes"


def test_next_step_hint_accepts_enum_and_string() -> None:
    enum_hint = next_step_hint(ErrorCode.TARGET_NOT_FOUND)
    str_hint = next_step_hint("TARGET_NOT_FOUND")
    assert enum_hint
    assert enum_hint == str_hint


def test_next_step_hint_unknown_returns_empty() -> None:
    assert next_step_hint("DEFINITELY_NOT_A_CODE") == ""


def test_make_error_result_carries_code_and_hint() -> None:
    result = make_error_result(
        ErrorCode.RATE_LIMITED, "blocked after 10 rapid clicks"
    )
    assert result.is_error is True
    assert result.details["error_code"] == "RATE_LIMITED"
    assert result.details["next_step_hint"]
    # Hint must surface in the LLM-visible output, not just details
    assert "Next step:" in result.output
    assert "[RATE_LIMITED]" in result.output
    assert "blocked after 10 rapid clicks" in result.output


def test_make_error_result_merges_extra_details() -> None:
    result = make_error_result(
        ErrorCode.TARGET_NOT_FOUND,
        "no match for 'login button'",
        extra_details={"selector": "#login", "page_url": "https://example.com"},
    )
    assert result.details["error_code"] == "TARGET_NOT_FOUND"
    assert result.details["selector"] == "#login"
    assert result.details["page_url"] == "https://example.com"
    # extra_details must not clobber the structured fields
    assert "next_step_hint" in result.details


def test_error_code_string_compat() -> None:
    """ErrorCode is a StrEnum so existing `.value` and direct-string usage holds."""
    assert ErrorCode.TIMEOUT == "TIMEOUT"
    assert ErrorCode.TIMEOUT.value == "TIMEOUT"
    assert str(ErrorCode.TIMEOUT) in ("TIMEOUT", "ErrorCode.TIMEOUT")
