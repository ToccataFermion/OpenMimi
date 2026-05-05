"""Configuration loader: env vars > optional config file > defaults."""
from __future__ import annotations

from .schema import AppConfig


def load_config() -> AppConfig:
    """Return application config. M1: defaults only, env-based overrides come later."""
    return AppConfig()
