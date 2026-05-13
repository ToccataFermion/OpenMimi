"""Unit tests for slider_find_gap_vision helpers."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from openmimi.tools.actions.captcha import (
    _extract_json_from_text,
    _read_image_file,
)


class TestExtractJsonFromText:
    def test_plain_json(self):
        text = '{"gap_x": 123, "confidence": 0.95, "reasoning": "gap near left edge"}'
        result = _extract_json_from_text(text)
        assert result == {"gap_x": 123, "confidence": 0.95, "reasoning": "gap near left edge"}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"gap_x": 200, "confidence": 0.8}\n```'
        result = _extract_json_from_text(text)
        assert result == {"gap_x": 200, "confidence": 0.8}

    def test_no_gap_x_key(self):
        text = '{"confidence": 0.8}'
        result = _extract_json_from_text(text)
        assert result is None

    def test_regex_fallback(self):
        text = 'The gap is at position "gap_x": 150 in the image.'
        result = _extract_json_from_text(text)
        assert result == {"gap_x": 150, "confidence": 0.5, "reasoning": "extracted via regex fallback"}

    def test_invalid_json_no_regex(self):
        text = 'I think the gap is somewhere around here.'
        result = _extract_json_from_text(text)
        assert result is None


class TestReadImageFile:
    def test_png(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
            path = f.name
        try:
            data, media_type = _read_image_file(path)
            assert data == b"\x89PNG\r\n\x1a\nfake"
            assert media_type == "image/png"
        finally:
            os.unlink(path)

    def test_jpg(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xfffake")
            path = f.name
        try:
            data, media_type = _read_image_file(path)
            assert data == b"\xff\xd8\xfffake"
            assert media_type == "image/jpeg"
        finally:
            os.unlink(path)
