"""CDP key-code mapping shared by interaction-style handlers.

Lifted verbatim from ``agent_browser._cdp_key_code`` so handler modules
in ``actions/`` don't have to import it back from the god-class file.
"""
from __future__ import annotations

_KEY_MAP = {
    "control": "ControlLeft",
    "ctrl": "ControlLeft",
    "shift": "ShiftLeft",
    "alt": "AltLeft",
    "meta": "MetaLeft",
    "enter": "Enter",
    "return": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
}


def cdp_key_code(key: str) -> str:
    """Map a key name to a CDP key code for ``Input.dispatchKeyEvent``."""
    lower = key.lower()
    if lower in _KEY_MAP:
        return _KEY_MAP[lower]
    if len(key) == 1 and key.isalpha():
        return f"Key{key.upper()}"
    if len(key) == 1 and key.isdigit():
        return f"Digit{key}"
    return key


__all__ = ["cdp_key_code"]
