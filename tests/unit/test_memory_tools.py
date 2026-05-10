"""Tests for the LLM-facing memory tools (roadmap #9 stage 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmimi.tools.memory import (
    MemoryGrepTool,
    MemoryListTool,
    MemoryReadTool,
    MemoryWriteTool,
    _resolve_scope_path,
    _split_scope_and_rel,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _seed_memory_root(root: Path) -> None:
    """Populate a memory root with a tiny but representative tree."""
    sites = root / "sites"
    skills = root / "skills"
    episodic = root / "episodic" / "2026-05"
    sites.mkdir(parents=True, exist_ok=True)
    skills.mkdir(parents=True, exist_ok=True)
    episodic.mkdir(parents=True, exist_ok=True)
    (sites / "github.com.md").write_text(
        "## github.com\n- login is at /login\n- 2FA enabled\n",
        encoding="utf-8",
    )
    (sites / "example.com.md").write_text(
        "## example.com\n- read-only demo site\n",
        encoding="utf-8",
    )
    (skills / "login_form.md").write_text(
        "# login form\nclick username; type pw; click submit\n",
        encoding="utf-8",
    )
    (episodic / "sess-1.jsonl").write_text(
        json.dumps({
            "ts": "2026-05-11T12:00:00",
            "session_id": "sess-1",
            "step": 0,
            "tool": "browser_navigate",
            "url": "https://github.com/login",
            "domain": "github.com",
            "result_summary": "navigated to login",
        })
        + "\n",
        encoding="utf-8",
    )
    (episodic / "sess-2.jsonl").write_text(
        json.dumps({
            "ts": "2026-05-12T08:00:00",
            "session_id": "sess-2",
            "step": 0,
            "tool": "browser_navigate",
            "url": "https://example.com",
            "domain": "example.com",
            "result_summary": "loaded example",
        })
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def mem_root(tmp_path: Path) -> Path:
    _seed_memory_root(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# _split_scope_and_rel + _resolve_scope_path (path safety)
# ---------------------------------------------------------------------------


def test_split_scope_and_rel_basic() -> None:
    assert _split_scope_and_rel("sites/github.com.md") == ("sites", "github.com.md")
    assert _split_scope_and_rel("skills/login.md") == ("skills", "login.md")
    assert _split_scope_and_rel("episodic/2026-05/x.jsonl") == (
        "episodic",
        "2026-05/x.jsonl",
    )


def test_split_scope_and_rel_normalises_backslash() -> None:
    """Windows paths with backslashes still split correctly."""
    assert _split_scope_and_rel("sites\\github.com.md") == ("sites", "github.com.md")


def test_split_scope_and_rel_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="must start with"):
        _split_scope_and_rel("secrets/passwords.txt")
    with pytest.raises(ValueError, match="must start with"):
        _split_scope_and_rel("")


def test_resolve_scope_path_blocks_traversal(tmp_path: Path) -> None:
    _seed_memory_root(tmp_path)
    with pytest.raises(ValueError, match="escapes scope dir"):
        _resolve_scope_path(tmp_path, "sites", "../episodic/leak.txt")
    with pytest.raises(ValueError, match="escapes scope dir"):
        _resolve_scope_path(tmp_path, "sites", "../../etc/passwd")


def test_resolve_scope_path_unknown_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        _resolve_scope_path(tmp_path, "secrets", "x")


# ---------------------------------------------------------------------------
# MemoryGrepTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_substring_across_all_scopes(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "github.com"})
    assert not result.is_error
    assert "github.com" in result.output
    # Should hit at least the sites/github.com.md AND the episodic record
    matches = result.structured["matches"]
    rels = [m.split(":", 1)[0] for m in matches]
    assert any(r.startswith("sites/") for r in rels)
    assert any(r.startswith("episodic/") for r in rels)


@pytest.mark.asyncio
async def test_grep_scope_filter_limits_search(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "github.com", "scope": "sites"})
    matches = result.structured["matches"]
    assert all(m.startswith("sites/") for m in matches)


@pytest.mark.asyncio
async def test_grep_regex_mode(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": r"^- 2FA", "regex": True})
    assert "2FA" in result.output
    assert result.structured["matches"]


@pytest.mark.asyncio
async def test_grep_ignore_case(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result_caseful = await tool({"pattern": "GITHUB", "scope": "sites"})
    assert not result_caseful.structured["matches"]
    result_caseless = await tool(
        {"pattern": "GITHUB", "scope": "sites", "ignore_case": True}
    )
    assert result_caseless.structured["matches"]


@pytest.mark.asyncio
async def test_grep_glob_filter(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool(
        {"pattern": "github", "scope": "sites", "glob": "github.*"}
    )
    matches = result.structured["matches"]
    assert all("github.com" in m for m in matches)


@pytest.mark.asyncio
async def test_grep_no_matches(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "nonexistent-xyz-789"})
    assert "No matches" in result.output
    assert result.structured["matches"] == []


@pytest.mark.asyncio
async def test_grep_max_matches_truncation(mem_root: Path) -> None:
    # Drop a file with many matching lines
    big = mem_root / "skills" / "many.md"
    big.write_text("hit\n" * 100, encoding="utf-8")
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "hit", "scope": "skills", "max_matches": 5})
    assert result.structured["truncated"] is True
    assert len(result.structured["matches"]) == 5


@pytest.mark.asyncio
async def test_grep_empty_pattern_is_invalid(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": ""})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_grep_unknown_scope(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "x", "scope": "secrets"})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_grep_bad_regex(mem_root: Path) -> None:
    tool = MemoryGrepTool(memory_root=mem_root)
    result = await tool({"pattern": "(", "regex": True})
    assert result.is_error
    assert "bad regex" in result.output.lower()


# ---------------------------------------------------------------------------
# MemoryReadTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_returns_content(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": "sites/github.com.md"})
    assert not result.is_error
    assert "2FA" in result.output
    assert result.structured["path"] == "sites/github.com.md"
    assert result.structured["total_lines"] >= 1


@pytest.mark.asyncio
async def test_read_offset_and_limit(mem_root: Path) -> None:
    big = mem_root / "skills" / "long.md"
    big.write_text("\n".join(f"line-{i}" for i in range(50)) + "\n", encoding="utf-8")
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool(
        {"path": "skills/long.md", "offset": 10, "limit": 3}
    )
    assert "line-9" in result.output  # offset=10 is line 10 (1-based) → line-9 (0-indexed content)
    assert "line-11" in result.output
    assert "line-12" not in result.output
    assert "[truncated]" in result.output


@pytest.mark.asyncio
async def test_read_missing_file(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": "sites/missing.md"})
    assert result.is_error
    assert result.details.get("error_code") == "TARGET_NOT_FOUND"


@pytest.mark.asyncio
async def test_read_blocks_traversal(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": "sites/../../etc/passwd"})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_read_unknown_scope_rejected(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": "secrets/keys.txt"})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_read_directory_rejected(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": "sites"})
    assert result.is_error


@pytest.mark.asyncio
async def test_read_empty_path(mem_root: Path) -> None:
    tool = MemoryReadTool(memory_root=mem_root)
    result = await tool({"path": ""})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# MemoryWriteTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_creates_file(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool(
        {"path": "skills/new_skill.md", "content": "# new skill\nsteps...\n"}
    )
    assert not result.is_error
    target = mem_root / "skills" / "new_skill.md"
    assert target.read_text(encoding="utf-8") == "# new skill\nsteps...\n"


@pytest.mark.asyncio
async def test_write_creates_nested_dirs(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool(
        {"path": "sites/sub/nested.md", "content": "data"}
    )
    assert not result.is_error
    assert (mem_root / "sites" / "sub" / "nested.md").is_file()


@pytest.mark.asyncio
async def test_write_append_mode(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    target_rel = "sites/github.com.md"
    await tool({"path": target_rel, "content": "extra line\n", "mode": "append"})
    text = (mem_root / "sites" / "github.com.md").read_text(encoding="utf-8")
    assert text.endswith("extra line\n")
    assert "2FA" in text  # original content preserved


@pytest.mark.asyncio
async def test_write_episodic_rejected(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool(
        {"path": "episodic/2026-05/forged.jsonl", "content": "{}"}
    )
    assert result.is_error
    assert result.details.get("error_code") == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_write_blocks_traversal(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool(
        {"path": "sites/../../escape.txt", "content": "x"}
    )
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_write_requires_filename(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool({"path": "sites", "content": "x"})
    assert result.is_error
    assert "filename" in result.output.lower()


@pytest.mark.asyncio
async def test_write_invalid_mode(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool(
        {"path": "skills/x.md", "content": "y", "mode": "delete"}
    )
    assert result.is_error
    assert "mode must be" in result.output


@pytest.mark.asyncio
async def test_write_oversize_rejected(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    huge = "a" * 600_000
    result = await tool({"path": "skills/big.md", "content": huge})
    assert result.is_error
    assert "exceeds" in result.output


@pytest.mark.asyncio
async def test_write_missing_content(mem_root: Path) -> None:
    tool = MemoryWriteTool(memory_root=mem_root)
    result = await tool({"path": "skills/x.md"})
    assert result.is_error
    assert "content" in result.output


# ---------------------------------------------------------------------------
# MemoryListTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sites(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "sites"})
    files = result.structured["files"]
    assert "sites/github.com.md" in files
    assert "sites/example.com.md" in files


@pytest.mark.asyncio
async def test_list_skills(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "skills"})
    assert result.structured["files"] == ["skills/login_form.md"]


@pytest.mark.asyncio
async def test_list_episodic_all(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "episodic"})
    files = result.structured["files"]
    assert "episodic/2026-05/sess-1.jsonl" in files
    assert "episodic/2026-05/sess-2.jsonl" in files


@pytest.mark.asyncio
async def test_list_episodic_month_filter(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    other = mem_root / "episodic" / "2026-06"
    other.mkdir()
    (other / "sess-3.jsonl").write_text(
        json.dumps({"step": 0, "domain": "x.com"}) + "\n",
        encoding="utf-8",
    )
    result = await tool({"scope": "episodic", "month": "2026-05"})
    files = result.structured["files"]
    assert all("2026-05" in f for f in files)


@pytest.mark.asyncio
async def test_list_episodic_domain_filter(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "episodic", "domain": "github.com"})
    files = result.structured["files"]
    assert files == ["episodic/2026-05/sess-1.jsonl"]


@pytest.mark.asyncio
async def test_list_unknown_scope(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "secrets"})
    assert result.is_error
    assert result.details.get("error_code") == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_list_filters_only_for_episodic(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "sites", "month": "2026-05"})
    assert result.is_error
    assert "only apply to episodic" in result.output


@pytest.mark.asyncio
async def test_list_glob_filter(mem_root: Path) -> None:
    tool = MemoryListTool(memory_root=mem_root)
    result = await tool({"scope": "sites", "glob": "github.*"})
    files = result.structured["files"]
    assert files == ["sites/github.com.md"]


@pytest.mark.asyncio
async def test_list_empty_dir(tmp_path: Path) -> None:
    tool = MemoryListTool(memory_root=tmp_path)
    result = await tool({"scope": "skills"})
    # Tool should not crash if the scope dir doesn't exist yet
    assert not result.is_error
    assert result.structured["files"] == []


# ---------------------------------------------------------------------------
# Tool schema sanity (ensures Anthropic-style param dicts are well-formed)
# ---------------------------------------------------------------------------


def test_all_tool_schemas_well_formed(tmp_path: Path) -> None:
    tools = [
        MemoryGrepTool(memory_root=tmp_path),
        MemoryReadTool(memory_root=tmp_path),
        MemoryWriteTool(memory_root=tmp_path),
        MemoryListTool(memory_root=tmp_path),
    ]
    for t in tools:
        params = t.to_params()
        assert params["name"] == t.name
        assert params["description"]
        assert params["input_schema"]["type"] == "object"
        assert "properties" in params["input_schema"]
        assert "required" in params["input_schema"]


def test_tool_names_are_unique(tmp_path: Path) -> None:
    tools = [
        MemoryGrepTool(memory_root=tmp_path),
        MemoryReadTool(memory_root=tmp_path),
        MemoryWriteTool(memory_root=tmp_path),
        MemoryListTool(memory_root=tmp_path),
    ]
    names = {t.name for t in tools}
    assert names == {"memory_grep", "memory_read", "memory_write", "memory_list"}
