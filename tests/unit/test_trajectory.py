"""Tests for openmimi.utils.trajectory — human-like pointer track generation."""
from __future__ import annotations

import math

import pytest

from openmimi.utils.trajectory import generate_trajectory


def test_linear_non_humanize_produces_straight_line() -> None:
    track = generate_trajectory(0, 0, 100, 0, steps=10, delay_ms=10, humanize=False)
    assert len(track) > 0
    assert track[0][0] == 0
    assert track[-1][0] == 100
    # Each step should move monotonically in X (no arc/jitter when humanize=False)
    xs = [x for x, _y, _d in track]
    assert all(xs[i] <= xs[i + 1] for i in range(len(xs) - 1))


def test_humanize_produces_arc() -> None:
    track = generate_trajectory(0, 0, 100, 0, steps=20, delay_ms=10, humanize=True)
    assert track[0][0] == 0
    assert track[-1][0] == 100
    # With a perpendicular control-point offset, at least one Y should be non-zero.
    ys = [y for _x, y, _d in track]
    assert any(y != 0 for y in ys), "humanized track should have some Y deviation"


def test_zero_distance_returns_single_point() -> None:
    track = generate_trajectory(50, 50, 50, 50, steps=20, delay_ms=10, humanize=True)
    assert len(track) == 1
    assert track[0][:2] == (50, 50)


def test_dedupe_removes_consecutive_duplicates() -> None:
    # Non-humanized linear track with many steps should still have no duplicate coords.
    track = generate_trajectory(0, 0, 10, 0, steps=100, delay_ms=10, humanize=False)
    coords = [(x, y) for x, y, _d in track]
    assert len(coords) == len(set(coords)), "consecutive duplicates should be removed"


def test_end_point_always_present() -> None:
    for _ in range(20):
        track = generate_trajectory(0, 0, 123, 456, steps=10, delay_ms=10, humanize=True)
        assert track[-1][0] == 123
        assert track[-1][1] == 456


def test_step_delays_are_positive() -> None:
    track = generate_trajectory(0, 0, 100, 100, steps=10, delay_ms=10, humanize=True)
    for _x, _y, d in track:
        assert d >= 1


def test_delay_variation_with_humanize() -> None:
    track = generate_trajectory(0, 0, 200, 0, steps=50, delay_ms=20, humanize=True)
    delays = [d for _x, _y, d in track]
    # Humanize adds random variation, so not every delay should be exactly 20.
    assert any(d != 20 for d in delays), "delays should vary under humanize"


def test_adaptive_step_count() -> None:
    # Short distance should get fewer effective points than requested.
    track = generate_trajectory(0, 0, 5, 0, steps=100, delay_ms=10, humanize=True)
    assert len(track) < 50, "very short distance should not produce 100+ points"
