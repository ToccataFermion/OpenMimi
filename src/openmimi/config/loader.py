"""Configuration loader: env vars > optional config file > defaults."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import AppConfig


def _load_json_config(path: Path) -> dict | None:
    """Best-effort load a JSON config file. Returns None on any error."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_config() -> AppConfig:
    """Return application config with overlay order:

    1. Hard-coded defaults (AppConfig())
    2. Global config file ``~/.openmimi/config.json``
    3. Local config file ``./.openmimi.json`` (cwd override)
    4. Environment variables (``OPENMIMI_*`` prefix)
    """
    cfg = AppConfig()

    # 2. Global JSON config
    global_path = Path.home() / ".openmimi" / "config.json"
    global_data = _load_json_config(global_path)
    if global_data:
        cfg = AppConfig.model_validate({**cfg.model_dump(mode="json"), **global_data})

    # 3. Local JSON config (cwd override)
    local_path = Path(".openmimi.json")
    local_data = _load_json_config(local_path)
    if local_data:
        cfg = AppConfig.model_validate({**cfg.model_dump(mode="json"), **local_data})

    # 4. Env overrides (OPENMIMI_ENABLE_PLANNING, OPENMIMI_MAX_TURNS, etc.)
    cfg = _apply_env_overrides(cfg)

    return cfg


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """Overlay ``OPENMIMI_*`` environment variables onto the config object.

    Supported mappings (add more as needed):
    - OPENMIMI_ENABLE_PLANNING → bool(enable_planning)
    - OPENMIMI_MAX_TURNS → int(max_turns)
    - OPENMIMI_COMPRESSION_STRATEGY → str(compression_strategy)
    - OPENMIMI_MAX_CONTEXT_TOKENS → int(max_context_tokens)
    """
    data = cfg.model_dump(mode="json")

    if "OPENMIMI_ENABLE_PLANNING" in os.environ:
        data["enable_planning"] = os.environ["OPENMIMI_ENABLE_PLANNING"].lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if "OPENMIMI_MAX_TURNS" in os.environ:
        try:
            data["max_turns"] = int(os.environ["OPENMIMI_MAX_TURNS"])
        except ValueError:
            pass
    if "OPENMIMI_COMPRESSION_STRATEGY" in os.environ:
        val = os.environ["OPENMIMI_COMPRESSION_STRATEGY"]
        if val in ("truncate", "summarize"):
            data["compression_strategy"] = val
    if "OPENMIMI_MAX_CONTEXT_TOKENS" in os.environ:
        try:
            data["max_context_tokens"] = int(os.environ["OPENMIMI_MAX_CONTEXT_TOKENS"])
        except ValueError:
            pass

    return AppConfig.model_validate(data)
