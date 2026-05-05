"""Unit tests for utils.images."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from openmimi.utils.images import png_resize_max_side, png_to_base64


def _make_png(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_png_to_base64_round_trip() -> None:
    png = _make_png(10, 10)
    encoded = png_to_base64(png)
    assert isinstance(encoded, str)
    assert base64.b64decode(encoded) == png


def test_resize_keeps_image_when_already_small() -> None:
    png = _make_png(800, 600)
    out = png_resize_max_side(png, max_side_px=1280)
    assert out == png


def test_resize_scales_to_max_side() -> None:
    png = _make_png(2560, 1440)
    out = png_resize_max_side(png, max_side_px=1280)
    with Image.open(io.BytesIO(out)) as img:
        assert max(img.size) == 1280
        assert img.size[0] / img.size[1] == pytest.approx(2560 / 1440, rel=0.05)


def test_resize_handles_portrait() -> None:
    png = _make_png(1200, 3000)
    out = png_resize_max_side(png, max_side_px=1500)
    with Image.open(io.BytesIO(out)) as img:
        assert max(img.size) == 1500
        assert img.size[1] >= img.size[0]


def test_resize_invalid_max_side() -> None:
    png = _make_png(100, 100)
    with pytest.raises(ValueError):
        png_resize_max_side(png, max_side_px=0)


def test_resize_jpeg_input_is_returned_as_png() -> None:
    img = Image.new("RGB", (3000, 2000), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    out = png_resize_max_side(jpeg_bytes, max_side_px=1024)
    with Image.open(io.BytesIO(out)) as recoded:
        assert recoded.format == "PNG"
        assert max(recoded.size) == 1024
