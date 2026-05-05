"""Unit tests for the BrowserTool Pydantic schema.

Covers: action discrimination, locator exclusivity, required locators,
field bounds, and JSON Schema export.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmimi.tools.browser_schema import (
    BrowserToolInput,
    NavigateInput,
    browser_tool_input_json_schema,
    parse_browser_tool_input,
)


def test_navigate_minimal_ok() -> None:
    parsed = parse_browser_tool_input({"action": "navigate", "url": "https://example.com"})
    assert isinstance(parsed, NavigateInput)
    assert parsed.url == "https://example.com"


def test_navigate_missing_url_fails() -> None:
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "navigate"})


def test_click_with_text_ok() -> None:
    parsed = parse_browser_tool_input({"action": "click", "target_text": "Login"})
    assert parsed.action == "click"
    assert parsed.target_text == "Login"
    assert parsed.coordinate is None


def test_click_with_coordinate_ok() -> None:
    parsed = parse_browser_tool_input({"action": "click", "coordinate": [812, 124]})
    assert parsed.action == "click"
    assert parsed.coordinate == (812, 124)


def test_click_locator_exclusive() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_browser_tool_input(
            {"action": "click", "target_text": "Login", "coordinate": [10, 10]}
        )
    assert "mutually exclusive" in str(exc.value)


def test_click_requires_locator() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_browser_tool_input({"action": "click"})
    assert "click requires one of" in str(exc.value)


def test_type_can_omit_locator() -> None:
    parsed = parse_browser_tool_input({"action": "type", "text": "hello"})
    assert parsed.action == "type"
    assert parsed.text == "hello"
    assert parsed.target_text is None
    assert parsed.coordinate is None


def test_press_requires_key() -> None:
    parse_browser_tool_input({"action": "press", "key": "Enter"})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "press"})


def test_scroll_directions() -> None:
    for direction in ("up", "down", "left", "right"):
        parse_browser_tool_input(
            {"action": "scroll", "direction": direction, "amount": 600}
        )
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "scroll", "direction": "diagonal", "amount": 1})


def test_scroll_amount_bounds() -> None:
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "scroll", "direction": "down", "amount": 0})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "scroll", "direction": "down", "amount": 99999})


def test_wait_bounds() -> None:
    parse_browser_tool_input({"action": "wait", "duration_s": 1.0})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "wait", "duration_s": 0})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "wait", "duration_s": 999})


def test_screenshot_no_extra_fields() -> None:
    parse_browser_tool_input({"action": "screenshot"})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "screenshot", "url": "https://x"})


def test_extract_requires_instruction() -> None:
    parse_browser_tool_input({"action": "extract", "instruction": "summarize"})
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "extract"})


def test_download_requires_locator() -> None:
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "download"})
    parse_browser_tool_input({"action": "download", "target_text": "Export"})


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_browser_tool_input({"action": "teleport"})


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        parse_browser_tool_input(
            {"action": "navigate", "url": "https://x", "junk": True}
        )


def test_expect_shape_ok() -> None:
    parsed = parse_browser_tool_input(
        {
            "action": "navigate",
            "url": "https://example.com",
            "expect": {"url_contains": "example.com"},
        }
    )
    assert parsed.expect is not None
    assert parsed.expect.url_contains == "example.com"


def test_json_schema_exports() -> None:
    schema = browser_tool_input_json_schema()
    assert isinstance(schema, dict)
    text = str(schema)
    for action in (
        "navigate",
        "click",
        "type",
        "press",
        "scroll",
        "wait",
        "screenshot",
        "extract",
        "download",
    ):
        assert action in text


def test_type_alias_runtime() -> None:
    """Sanity: the union resolves all action variants at runtime."""
    assert BrowserToolInput is not None
