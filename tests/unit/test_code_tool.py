"""Unit tests for CodeTool."""
from __future__ import annotations

import pytest

from openmimi.tools.code import CodeTool


@pytest.fixture
def code_tool() -> CodeTool:
    return CodeTool()


@pytest.mark.asyncio
async def test_code_simple_math(code_tool: CodeTool) -> None:
    result = await code_tool({"code": "x = 2 + 3\nprint(x)"})
    assert result.is_error is False
    assert "5" in result.output


@pytest.mark.asyncio
async def test_code_returns_last_expression(code_tool: CodeTool) -> None:
    result = await code_tool({"code": "{'a': 1, 'b': 2}"})
    assert result.is_error is False
    assert "a" in result.output


@pytest.mark.asyncio
async def test_code_empty_code(code_tool: CodeTool) -> None:
    result = await code_tool({"code": ""})
    assert result.is_error is True
    assert "No code" in result.output


@pytest.mark.asyncio
async def test_code_blocked_pattern(code_tool: CodeTool) -> None:
    result = await code_tool({"code": "import os; os.system('ls')"})
    assert result.is_error is True
    assert "Blocked" in result.output


@pytest.mark.asyncio
async def test_code_syntax_error(code_tool: CodeTool) -> None:
    result = await code_tool({"code": "if True print('bad')"})
    assert result.is_error is True
    assert "Syntax error" in result.output


@pytest.mark.asyncio
async def test_code_import_numpy_if_available(code_tool: CodeTool) -> None:
    result = await code_tool({"code": "import numpy as np\nprint(np.array([1,2,3]).sum())"})
    # numpy may or may not be installed in the test environment
    if result.is_error:
        assert "ModuleNotFoundError" in result.output or "ImportError" in result.output
    else:
        assert "6" in result.output
