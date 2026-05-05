"""Smoke tests for ToolCollection."""
from __future__ import annotations

import pytest

from openmimi.tools.collection import ToolCollection


def test_collection_starts_empty() -> None:
    coll = ToolCollection()
    assert coll.to_params() == []


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    coll = ToolCollection()
    with pytest.raises(KeyError):
        await coll.run("nope", {})
