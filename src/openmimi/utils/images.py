"""Screenshot helpers: resize and base64 encode."""
from __future__ import annotations

import base64
import io

from PIL import Image


def png_resize_max_side(png_bytes: bytes, max_side_px: int = 1280) -> bytes:
    """Resize a PNG so its longest side is at most `max_side_px`, preserving aspect.

    Returns the original bytes unchanged if no resize is needed. The returned
    bytes are always in PNG format regardless of the input format.
    """
    if max_side_px <= 0:
        raise ValueError("max_side_px must be positive")

    with Image.open(io.BytesIO(png_bytes)) as img:
        width, height = img.size
        longest = max(width, height)
        if longest <= max_side_px and img.format == "PNG":
            return png_bytes

        scale = max_side_px / longest if longest > max_side_px else 1.0
        new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))

        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")

        if new_size != (width, height):
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()


def png_to_base64(png_bytes: bytes) -> str:
    """Encode raw PNG bytes as a base64 ASCII string (no data: URL prefix)."""
    return base64.b64encode(png_bytes).decode("ascii")


__all__ = ["png_resize_max_side", "png_to_base64"]
