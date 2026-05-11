"""Tests for environment-driven feature flags."""
from __future__ import annotations

import os

import pytest

from openmimi.utils.env_flags import screenshots_disabled


@pytest.fixture
def clear_enable_screenshots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENMIMI_ENABLE_SCREENSHOTS", raising=False)


def test_screenshots_disabled_default(clear_enable_screenshots: None) -> None:
    assert screenshots_disabled() is True


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on"],
)
def test_screenshots_enabled_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENMIMI_ENABLE_SCREENSHOTS", value)
    assert screenshots_disabled() is False


@pytest.mark.parametrize(
    "value",
    ["0", "false", "no", "off", ""],
)
def test_screenshots_disabled_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENMIMI_ENABLE_SCREENSHOTS", value)
    assert screenshots_disabled() is True
