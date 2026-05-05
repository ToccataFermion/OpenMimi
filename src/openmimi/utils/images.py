"""Screenshot helpers: resize and base64 encode."""
from __future__ import annotations


def png_resize_max_side(png_bytes: bytes, max_side_px: int = 1280) -> bytes:
    raise NotImplementedError("M1: implement using Pillow")


def png_to_base64(png_bytes: bytes) -> str:
    raise NotImplementedError("M1: implement base64 encode")
