"""Configuration models for OpenMimi."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"


class BrowserConfig(BaseModel):
    download_dir: Path = Path("data/downloads")
    viewport_width: int = 1280
    viewport_height: int = 800
    args: list[str] = Field(default_factory=list)


class StorageConfig(BaseModel):
    sqlite_path: Path = Path("data/openmimi.sqlite")
    audit_dir: Path = Path("data/audit")
    screen_dir: Path = Path("data/screens")


class AppConfig(BaseModel):
    llm: ProviderConfig = Field(default_factory=ProviderConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    max_turns: int = 50
    only_n_most_recent_images: int = 2
    max_context_turns: int = 10  # keep this many recent turns intact; truncate older tool results
