"""Smoke tests that runtime dependencies declared in pyproject.toml are importable.

A regression here means the package's `dependencies` list is out of sync with
what the code actually requires at runtime — users hit ImportError on first use.
"""
from __future__ import annotations


def test_mss_importable() -> None:
    """mss is required by ComputerTool for screenshots / vision / mouse ops."""
    import mss
    import mss.tools

    assert hasattr(mss, "mss")
    assert hasattr(mss.tools, "to_png")
